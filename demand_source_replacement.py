"""校验新文档后原子替换需求来源，旧文档与交付历史保留独立身份。"""
from domain import Fault, now, uid
from qq_sheet import QQSheetError, read_qq_sheet, sheet_url


def _editable_source(sync, conn, actor, source_id, version):
    source = sync._source(conn, actor, source_id)
    if source['source_kind'] != 'QQ_DOC':
        raise Fault('IMMUTABLE', 'Excel导入来源不能改为文档链接')
    if source['replaced_by']:
        raise Fault('SOURCE_REPLACED', '该来源已被更换，请刷新使用当前链接', 409)
    if source['version'] != version:
        raise Fault('VERSION_CONFLICT', '来源已被修改，请刷新后重试', 409)
    if source['running_until'] and source['running_until'] > now():
        raise Fault('SYNC_RUNNING', '该来源正在同步，请结束后修改链接', 409)
    return source


def _target_source(sync, conn, actor, doc_id, tab_id, current_id):
    row = conn.execute('SELECT * FROM demand_sync_sources WHERE org_id=? AND doc_id=? AND tab_id=?',
                       (actor['org_id'], doc_id, tab_id)).fetchone()
    if not row or row['id'] == current_id:
        return dict(row) if row else None
    # 允许改回本次更换链上的旧文档，但不能接管另一份正在使用的来源。
    seen = set()
    cursor = dict(row)
    while cursor['replaced_by'] and cursor['id'] not in seen:
        seen.add(cursor['id'])
        if cursor['replaced_by'] == current_id:
            return dict(row)
        cursor = sync._source(conn, actor, cursor['replaced_by'])
    raise Fault('SYNC_DUPLICATE', '该工作表已有其他需求来源，请修改已有来源，不能重复接入', 409)


def _read_candidate(sync, url, rules):
    try:
        result = (sync.fetcher(url, duplicate_rules=rules) if sync.fetcher is read_qq_sheet
                  else sync.fetcher(url))
        records = result.get('records', [])
        if (not records or any(not isinstance(row.get('key'), str) or not row['key']
                               or not isinstance(row.get('values'), dict)
                               or not isinstance(row.get('raw_values'), dict)
                               or type(row.get('row_number')) is not int for row in records)
                or len({row['key'] for row in records}) != len(records)):
            raise QQSheetError('新表为空、字段格式不完整或需求编号重复')
        requested_url, requested_doc, requested_tab = sheet_url(url)
        resolved_url, resolved_doc, resolved_tab = sheet_url(result.get('url', requested_url))
        if resolved_doc != requested_doc or (resolved_tab != requested_tab and not (
                result.get('notice') and result.get('requested_tab') == requested_tab
                and result.get('tab') == resolved_tab)):
            raise QQSheetError('新链接返回的文档身份不一致')
        return result, resolved_url, resolved_doc, resolved_tab
    except QQSheetError as error:
        raise Fault('SYNC_URL_UNREADABLE', '新链接未通过校验，原链接和需求未改变：' + str(error)[:250]) from None
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Fault('SYNC_URL_UNREADABLE', '新链接数据格式无法识别，原链接和需求未改变') from None


def replace_source_link(sync, actor, source_id, data) -> dict:
    """在写事务外读取新表，再原子归档旧来源并启用新来源。

    权限、组织、版本与同步锁在读取前后复核。不同文档不混用项目ID，旧表停止
    后续分发且取消未发送任务，已发送交付及快照不改。失败整体回滚，可改回同链旧表。
    """
    sync.p.require(actor, 'demand')
    conn = sync.p.connect()
    try:
        old = _editable_source(sync, conn, actor, source_id, data.get('version'))
        try:
            url, doc_id, tab_id = sheet_url(data.get('url', ''))
        except QQSheetError as error:
            raise Fault('SYNC_URL_INVALID', str(error)) from None
        target = _target_source(sync, conn, actor, doc_id, tab_id, source_id)
        rules = {row['source_key']: row['policy'] for row in conn.execute(
            'SELECT * FROM demand_sync_duplicate_rules WHERE source_id=? AND org_id=?',
            (target['id'] if target else '', actor['org_id']))}
        sync.validate_config(conn, actor, data, old)
    finally:
        conn.close()
    # 新文档的读取不持有SQLite写锁，失败不会修改现有来源或释放旧配额。
    result, url, doc_id, tab_id = _read_candidate(sync, url, rules)
    with sync.p.tx() as conn:
        old = _editable_source(sync, conn, actor, source_id, data.get('version'))
        target = _target_source(sync, conn, actor, doc_id, tab_id, source_id)
        interval, enabled, media, project, partner, name, route, basis = sync.validate_config(conn, actor, data, old)
        if not route:
            routes = conn.execute('SELECT id FROM routes WHERE org_id=? AND buyer_id=? AND media_id=?',
                                  (actor['org_id'], partner, media)).fetchall()
            if len(routes) != 1:
                raise Fault('SYNC_ROUTE_REQUIRED', '请为新链接选择对应合作主体和最终媒体的交付路由')
            route = routes[0]['id']
        config = {'url': url, 'name': name, 'partner_id': partner, 'media_id': media,
                  'sale_project': project, 'route_id': route, 'quantity_basis': basis,
                  'interval_minutes': interval, 'enabled': enabled}
        same_source = bool(target and target['id'] == source_id)
        if target:
            new_id = target['id']
            conn.execute('UPDATE demand_sync_sources SET replaced_by=NULL WHERE id=? AND org_id=?',
                         (new_id, actor['org_id']))
        else:
            new_id = sync.save(actor, config, _connection=conn)['id']
        if not same_source:
            conn.execute('UPDATE demand_sync_sources SET replaced_by=?,enabled=0,version=version+1,run_id=NULL,running_until=NULL WHERE id=? AND org_id=?',
                         (new_id, source_id, actor['org_id']))
            if old['run_id']:
                conn.execute("UPDATE demand_sync_runs SET status='FAILED',finished_at=?,error=? WHERE id=? AND org_id=? AND status='RUNNING'",
                             (now(), '来源链接已更换，本轮同步终止', old['run_id'], actor['org_id']))
            conn.execute('UPDATE demand_sync_projects SET is_present=0,changed_at=? WHERE source_id=? AND org_id=?',
                         (now(), source_id, actor['org_id']))
            sync.p._reconcile_source(conn, actor, source_id)
        if target:
            sync.save(actor, {**config, 'version': target['version']}, new_id, _connection=conn)
        current = sync._source(conn, actor, new_id)
        run_id = uid('dsr_')
        conn.execute('INSERT INTO demand_sync_runs(id,source_id,org_id,trigger_kind,status,started_at) VALUES(?,?,?,?,?,?)',
                     (run_id, new_id, actor['org_id'], 'link_change', 'RUNNING', now()))
        conn.execute('UPDATE demand_sync_sources SET last_attempt_at=? WHERE id=? AND org_id=?',
                     (now(), new_id, actor['org_id']))
        outcome = sync._store_result(conn, actor, new_id, current, result, rules, run_id)
        if outcome['demands']['errors']:
            raise Fault('SYNC_URL_REPLACEMENT_INVALID', '新表存在无法映射的需求，已整体回滚，原链接和需求保持不变')
        sync.p.audit(conn, actor, 'DEMAND_SOURCE_URL_REPLACED', source_id,
                     {'new_source_id': new_id, 'old_url': old['url'], 'new_url': url,
                      'project_count': len(result['records']), 'restored_previous': bool(target and not same_source)})
        response = sync._public_source(conn, sync._source(conn, actor, new_id))
        response.update(url_changed=True, replaced_source_id=None if same_source else source_id,
                        sync_result=outcome)
        return response
