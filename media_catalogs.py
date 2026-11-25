"""按组织和最终媒体管理API字典，完整拉取后原子替换，不调用线索发送接口。

当前支持已核对的errorCode/message.data格式。网络读取位于写事务外，
失败、分页不完整、配置变更均保留旧库。旧易车签名车型源继续兼容。
"""
import ipaddress
import json
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener

from domain import Fault, day, later, now, uid
from media_vehicles import MediaVehicles, NoRedirect, SyncError, normalize_yiche
from security import canonical, digest

DEFAULT_ENDPOINTS = {
    'vehicles': 'http://yiche.asoapple.cn/api/v1/pullCarList',
    'cities': 'http://yiche.asoapple.cn/api/v1/pullCityList',
}
KINDS = ('vehicles', 'cities')
MAX_PAGES = 2000
MAX_ITEMS = 200000


def validated_endpoint(value: str) -> str:
    """只接受公开HTTP(S)字典地址，page由同步器控制，凭证不放URL。"""
    if not isinstance(value, str) or len(value) > 1000 or any(ord(ch) < 33 for ch in value):
        raise Fault('CATALOG_URL_INVALID', '请填写有效的字典API地址')
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        raise Fault('CATALOG_URL_INVALID', 'API端口无效') from None
    if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
            or parts.fragment or port not in (None, 80, 443) or parts.hostname.lower() == 'localhost'):
        raise Fault('CATALOG_URL_INVALID', '字典API须为公开HTTP(S)地址，不含账号、密码或片段')
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise Fault('CATALOG_URL_INVALID', '不能访问内网或本机地址')
    query = parse_qs(parts.query, keep_blank_values=True)
    if set(query) - {'page'}:
        raise Fault('CATALOG_URL_INVALID', 'URL只接受page参数，鉴权请使用独立Token字段')
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def dictionary_page(endpoint: str, token: str, kind: str, page: int) -> dict:
    """GET用户配置的公开字典，不跟随重定向，不输出URL或外部错误报文。"""
    endpoint = validated_endpoint(endpoint)
    parts = urlsplit(endpoint)
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80),
                                       type=socket.SOCK_STREAM)
    except OSError:
        raise SyncError('字典API域名无法解析') from None
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise SyncError('字典API指向非公开地址，已停止读取')
    headers = {'Accept': 'application/json'}
    if token:
        if parts.scheme != 'https':
            raise SyncError('带Token的字典API必须使用HTTPS')
        headers['Authorization'] = 'Bearer ' + token
    url = endpoint + ('?' + urlencode({'page': page}) if kind == 'vehicles' else '')
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=20) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise SyncError('字典单页超过大小限制')
        result = json.loads(raw)
    except HTTPError as error:
        raise SyncError('字典API返回HTTP ' + str(error.code)) from None
    except (URLError, OSError, TimeoutError):
        raise SyncError('字典API连接失败或超时') from None
    except (ValueError, UnicodeError):
        raise SyncError('字典API未返回合法JSON') from None
    return result


def normalize_city(row: dict) -> tuple:
    """取对方cityCode而非行id，保留省份用于重名城市核对。"""
    if not isinstance(row, dict):
        raise ValueError('城市条目须为对象')
    code = str(row.get('cityCode') or '').strip()
    name = row.get('cityName')
    if not code or len(code) > 64 or not isinstance(name, str) or not name.strip():
        raise ValueError('城市编码或名称缺失')
    return code, name.strip(), str(row.get('parentCode') or ''), str(row.get('parentName') or '')


class MediaCatalogs:
    """平台协调器的字典服务，权限与媒体归属由后端校验，任务不发送线索。"""

    def __init__(self, platform, fetcher=None):
        self.platform = platform
        self.fetcher = fetcher or dictionary_page
        self.stop = threading.Event()

    def _media(self, conn, actor, media_id, kind):
        if kind not in KINDS:
            raise Fault('CATALOG_KIND_INVALID', '请选择车型库或城市库')
        partner = self.platform.one(conn, 'partners', media_id, actor)
        if partner['role'] != 'MEDIA':
            raise Fault('CATALOG_MEDIA_REQUIRED', '字典只能绑定最终媒体，不能绑定乙方或供应商')
        return partner

    def overview(self, actor: dict, kind: str) -> list:
        """返回本组织媒体分类、库存与脱敏配置，Token仅提供是否已保存。"""
        self.platform.require(actor, 'read')
        if kind not in KINDS:
            raise Fault('CATALOG_KIND_INVALID', '请选择车型库或城市库')
        conn = self.platform.connect()
        try:
            result = []
            legacy = {row['id']: row for row in MediaVehicles(self.platform).overview(actor)} if kind == 'vehicles' else {}
            table = 'media_vehicle_items' if kind == 'vehicles' else 'media_city_items'
            for media in conn.execute("SELECT id,name FROM partners WHERE org_id=? AND role='MEDIA' ORDER BY name,id",
                                      (actor['org_id'],)):
                source = conn.execute('SELECT * FROM media_dictionary_sources WHERE org_id=? AND media_id=? AND kind=?',
                                      (actor['org_id'], media['id'], kind)).fetchone()
                item = {'id': media['id'], 'name': media['name'], 'kind': kind, 'configured': bool(source),
                        'endpoint': '', 'enabled': False, 'schedule_hour': 0, 'has_token': False, 'version': 0,
                        'total': conn.execute('SELECT count(*) FROM ' + table + ' WHERE org_id=? AND media_id=?',
                                              (actor['org_id'], media['id'])).fetchone()[0], 'runs': [], 'legacy': False}
                if source:
                    item.update({key: source[key] for key in ('endpoint', 'enabled', 'schedule_hour', 'version')})
                    item['has_token'] = bool(source['token_cipher'])
                    item['runs'] = [dict(row) for row in conn.execute('''SELECT sync_day,trigger_kind,started_at,
                        finished_at,state,pages,item_count,error FROM media_dictionary_runs
                        WHERE org_id=? AND media_id=? AND kind=? ORDER BY started_at DESC,rowid DESC LIMIT 10''',
                        (actor['org_id'], media['id'], kind))]
                elif legacy.get(media['id'], {}).get('adapter'):
                    item.update(legacy=True, configured=True, enabled=bool(legacy[media['id']]['enabled']),
                                runs=legacy[media['id']]['runs'])
                item['last_success'] = next((row['finished_at'] for row in item['runs'] if row['state'] == 'SUCCESS'), None)
                result.append(item)
            return result
        finally:
            conn.close()

    def list(self, actor: dict, media_id: str, kind: str, query='', page=1, page_size=20) -> dict:
        """按媒体独立搜索分页，不从其他媒体字典补空。"""
        self.platform.require(actor, 'read')
        if type(page) is not int or page < 1 or page_size not in (20, 50, 100):
            raise Fault('CATALOG_PAGE_INVALID', '分页参数无效')
        conn = self.platform.connect()
        try:
            self._media(conn, actor, media_id, kind)
            fields = ('car_name', 'series_name', 'factory_name', 'brand_name') if kind == 'vehicles' else ('city_name', 'city_code', 'parent_name')
            table = 'media_vehicle_items' if kind == 'vehicles' else 'media_city_items'
            where = ' WHERE org_id=? AND media_id=? AND (' + ' OR '.join(field + ' LIKE ?' for field in fields) + ')'
            params = (actor['org_id'], media_id, *(['%' + str(query)[:120] + '%'] * len(fields)))
            total = conn.execute('SELECT count(*) FROM ' + table + where, params).fetchone()[0]
            order = 'brand_name,series_name,car_id' if kind == 'vehicles' else 'parent_name,city_name,city_code'
            rows = conn.execute('SELECT * FROM ' + table + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?',
                                (*params, page_size, (page - 1) * page_size))
            items = [json.loads(row['payload']) if kind == 'vehicles' else dict(row) for row in rows]
            return {'items': items, 'total': total, 'page': page, 'page_size': page_size,
                    'has_more': page * page_size < total}
        finally:
            conn.close()

    def entry_options(self, actor: dict, media_id: str) -> dict:
        """返回手动需求可选车系和城市，编号带媒体命名空间，其他媒体不补空。"""
        self.platform.require(actor, 'read')
        conn = self.platform.connect()
        try:
            self._media(conn, actor, media_id, 'vehicles')
            models = [dict(row) for row in conn.execute('''SELECT DISTINCT
                'media:'||media_id||':'||brand_id||':'||series_id AS value,
                brand_name,series_name FROM media_vehicle_items WHERE org_id=? AND media_id=?
                ORDER BY brand_name,series_name''', (actor['org_id'], media_id))]
            cities = [dict(row) for row in conn.execute('''SELECT city_name AS value,city_name,parent_name
                FROM media_city_items WHERE org_id=? AND media_id=? ORDER BY parent_name,city_name''',
                (actor['org_id'], media_id))]
            configured = bool(conn.execute('SELECT 1 FROM media_dictionary_sources WHERE org_id=? AND media_id=?',
                                          (actor['org_id'], media_id)).fetchone())
            return {'models': models, 'cities': cities, 'configured': configured or not self.platform.mock_enabled}
        finally:
            conn.close()

    def configure(self, actor: dict, media_id: str, kind: str, data: dict) -> dict:
        """保存API及每日自动同步开关，Token加密且留空保留，版本冲突拒绝覆盖。"""
        self.platform.require(actor, 'settings')
        endpoint = validated_endpoint(data.get('endpoint', ''))
        enabled, hour = data.get('enabled'), data.get('schedule_hour', 0)
        token = data.get('token', '')
        if (type(enabled) is not bool or type(hour) is not int or not 0 <= hour <= 23
                or not isinstance(token, str) or len(token) > 2000 or any(ord(ch) < 32 for ch in token)
                or type(data.get('clear_token', False)) is not bool):
            raise Fault('CATALOG_CONFIG_INVALID', '请核对启停、时间及Token格式')
        with self.platform.tx() as conn:
            self._media(conn, actor, media_id, kind)
            old = conn.execute('SELECT * FROM media_dictionary_sources WHERE org_id=? AND media_id=? AND kind=?',
                               (actor['org_id'], media_id, kind)).fetchone()
            if type(data.get('version')) is not int or data['version'] != (old['version'] if old else 0):
                raise Fault('VERSION_CONFLICT', 'API配置已变化，请刷新后重试', 409)
            saved_token = old['token_cipher'] if old else ''
            if token:
                saved_token = self.platform.vault.encrypt(token)
            elif data.get('clear_token'):
                saved_token = ''
            if saved_token and not endpoint.startswith('https://'):
                raise Fault('CATALOG_CONFIG_INVALID', '带Token的API必须使用HTTPS')
            conn.execute('''INSERT INTO media_dictionary_sources
                (org_id,media_id,kind,endpoint,token_cipher,enabled,schedule_hour,version) VALUES(?,?,?,?,?,?,?,1)
                ON CONFLICT(org_id,media_id,kind) DO UPDATE SET endpoint=excluded.endpoint,
                token_cipher=excluded.token_cipher,enabled=excluded.enabled,schedule_hour=excluded.schedule_hour,
                version=media_dictionary_sources.version+1''',
                (actor['org_id'], media_id, kind, endpoint, saved_token, int(enabled), hour))
            # 新API成为车型库唯一写入源，防止旧签名任务迟到覆盖同一库存。
            if kind == 'vehicles':
                conn.execute('UPDATE media_vehicle_sources SET enabled=0 WHERE org_id=? AND media_id=?',
                             (actor['org_id'], media_id))
            conn.execute("""UPDATE media_dictionary_runs SET state='FAILED',finished_at=?,error='配置已修改，本轮同步作废'
                WHERE org_id=? AND media_id=? AND kind=? AND state='RUNNING'""",
                (now(), actor['org_id'], media_id, kind))
            self.platform.audit(conn, actor, 'MEDIA_CATALOG_CONFIGURE', media_id,
                                {'kind': kind, 'enabled': enabled, 'schedule_hour': hour})
        return {'saved': True}

    def _claim(self, actor, media_id, kind, trigger):
        with self.platform.tx() as conn:
            self._media(conn, actor, media_id, kind)
            source = conn.execute('SELECT * FROM media_dictionary_sources WHERE org_id=? AND media_id=? AND kind=?',
                                  (actor['org_id'], media_id, kind)).fetchone()
            if not source:
                if trigger == 'auto':
                    return None
                raise Fault('CATALOG_CONFIG_REQUIRED', '请先保存该媒体的字典API地址')
            # enabled只控制每日自动任务，手动读取不应顺带开启每日同步。
            if trigger == 'auto' and not source['enabled']:
                return None
            conn.execute("""UPDATE media_dictionary_runs SET state='FAILED',finished_at=?,error='同步超时或中断'
                WHERE org_id=? AND media_id=? AND kind=? AND state='RUNNING' AND started_at<?""",
                (now(), actor['org_id'], media_id, kind, later(hours=-1)))
            if conn.execute("""SELECT 1 FROM media_dictionary_runs WHERE org_id=? AND media_id=? AND kind=?
                AND state='RUNNING'""", (actor['org_id'], media_id, kind)).fetchone():
                if trigger == 'auto':
                    return None
                raise Fault('CATALOG_SYNC_RUNNING', '该字典正在同步，请稍后刷新', 409)
            from domain import SH, parse
            if trigger == 'auto':
                if parse(now()).astimezone(SH).hour < source['schedule_hour']:
                    return None
                if conn.execute("""SELECT 1 FROM media_dictionary_runs WHERE org_id=? AND media_id=? AND kind=?
                    AND sync_day=? AND (state='SUCCESS' OR started_at>=?)""",
                    (actor['org_id'], media_id, kind, day(), later(hours=-0.25))).fetchone():
                    return None
            run_id = uid('dict_')
            conn.execute('''INSERT INTO media_dictionary_runs
                (id,org_id,media_id,kind,source_version,sync_day,trigger_kind,started_at,state)
                VALUES(?,?,?,?,?,?,?,?,'RUNNING')''',
                (run_id, actor['org_id'], media_id, kind, source['version'], day(), trigger, now()))
            return run_id, dict(source)

    def _sync(self, run_id: str, source: dict):
        items, signatures, pages = {}, set(), 0
        kind, org, media_id = source['kind'], source['org_id'], source['media_id']
        try:
            token = self.platform.vault.decrypt(source['token_cipher']) if source['token_cipher'] else ''
            expected_count, expected_pages = None, None
            deadline = time.monotonic() + 2700
            for page in range(1, MAX_PAGES + 1):
                if self.stop.is_set() or time.monotonic() > deadline:
                    raise SyncError('同步已中断或超时')
                result = self.fetcher(source['endpoint'], token, kind, page)
                if not isinstance(result, dict) or str(result.get('errorCode')) != '1':
                    raise SyncError('字典API返回失败或格式不符')
                message = result.get('message')
                rows = message.get('data') if isinstance(message, dict) else None
                if not isinstance(rows, list):
                    raise SyncError('字典API缺少message.data数组')
                if kind == 'vehicles':
                    try:
                        count, total, returned_page = (int(message[key]) for key in ('count', 'total', 'page'))
                    except (KeyError, TypeError, ValueError):
                        raise SyncError('车型API缺少合法分页元数据count、total、page') from None
                    if (returned_page != page or count < 1 or not 1 <= total <= MAX_PAGES
                            or expected_count not in (None, count) or expected_pages not in (None, total)):
                        raise SyncError('车型分页总量或页码发生变化，保留旧库，请重新同步')
                    expected_count, expected_pages = count, total
                if not rows:
                    raise SyncError('字典页为空，保留旧库')
                signature = digest(canonical(rows))
                if signature in signatures:
                    raise SyncError('字典API重复返回相同页，保留旧库')
                signatures.add(signature)
                for row in rows:
                    try:
                        item = normalize_yiche(row) if kind == 'vehicles' else normalize_city(row)
                    except (ValueError, TypeError):
                        raise SyncError('字典条目缺少合法编码或名称') from None
                    if item[0] in items and items[item[0]] != item:
                        raise SyncError('字典存在冲突的重复编码，保留旧库')
                    items[item[0]] = item
                pages = page
                if len(items) > MAX_ITEMS:
                    raise SyncError('字典超过总条目限制')
                if kind == 'cities' or page == expected_pages:
                    break
            if not items or (kind == 'vehicles' and len(items) != expected_count):
                raise SyncError('字典实际条数与API总量不一致，保留旧库')
            with self.platform.tx() as conn:
                current = conn.execute('''SELECT s.version,s.enabled,r.state,r.trigger_kind FROM media_dictionary_sources s
                    JOIN media_dictionary_runs r ON r.org_id=s.org_id AND r.media_id=s.media_id AND r.kind=s.kind
                    WHERE r.id=? AND s.org_id=?''', (run_id, org)).fetchone()
                if (not current or current['version'] != source['version'] or current['state'] != 'RUNNING'
                        or (current['trigger_kind'] == 'auto' and not current['enabled'])):
                    raise SyncError('配置已变化，本轮同步不写入')
                table = 'media_vehicle_items' if kind == 'vehicles' else 'media_city_items'
                conn.execute('DELETE FROM ' + table + ' WHERE org_id=? AND media_id=?', (org, media_id))
                columns = ('org_id,media_id,car_id,car_name,series_id,series_name,factory_id,factory_name,brand_id,brand_name,payload'
                           if kind == 'vehicles' else 'org_id,media_id,city_code,city_name,parent_code,parent_name')
                conn.executemany('INSERT INTO ' + table + '(' + columns + ') VALUES(' + ','.join('?' for _ in columns.split(',')) + ')',
                                 ((org, media_id, *item) for item in items.values()))
                conn.execute("""UPDATE media_dictionary_runs SET state='SUCCESS',finished_at=?,pages=?,item_count=?
                    WHERE id=? AND org_id=?""", (now(), pages, len(items), run_id, org))
        except Exception as error:
            # 顶层任务必须保活，底层未知异常不回显URL、Token或完整返回报文。
            message = str(error) if isinstance(error, SyncError) else '字典同步失败，请检查API返回格式和服务配置'
            with self.platform.tx() as conn:
                conn.execute("""UPDATE media_dictionary_runs SET state='FAILED',finished_at=?,pages=?,error=?
                    WHERE id=? AND org_id=? AND state='RUNNING'""", (now(), pages, message, run_id, org))

    def start(self, actor: dict, media_id: str, kind: str) -> dict:
        """领取单个手动任务，允许每日自动同步暂停，不开启定时任务或线索发送。"""
        self.platform.require(actor, 'settings')
        claim = self._claim(actor, media_id, kind, 'manual')
        threading.Thread(target=self._sync, args=claim, daemon=True).start()
        return {'run_id': claim[0]}

    def schedule(self):
        """按北京时间每日时刻领取任务，重启后补同步，失败至少间隔15分钟。"""
        while not self.stop.is_set():
            try:
                conn = self.platform.connect()
                try:
                    sources = [dict(row) for row in conn.execute('SELECT org_id,media_id,kind FROM media_dictionary_sources WHERE enabled=1')]
                finally:
                    conn.close()
                for source in sources:
                    if self.stop.is_set():
                        break
                    claim = self._claim({'org_id': source['org_id']}, source['media_id'], source['kind'], 'auto')
                    if claim:
                        threading.Thread(target=self._sync, args=claim, daemon=True).start()
            except Exception:
                print('媒体字典调度暂时不可用，稍后重试', flush=True)
            self.stop.wait(30)
