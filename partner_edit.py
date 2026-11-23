"""合作主体基本资料编辑；不通过改角色或隐式开项目重解释既有交付。"""
from decimal import Decimal, InvalidOperation


def update_partner(platform, actor, partner_id: str, data: dict) -> dict:
    """按组织和配置权限编辑资料，前端expected用于检测资料被他人修改。

    角色不可变；默认项目须已开通。新增最终媒体只增加关联，不移除历史路由。
    所有修改和审计在同一事务中，历史交付快照不变。
    """
    from domain import Fault
    platform.require(actor, 'settings')
    with platform.tx() as conn:
        partner = platform.one(conn, 'partners', partner_id, actor)
        editable = ('name', 'cooperation_project', 'cooperation_price_fen', 'cooperation_note', 'enabled')
        expected = data.get('expected')
        if expected is not None and (not isinstance(expected, dict) or
                any(key not in editable or partner[key] != value for key, value in expected.items())):
            raise Fault('VERSION_CONFLICT', '主体资料已被修改，请刷新后重试', 409)
        if 'role' in data and data['role'] != partner['role']:
            raise Fault('PARTNER_ROLE_IMMUTABLE', '已有主体不能直接更换角色，请新增正确角色的主体')
        values = {}
        if 'name' in data:
            name = data['name']
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
                raise Fault('PARTNER_INVALID', '主体名称必填且最多100字')
            values['name'] = name.strip()
        if 'cooperation_note' in data:
            note = data['cooperation_note']
            if not isinstance(note, str) or len(note) > 1000:
                raise Fault('COOPERATION_NOTE_INVALID', '备注说明最多1000字')
            values['cooperation_note'] = note.strip()
        if 'cooperation_price_yuan' in data:
            raw = data['cooperation_price_yuan']
            if raw in ('', None):
                values['cooperation_price_fen'] = None
            else:
                try:
                    price = Decimal(str(raw))
                    if not price.is_finite() or price < 0 or price * 100 != (price * 100).to_integral_value():
                        raise InvalidOperation
                    values['cooperation_price_fen'] = int(price * 100)
                    if values['cooperation_price_fen'] > 9_000_000_000_000:
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    raise Fault('PRICE_INVALID', '合作价格须为非负金额，最多两位小数') from None
        if 'cooperation_project' in data:
            project = data['cooperation_project']
            if project not in ('MEMBER', 'KA') or not conn.execute(
                    'SELECT 1 FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',
                    (actor['org_id'], partner_id, project)).fetchone():
                raise Fault('COOPERATION_PROJECT_REQUIRED', '请先开通所选合作项目，再设为默认项目')
            values['cooperation_project'] = project
        if 'enabled' in data:
            if type(data['enabled']) not in (int, bool) or data['enabled'] not in (0, 1):
                raise Fault('PARTNER_INVALID', '启用状态须为0或1')
            values['enabled'] = int(data['enabled'])
        if values:
            conn.execute('UPDATE partners SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=? AND org_id=?',
                         (*values.values(), partner_id, actor['org_id']))
        if values.get('enabled') == 0:
            for row in conn.execute(
                    "SELECT x.id FROM deliveries x JOIN demands d ON d.id=x.demand_id WHERE x.org_id=? AND d.org_id=? AND d.buyer_id=? AND x.first_sent_at IS NULL AND x.send_status='QUEUED'",
                    (actor['org_id'], actor['org_id'], partner_id)).fetchall():
                platform._cancel(conn, actor, row['id'], 'PROJECT_PAUSED')
        if data.get('final_media_id') or data.get('final_media'):
            if partner['role'] != 'AGENCY':
                raise Fault('FINAL_MEDIA_INVALID', '仅乙方可增加最终媒体关联')
            platform.attach_agency_media(conn, actor, partner_id, data)
        platform.audit(conn, actor, 'PARTNER_EDIT', partner_id, {'fields': list(values), 'media_link_added': bool(data.get('final_media_id') or data.get('final_media'))})
    return {'ok': True}
