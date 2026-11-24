"""软米会员接口的显式归属及历史独立探针认领。

只配置现有主体与项目，不创建主体、不迁移业务交付或发送线索。
成功探针通过追加审计关联，仍不计入业务库存、配额或有效量。
"""
import json

from domain import Fault, uid
from zhemanhui import CALLBACK_PATH

PARTNER_NAME = '北京软米科技有限公司(会员 1)'
MEDIA_NAME = '易车'
PROJECT = 'MEMBER'
ADAPTER = 'zhemanhui-v1'
CALLBACK_URL = 'https://saas.asoapple.cn' + CALLBACK_PATH


def binding_plan(conn, actor: dict, *, partner_name=PARTNER_NAME, media_name=MEDIA_NAME,
                 route_id=None, probe_id=None) -> dict:
    """只读核对本组织精确主体、已开通会员项目、路由及可认领的成功探针。

    不按近似名称猜测会员1/会员2，不把失败、未知、其他组织或业务交付作为独立探针。
    无同主体同媒体路由时计划新建停用路由；多条候选须明确route_id。
    """
    def subject(name, role):
        rows = conn.execute('SELECT * FROM partners WHERE org_id=? AND name=? AND role=?',
                            (actor['org_id'], name, role)).fetchall()
        if len(rows) != 1:
            raise Fault('ZMH_SUBJECT_REQUIRED', '请核对本组织唯一且名称完全一致的合作主体和最终媒体')
        return dict(rows[0])

    buyer = subject(partner_name, 'AGENCY')
    media = subject(media_name, 'MEDIA')
    project = conn.execute('SELECT enabled FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',
                           (actor['org_id'], buyer['id'], PROJECT)).fetchone()
    if not project:
        raise Fault('ZMH_MEMBER_REQUIRED', '该合作主体尚未开通会员项目，不能自动开通或绑定KA')
    routes = conn.execute('''SELECT * FROM routes WHERE org_id=? AND buyer_id=? AND receiver_id=?
                            AND media_id=?''' + (' AND id=?' if route_id else ''),
                          (actor['org_id'], buyer['id'], buyer['id'], media['id'])
                          + ((route_id,) if route_id else ())).fetchall()
    if len(routes) > 1 or (route_id and not routes):
        raise Fault('ZMH_ROUTE_REQUIRED', '请明确选择该主体到易车的唯一乙方路由')
    route = dict(routes[0]) if routes else None
    if route and route['adapter_version'] not in ('mock-v1', ADAPTER):
        raise Fault('ZMH_ROUTE_CONFLICT', '该路由已使用其他接口，不能直接覆盖')
    if route and route['adapter_version'] != ADAPTER and conn.execute(
            'SELECT 1 FROM deliveries WHERE org_id=? AND route_id=? LIMIT 1',
            (actor['org_id'], route['id'])).fetchone():
        raise Fault('ZMH_ROUTE_HISTORY', '原路由已有交付记录，请先建立独立接口路由，保留原交付快照')
    config = conn.execute('SELECT enabled FROM zmh_routes WHERE org_id=? AND route_id=?',
                          (actor['org_id'], route['id'])).fetchone() if route else None
    if route and route['adapter_version'] != ADAPTER and config and config['enabled']:
        raise Fault('ZMH_ACTIVE_CONFIG_CONFLICT', '原模拟路由存在启用的接口配置，请先停用后核对，避免意外外发')
    proof = None
    if probe_id:
        probe = conn.execute('SELECT * FROM zmh_submissions WHERE org_id=? AND id=?',
                             (actor['org_id'], probe_id)).fetchone()
        if not probe or probe['delivery_id'] is not None or probe['send_status'] != 'ACKED':
            raise Fault('ZMH_PROBE_REQUIRED', '须选择本组织已确认接收成功且不属于业务交付的独立探针')
        previous = conn.execute("SELECT detail FROM audit_log WHERE org_id=? AND action='ZMH_PROBE_BINDING' AND target_id=?",
                                (actor['org_id'], probe_id)).fetchall()
        for event in previous:
            bound = json.loads(event['detail'])
            if not route or any(bound.get(key) != value for key, value in (
                    ('route_id', route['id']), ('buyer_id', buyer['id']), ('media_id', media['id']),
                    ('project', PROJECT))):
                raise Fault('ZMH_PROBE_ALREADY_BOUND', '该探针已经归属其他接口，不可重复认领')
        proof = {'probe_id': probe_id, 'verified_at': probe['created_at'], 'already_bound': bool(previous)}
    return {
        'org_id': actor['org_id'], 'buyer_id': buyer['id'], 'receiver_id': buyer['id'],
        'partner_name': buyer['name'], 'media_id': media['id'], 'media_name': media['name'],
        'project': PROJECT, 'adapter_version': ADAPTER, 'route_id': route['id'] if route else None,
        'create_disabled_route': route is None, 'route_enabled': bool(route and route['enabled']),
        'interface_enabled': bool(config and config['enabled']),
        'project_enabled': bool(buyer['enabled'] and media['enabled'] and project['enabled']),
        'proof': proof,
    }


def bind_member_interface(platform, actor: dict, *, apply=False, **selection) -> dict:
    """工程权限下预览或原子应用接口归属，追加审计，不执行网络请求。

    apply默认False。应用只设置适配器及关联，保持既有启停，新配置默认停用。
    独立成功探针仍保留delivery_id为空，绝不伪造业务分发来制造接通状态。
    """
    platform.require(actor, 'engineering')
    if not apply:
        conn = platform.connect()
        try:
            return binding_plan(conn, actor, **selection)
        finally:
            conn.close()
    with platform.tx() as conn:
        plan = binding_plan(conn, actor, **selection)
        route_id = plan['route_id'] or uid('route_')
        if plan['create_disabled_route']:
            conn.execute('''INSERT INTO routes(id,org_id,buyer_id,receiver_id,media_id,enabled,
                            adapter_version,supports_query,supports_idempotency) VALUES(?,?,?,?,?,0,?,0,0)''',
                         (route_id, actor['org_id'], plan['buyer_id'], plan['receiver_id'], plan['media_id'], ADAPTER))
        else:
            conn.execute('UPDATE routes SET adapter_version=?,supports_query=0,supports_idempotency=0 WHERE org_id=? AND id=?',
                         (ADAPTER, actor['org_id'], route_id))
        conn.execute('INSERT OR IGNORE INTO zmh_routes(route_id,org_id,callback_url,enabled) VALUES(?,?,?,0)',
                     (route_id, actor['org_id'], CALLBACK_URL))
        conn.execute('INSERT OR IGNORE INTO agency_final_media(org_id,agency_id,media_id) VALUES(?,?,?)',
                     (actor['org_id'], plan['buyer_id'], plan['media_id']))
        evidence = {key: plan[key] for key in ('buyer_id', 'receiver_id', 'media_id', 'project', 'adapter_version')}
        evidence['route_id'] = route_id
        platform.audit(conn, actor, 'ZMH_INTERFACE_BINDING', route_id, evidence)
        if plan['proof'] and not plan['proof']['already_bound']:
            platform.audit(conn, actor, 'ZMH_PROBE_BINDING', plan['proof']['probe_id'], evidence)
        return {**plan, 'route_id': route_id, 'applied': True}
