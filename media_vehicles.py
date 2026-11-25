"""Media-specific vehicle dictionaries, with an atomic daily refresh."""
import hashlib
import json
import re
import threading
import time
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from domain import Fault, day, later, now, uid
from security import canonical
from yiche import integer

YICHE_URL = 'https://mapi.yiche.com/open_api/api/v1/openapi/clue/getCarList'
MAX_PAGES = 2000


class SyncError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def yiche_page(cid, key, page):
    param = json.dumps({'page': page}, separators=(',', ':'))
    timestamp = str(time.time_ns() // 1_000_000)
    version = '1.0.0'
    signed = f'cid={cid};param={param};ver={version};t={timestamp};{key}'
    headers = {
        'Content-Type': 'application/json',
        'cid': cid, 'ver': version, 't': timestamp,
        'sign': hashlib.md5(signed.encode('utf-8')).hexdigest(),
    }
    request = Request(YICHE_URL, ('{"param":' + param + '}').encode(), headers, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            body = response.read(2_000_001)
    except HTTPError as error:
        raise SyncError(f'易车接口返回 HTTP {error.code}（第 {page} 页）') from None
    except (URLError, TimeoutError, OSError):
        raise SyncError(f'易车接口连接失败或超时（第 {page} 页）') from None
    if len(body) > 2_000_000:
        raise SyncError(f'易车接口响应超出大小限制（第 {page} 页）')
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise SyncError(f'易车接口返回的内容无法解析（第 {page} 页）') from None
    if not isinstance(result, dict):
        raise SyncError(f'易车接口返回格式异常（第 {page} 页）')
    if str(result.get('status')) != '1':
        code = str(result.get('ercd') or result.get('status') or '')
        code = code if re.fullmatch(r'[A-Za-z0-9_-]{1,40}', code) else '未知'
        raise SyncError(f'易车接口拒绝请求，错误码 {code}（第 {page} 页）')
    data = result.get('data')
    # Some successful terminal pages return an explicit null.
    if 'data' in result and data is None:
        return []
    if not isinstance(data, list):
        raise SyncError(f'易车接口缺少车款列表（第 {page} 页）')
    return data


def normalize_yiche(row):
    if not isinstance(row, dict):
        raise ValueError('车型条目必须是对象')
    keys = ('car', 'series', 'factory', 'brand')
    values = []
    for prefix in keys:
        values.append(integer(row.get(prefix + 'Id'), prefix + 'Id'))
        name = row.get(prefix + 'Name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('车型条目缺少名称')
        values.append(name.strip())
    return (*values, canonical(row))


class MediaVehicles:
    def __init__(self, platform):
        self.platform = platform
        self.stop = threading.Event()

    def _partner(self, c, actor, media_id):
        partner = self.platform.one(c, 'partners', media_id, actor)
        if partner['role'] != 'MEDIA':
            raise Fault('VEHICLE_MEDIA_REQUIRED', '请选择媒体')
        return partner

    def overview(self, actor):
        with self.platform.tx() as c:
            rows = c.execute("""
                SELECT p.id, p.name, s.adapter, s.cid, s.enabled, s.start_on,
                  (SELECT COUNT(*) FROM media_vehicle_items i WHERE i.org_id=p.org_id AND i.media_id=p.id) AS total
                FROM partners p LEFT JOIN media_vehicle_sources s ON s.org_id=p.org_id AND s.media_id=p.id
                WHERE p.org_id=? AND p.role='MEDIA' ORDER BY p.id
            """, (actor['org_id'],)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                recent = c.execute("""
                    SELECT sync_day,trigger_kind,started_at,finished_at,state,pages,item_count,error
                    FROM media_vehicle_runs WHERE org_id=? AND media_id=?
                    ORDER BY started_at DESC, rowid DESC LIMIT 10
                """, (actor['org_id'], item['id'])).fetchall()
                item['runs'] = [dict(run) for run in recent]
                latest_success = c.execute("""
                    SELECT finished_at FROM media_vehicle_runs WHERE org_id=? AND media_id=? AND state='SUCCESS'
                    ORDER BY finished_at DESC LIMIT 1
                """, (actor['org_id'], item['id'])).fetchone()
                item['last_success'] = latest_success['finished_at'] if latest_success else None
                result.append(item)
            return result

    def list(self, actor, media_id, query, page):
        with self.platform.tx() as c:
            self._partner(c, actor, media_id)
            search = '%' + query[:120] + '%'
            where = """WHERE org_id=? AND media_id=? AND (
                car_name LIKE ? OR series_name LIKE ? OR factory_name LIKE ? OR brand_name LIKE ? OR CAST(car_id AS TEXT) LIKE ?
            )"""
            params = (actor['org_id'], media_id, search, search, search, search, search)
            total = c.execute('SELECT COUNT(*) FROM media_vehicle_items ' + where, params).fetchone()[0]
            rows = c.execute('SELECT * FROM media_vehicle_items ' + where +
                             ' ORDER BY brand_name,series_name,car_id LIMIT 30 OFFSET ?', (*params, (page-1)*30))
            return {'total': total, 'items': [json.loads(row['payload']) for row in rows]}

    def configure(self, actor, media_id, cid, key, enabled=True):
        self.platform.require(actor, 'settings')
        if not isinstance(cid, str) or not re.fullmatch(r'[0-9]{1,20}', cid):
            raise Fault('VEHICLE_CID_INVALID', 'CID 必须是数字')
        if not isinstance(key, str) or len(key) > 512 or key != key.strip():
            raise Fault('VEHICLE_KEY_INVALID', '请填写有效的签名密钥')
        with self.platform.tx() as c:
            self._partner(c, actor, media_id)
            existing = c.execute('SELECT * FROM media_vehicle_sources WHERE org_id=? AND media_id=?',
                                 (actor['org_id'], media_id)).fetchone()
            if c.execute("SELECT 1 FROM media_dictionary_sources WHERE org_id=? AND media_id=? AND kind='vehicles'", (actor['org_id'], media_id)).fetchone():
                raise Fault('VEHICLE_API_MANAGED', '该媒体已改用字典API管理，请在车型库API配置中修改')
            if not existing and media_id != 'MEDIA_A':
                raise Fault('VEHICLE_ADAPTER_MISSING', '此媒体还未接入车型库接口')
            if not key and not existing:
                raise Fault('VEHICLE_KEY_REQUIRED', '首次配置需要填写签名密钥')
            cipher = self.platform.vault.encrypt(key) if key else existing['key_cipher']
            start_on = ((date.fromisoformat(day()) + timedelta(days=1)).isoformat()
                        if not existing or not existing['enabled'] else existing['start_on'])
            c.execute("""
                INSERT INTO media_vehicle_sources(org_id,media_id,adapter,cid,key_cipher,enabled,start_on)
                VALUES (?,?,'yiche',?,?,?,?)
                ON CONFLICT(org_id,media_id) DO UPDATE SET cid=excluded.cid,key_cipher=excluded.key_cipher,
                  enabled=excluded.enabled,start_on=excluded.start_on
            """, (actor['org_id'], media_id, cid, cipher, int(enabled), start_on))
            self.platform.audit(c, actor, 'MEDIA_VEHICLE_CONFIGURE', media_id, {'cid': cid, 'enabled': enabled})

    def _claim(self, actor, media_id, trigger):
        org = actor['org_id']
        with self.platform.tx() as c:
            self._partner(c, actor, media_id)
            source = c.execute('SELECT * FROM media_vehicle_sources WHERE org_id=? AND media_id=?', (org, media_id)).fetchone()
            if not source or not source['enabled']:
                if trigger == 'auto':
                    return
                raise Fault('VEHICLE_SYNC_DISABLED', '请先配置并启用车型库同步')
            c.execute("""
                UPDATE media_vehicle_runs SET state='FAILED',finished_at=?,error='同步任务中断'
                WHERE org_id=? AND media_id=? AND state='RUNNING' AND started_at<?
            """, (now(), org, media_id, later(hours=-1)))
            if c.execute("""SELECT 1 FROM media_vehicle_runs WHERE org_id=? AND media_id=? AND state='RUNNING'""",
                         (org, media_id)).fetchone():
                if trigger == 'auto':
                    return
                raise Fault('VEHICLE_SYNC_RUNNING', '车型库正在同步', 409)
            today = day()
            if trigger == 'auto' and today < source['start_on']:
                return
            if trigger == 'auto' and c.execute("""
                SELECT 1 FROM media_vehicle_runs WHERE org_id=? AND media_id=? AND sync_day=?
                  AND (state='SUCCESS' OR started_at>=?)
            """, (org, media_id, today, later(hours=-0.25))).fetchone():
                return
            run_id = uid('vc_')
            c.execute("""
                INSERT INTO media_vehicle_runs(id,org_id,media_id,sync_day,trigger_kind,started_at,state)
                VALUES(?,?,?,?,?,?,'RUNNING')
            """, (run_id, org, media_id, today, trigger, now()))
            return run_id, dict(source)

    def _sync(self, run_id, source):
        org, media_id = source['org_id'], source['media_id']
        items = {}
        seen_pages = set()
        pages = 0
        failure = ''
        try:
            key = self.platform.vault.decrypt(source['key_cipher'])
            fetch_page, normalize = ADAPTERS[source['adapter']]
            deadline = time.monotonic() + 2700
            for page in range(1, MAX_PAGES + 1):
                if self.stop.is_set() or time.monotonic() >= deadline:
                    raise SyncError('同步中断或超时')
                rows = fetch_page(source['cid'], key, page)
                if not rows:
                    break
                signature = hashlib.sha256(canonical(rows).encode()).digest()
                if signature in seen_pages:
                    raise SyncError(f'媒体接口重复返回同一页（第 {page} 页）')
                seen_pages.add(signature)
                for row in rows:
                    try:
                        item = normalize(row)
                    except ValueError:
                        raise SyncError(f'车型条目缺少有效编码或名称（第 {page} 页）') from None
                    items[item[0]] = item
                pages = page
            else:
                raise SyncError('达到分页上限，接口仍未返回空页')
            if not items:
                raise SyncError('媒体接口返回空车型库，保留上次同步数据')
            with self.platform.tx() as c:
                current = c.execute("""
                    SELECT s.enabled,s.cid,s.key_cipher,r.state FROM media_vehicle_runs r
                    JOIN media_vehicle_sources s USING(org_id,media_id) WHERE r.id=?
                """, (run_id,)).fetchone()
                if not current or current['state'] != 'RUNNING' or not current['enabled'] or any(
                        current[k] != source[k] for k in ('cid','key_cipher')):
                    raise SyncError('同步期间媒体配置已变更')
                c.execute('DELETE FROM media_vehicle_items WHERE org_id=? AND media_id=?', (org, media_id))
                c.executemany('INSERT INTO media_vehicle_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                              ((org, media_id, *item) for item in items.values()))
                c.execute("""UPDATE media_vehicle_runs SET finished_at=?,state='SUCCESS',pages=?,item_count=? WHERE id=?""",
                          (now(), pages, len(items), run_id))
        except SyncError as error:
            failure = str(error)
        except Exception:
            failure = '车型库同步或入库失败，请检查服务配置'
        if failure:
            with self.platform.tx() as c:
                c.execute("""UPDATE media_vehicle_runs SET finished_at=?,state='FAILED',pages=?,error=?
                             WHERE id=? AND state='RUNNING'""", (now(), pages, failure, run_id))

    def start(self, actor, media_id):
        self.platform.require(actor, 'settings')
        claim = self._claim(actor, media_id, 'manual')
        threading.Thread(target=self._sync, args=claim, daemon=True).start()
        return {'run_id': claim[0]}

    def schedule(self):
        # A persisted Shanghai-date claim allows midnight refresh and catch-up after restarts.
        while not self.stop.is_set():
            try:
                with self.platform.tx() as c:
                    sources = c.execute('SELECT org_id,media_id FROM media_vehicle_sources WHERE enabled=1').fetchall()
                for source in sources:
                    if self.stop.is_set():
                        break
                    claim = self._claim({'org_id': source['org_id']}, source['media_id'], 'auto')
                    if claim:
                        threading.Thread(target=self._sync, args=claim, daemon=True).start()
            except Exception:
                print('车型库调度暂时不可用，稍后重试', flush=True)
            self.stop.wait(15)


ADAPTERS = {'yiche': (yiche_page, normalize_yiche)}
