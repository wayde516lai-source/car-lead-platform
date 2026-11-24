"""交付路由发送管理：权限、版本、阻拦原因及审计。

仅配置已经绑定的软米会员适配器，不创建绑定、不修改服务器保护开关、
不执行网络或试发送。依赖Platform的组织对象查询、事务、取消预占及审计。
"""
import os

from zhemanhui import TEST_URL, PRODUCTION_URL


def control(conn, org_id, route_id):
    row = conn.execute('SELECT environment,version FROM route_send_controls WHERE org_id=? AND route_id=?',
                       (org_id, route_id)).fetchone()
    return dict(row) if row else {'environment': 'TEST', 'version': 0}


def send_block(conn, org_id, route):
    """手动、自动、预览和worker共同检查真实软米发送保护，不改动其开关。"""
    config = conn.execute('SELECT enabled FROM zmh_routes WHERE org_id=? AND route_id=?',
                          (org_id, route['id'])).fetchone()
    if os.environ.get('ZMH_ENABLE_TEST_PUSH') != '1':
        return 'ZMH_SERVER_DISABLED'
    if not config or not config['enabled']:
        return 'ZMH_ROUTE_DISABLED'
    return None


def status(platform, conn, actor, route):
    """返回本组织公开配置和阻拦原因，不返回回调令牌、报文或客户信息。"""
    config = conn.execute('SELECT enabled FROM zmh_routes WHERE org_id=? AND route_id=?',
                          (actor['org_id'], route['id'])).fetchone()
    supported = route['adapter_version'] == 'zhemanhui-v1' and config is not None
    values = control(conn, actor['org_id'], route['id'])
    blockers = []
    if not supported:
        blockers.append('当前路由未绑定可管理的软米会员发送接口')
    else:
        if os.environ.get('ZMH_ENABLE_TEST_PUSH') != '1':
            blockers.append('服务器发送总开关关闭，需维护人员开启')
        if not config['enabled']:
            blockers.append('路由接口发送开关关闭')
        for partner_id in {route['buyer_id'], route['receiver_id'], route['media_id']}:
            subject = platform.one(conn, 'partners', partner_id, actor)
            if not subject['enabled']:
                blockers.append('合作主体或最终媒体已停用')
                break
        project = conn.execute('SELECT enabled FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',
                               (actor['org_id'], route['buyer_id'], 'MEMBER')).fetchone()
        if not project or not project['enabled']:
            blockers.append('会员项目未开通或已停用')
        kinds = {row[0] for row in conn.execute('SELECT DISTINCT kind FROM zmh_mappings WHERE org_id=? AND route_id=?',
                                               (actor['org_id'], route['id']))}
        if not {'city', 'series'} <= kinds:
            blockers.append('尚未配置城市或车型映射')
    if not route['enabled']:
        blockers.append('交付路由已停用')
    setting = conn.execute('SELECT dispatch_enabled FROM settings WHERE org_id=?', (actor['org_id'],)).fetchone()
    if not setting or not setting[0]:
        blockers.append('全局分发开关关闭')
    environment = values['environment'] if supported else 'MOCK' if route['adapter_version'] == 'mock-v1' else 'EXTERNAL'
    if environment == 'MOCK' and not platform.mock_enabled:
        blockers.append('服务器禁止模拟分发')
    return {'route_id': route['id'], 'project': 'MEMBER' if supported else None,
            'supported': supported, 'environment': environment, 'version': values['version'],
            'endpoint': (PRODUCTION_URL if environment == 'PRODUCTION' else TEST_URL) if supported else None,
            'enabled': bool(config and config['enabled'] and route['enabled']),
            'server_enabled': os.environ.get('ZMH_ENABLE_TEST_PUSH') == '1' if supported else None,
            'blockers': blockers}


def get_status(platform, actor, route_id):
    """只读查看路由配置和最近20次本组织变更，需已有读取权限。"""
    platform.require(actor, 'read')
    from domain import PERMS
    conn = platform.connect()
    try:
        route = platform.one(conn, 'routes', route_id, actor)
        result = status(platform, conn, actor, route)
        # 操作记录仅向能够管理发送的管理员返回，不扩大普通读取账号的审计权限。
        if {'engineering', 'distribution_rules'} <= PERMS.get(actor['role'], set()):
            import json
            result['history'] = [dict(row) for row in conn.execute(
                'SELECT actor_id,created_at,detail FROM audit_log WHERE org_id=? AND target_id=? AND action=? ORDER BY created_at DESC,rowid DESC LIMIT 20',
                (actor['org_id'], route_id, 'ROUTE_SEND_UPDATED'))]
            for event in result['history']:
                event['detail'] = json.loads(event['detail'])
        return result
    finally:
        conn.close()


def update(platform, actor, route_id, data):
    """管理员独立配置发送开关，环境切换须先停用，取消未发送预占并保留历史。

    只接受固定测试/正式枚举和当前版本。环境切换不能同次启用，
    保存不发送请求、不修改服务器环境变量或自动分发策略。
    """
    from domain import Fault
    platform.require(actor, 'engineering')
    platform.require(actor, 'distribution_rules')
    environment, enabled = data.get('environment'), data.get('enabled')
    reason = data.get('reason')
    if environment not in ('TEST', 'PRODUCTION') or type(enabled) is not int or enabled not in (0, 1):
        raise Fault('ROUTE_SEND_INVALID', '请选择测试或正式环境及合法的发送状态')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
        raise Fault('ROUTE_SEND_REASON_REQUIRED', '请填写不超过300字的修改原因')
    if type(data.get('version')) is not int:
        raise Fault('VERSION_CONFLICT', '请刷新路由配置后重试', 409)
    with platform.tx() as conn:
        route = platform.one(conn, 'routes', route_id, actor)
        before = status(platform, conn, actor, route)
        if not before['supported']:
            raise Fault('ROUTE_SEND_UNSUPPORTED', '请先绑定软米会员发送接口，不能将模拟或其他接口直接改为真实发送')
        if before['version'] != data['version']:
            raise Fault('VERSION_CONFLICT', '路由配置已变化，请刷新后重试', 409)
        config = conn.execute('SELECT enabled FROM zmh_routes WHERE org_id=? AND route_id=?',
                              (actor['org_id'], route_id)).fetchone()
        if environment != before['environment'] and (config['enabled'] or enabled):
            raise Fault('ROUTE_ENV_REQUIRES_PAUSE', '请先停用发送，再单独切换接口环境；切换后需另行启用')
        old = {'environment': before['environment'], 'enabled': int(before['enabled'])}
        new = {'environment': environment, 'enabled': enabled}
        conn.execute('INSERT INTO route_send_controls(route_id,org_id,environment,version) VALUES(?,?,?,1) ON CONFLICT(route_id) DO UPDATE SET environment=excluded.environment,version=route_send_controls.version+1',
                     (route_id, actor['org_id'], environment))
        conn.execute('UPDATE zmh_routes SET enabled=? WHERE org_id=? AND route_id=?', (enabled, actor['org_id'], route_id))
        conn.execute('UPDATE routes SET enabled=? WHERE org_id=? AND id=?', (enabled, actor['org_id'], route_id))
        if old != new or not enabled:
            for row in conn.execute('SELECT id FROM deliveries WHERE org_id=? AND route_id=? AND send_status=? AND first_sent_at IS NULL',
                                    (actor['org_id'], route_id, 'QUEUED')).fetchall():
                platform._cancel(conn, actor, row['id'], 'ROUTE_SEND_CHANGED')
        platform.audit(conn, actor, 'ROUTE_SEND_UPDATED', route_id, {'before': old, 'after': new, 'reason': reason.strip()})
        return status(platform, conn, actor, platform.one(conn, 'routes', route_id, actor))
