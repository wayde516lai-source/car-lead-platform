"""KA需求来源配置与全量结果转换，复用DemandSync历史及映射事务。

每天北京时间8点拉取并支持人工触发，不注册甲方通知回调。凭证通过Vault加密且不返回页面。
本模块不发送线索，首次发送后的成功、失败、未知均占用当天提交上限。
"""
from datetime import datetime, timedelta

from domain import Fault, SH, day, now
from ka_demand_api import API_URL, DemandProtocolError


def next_daily_pull():
    """下一次北京时间8点，人工更新不把定时任务推迟24小时。"""
    current = datetime.fromisoformat(now()).astimezone(SH)
    scheduled = current.replace(hour=8, minute=0, second=0, microsecond=0)
    if scheduled <= current:
        scheduled += timedelta(days=1)
    return scheduled.astimezone(datetime.fromisoformat(now()).tzinfo).isoformat(timespec='seconds')


def submission_usage(conn, demand_id, exclude=None):
    """按北京时间提交日计已提交及未发送预占，不因无效回调补回提交次数。"""
    row = conn.execute('''SELECT
        COALESCE(sum(first_sent_at IS NULL AND quota_state='RESERVED' AND day=?),0),
        COALESCE(sum(first_sent_at IS NOT NULL AND
          strftime('%Y-%m-%d',first_sent_at,'+8 hours')=?),0)
        FROM deliveries WHERE demand_id=? AND id!=?''',
        (day(), day(), demand_id, exclude or '')).fetchone()
    return int(row[0]), int(row[1])


def result_for_source(records):
    """保留单个crid的共享配额，不按品牌或车型拆出多份需求目标。"""
    headers = ['需求编号', '品牌', '车型', '城市&地区', '下单量', '月度总量（仅展示）',
               '额度日期', '下单日期', '开始时间', '结束时间', '更新时间', '类型']
    converted = []
    for index, row in enumerate(records, 1):
        values = {'需求编号': row['demand_no'],
                  '品牌': '、'.join(dict.fromkeys(x['brand'] for x in row['vehicles'])),
                  '车型': '、'.join(dict.fromkeys(x['model'] for x in row['vehicles'])),
                  '城市&地区': '、'.join(x['name'] for x in row['cities']),
                  '下单量': str(row['daily_limit']), '月度总量（仅展示）': str(row['monthly_limit']),
                  '额度日期': row['day'], '下单日期': row['updated_time'][:10],
                  '开始时间': row['begin_time'], '结束时间': row['end_time'],
                  '更新时间': row['updated_time'], '类型': 'KA接口需求'}
        converted.append({'key': row['demand_no'], 'row_number': index,
                          'values': values, 'raw_values': row})
    return {'title': 'KA API需求', 'headers': headers, 'records': converted}


class KaDemandSources:
    """协调来源校验与加密配置，依赖平台事务和既有同步服务。"""

    def __init__(self, sync):
        self.sync = sync
        self.p = sync.p

    def save(self, actor, data, source_id=None):
        """创建/修改API来源，强制KA和DAILY，安排每日08:00及支持手动拉取。"""
        self.p.require(actor, 'demand')
        with self.p.tx() as conn:
            old = self.sync._source(conn, actor, source_id) if source_id else None
            if old and old['source_kind'] != 'API_KA':
                raise Fault('API_SOURCE_INVALID', '该来源不是KA接口需求')
            config = conn.execute('SELECT * FROM ka_demand_sources WHERE source_id=? AND org_id=?',
                                  (source_id, actor['org_id'])).fetchone() if old else None
            mode = data.get('auth_mode', config['auth_mode'] if config else 'NONE')
            url = data.get('url', old['url'] if old else API_URL)
            if url not in (API_URL, API_URL.replace('http:', 'https:')):
                raise Fault('API_URL_INVALID', '仅支持文档指定的KA需求拉取接口')
            if mode not in ('UNCONFIRMED', 'NONE', 'BEARER'):
                raise Fault('API_AUTH_INVALID', '请选择合法的鉴权方式')
            token = data.get('bearer_token', '')
            if not isinstance(token, str) or len(token) > 4096 or any(ord(char) < 32 for char in token):
                raise Fault('API_AUTH_INVALID', '专属Token格式无效')
            cipher = self.p.vault.encrypt(token.strip()) if token.strip() else config['token_cipher'] if config else ''
            if mode != 'BEARER':
                cipher = ''
            if mode == 'BEARER' and (not cipher or not url.startswith('https:')):
                raise Fault('API_AUTH_INVALID', 'Token鉴权须填写专属Token并使用甲方HTTPS接口')
            if data.get('sale_project', 'KA') != 'KA' or data.get('quantity_basis', 'DAILY') != 'DAILY':
                raise Fault('API_SOURCE_INVALID', '该接口固定KA项目和每日提交量口径')
            body = {**data, 'enabled': 0, 'sale_project': 'KA', 'quantity_basis': 'DAILY'}
            body.pop('url', None)
            if not old:
                partner_id = str(data.get('partner_id', ''))
                media_id = str(data.get('media_id', ''))
                key = 'api-ka:' + partner_id + ':' + media_id
                saved = self.sync.save(actor, body, _connection=conn, _file_id=key)
                source_id = saved['id']
            else:
                saved = self.sync.save(actor, body, source_id, _connection=conn)
            conn.execute("UPDATE demand_sync_sources SET source_kind='API_KA',url=?,enabled=?,next_run_at=? WHERE id=? AND org_id=?",
                         (url, int(mode!='UNCONFIRMED'), next_daily_pull(), source_id, actor['org_id']))
            conn.execute('''INSERT INTO ka_demand_sources(source_id,org_id,auth_mode,token_cipher)
                VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                auth_mode=excluded.auth_mode,token_cipher=excluded.token_cipher''',
                (source_id, actor['org_id'], mode, cipher))
            self.p.audit(conn, actor, 'KA_DEMAND_SOURCE_CONFIG', source_id, {'auth_mode': mode})
            return self.sync._public_source(conn, self.sync._source(conn, actor, source_id))

    def fetch(self, actor, source):
        """写事务外读取，配置按组织取出，不使用文档示例凭证。"""
        conn = self.p.connect()
        try:
            config = conn.execute('SELECT * FROM ka_demand_sources WHERE source_id=? AND org_id=?',
                                  (source['id'], actor['org_id'])).fetchone()
        finally:
            conn.close()
        if not config:
            raise DemandProtocolError('KA需求接口配置缺失')
        if config['auth_mode'] == 'UNCONFIRMED':
            raise DemandProtocolError('请先确认甲方鉴权要求，来源已保存但尚未拉取')
        token = self.p.vault.decrypt(config['token_cipher']) if config['token_cipher'] else ''
        records = self.sync.api_fetcher(source['url'], config['auth_mode'], token)
        if any(row['day'] != day().replace('-', '') for row in records):
            raise DemandProtocolError('接口额度日期不是北京时间今日，请甲方更新后重新拉取')
        return result_for_source(records)
