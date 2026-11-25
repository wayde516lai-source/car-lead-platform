"""清空业务记录，保留车型、Code字典及账号与系统基础配置。

仅提供事务内维护函数。调用者须先停写、私有备份，并负责提交后的重启验收。
不会初始化示例，不自动解除仍被保留车型库引用的合作主体外键。
"""

PRESERVED_TABLES = frozenset({
    'schema_version', 'users', 'settings', 'lead_field_permissions',
    'catalog', 'manual_vehicle_entries', 'media_vehicle_sources',
    'media_vehicle_items', 'media_vehicle_runs', 'yiche_code_overrides',
    'media_dictionary_sources', 'media_city_items', 'media_dictionary_runs',
})
CLEARED_TABLES = frozenset({
    'agency_final_media', 'attempts', 'audit_log', 'auto_dispatch_bindings',
    'auto_dispatch_decisions', 'callback_events', 'channels', 'consents',
    'daily_counter', 'dedup_registry', 'deliveries', 'demand_excel_batches',
    'demand_sync_duplicate_rules', 'demand_sync_project_history',
    'demand_sync_projects', 'demand_sync_runs', 'demand_sync_sources', 'ka_demand_sources',
    'demand_versions', 'demands', 'exports', 'hops', 'idempotency', 'imports',
    'inbox', 'ingestions', 'lead_sale_history', 'leads', 'nonces', 'outbox',
    'partner_projects', 'partners', 'privacy_notices', 'privacy_requests',
    'quota_ledger', 'risk_blocklist', 'routes', 'sessions', 'source_api_keys',
    'source_lead_details', 'yiche_submissions', 'zmh_mappings', 'zmh_routes',
    'zmh_submissions',
})


def reset_business_data(conn) -> dict:
    """在调用者事务中删除所有已知业务表记录，逐行核对保留表不变。

    未识别的表、缺失保留表或保留字典仍引用合作主体时抛异常，调用者必须回滚。
    返回仅含表计数的报告，不返回客户字段、字典凭证或用户密码摘要。
    """
    if not conn.in_transaction:
        raise ValueError('必须在停写、备份后的显式事务中执行')
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != PRESERVED_TABLES | CLEARED_TABLES:
        raise ValueError('数据库表与维护清单不一致，请先核对版本')
    preserved = {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                 for table in PRESERVED_TABLES}
    before = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
              for table in CLEARED_TABLES}
    # 用户明确要求连旧业务审计一并重置；离线备份后仅在本事务内暂解删除保护，随即恢复。
    audit_trigger = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='audit_no_delete' AND tbl_name='audit_log'").fetchone()
    if not audit_trigger:
        raise ValueError('审计保护触发器缺失，必须先核对数据库')
    conn.execute('DROP TRIGGER audit_no_delete')
    conn.execute('PRAGMA defer_foreign_keys=ON')
    for table in sorted(CLEARED_TABLES):
        conn.execute(f'DELETE FROM {table}')
    conn.execute(audit_trigger[0])
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('保留字典仍有关联引用，不能删除对应主体，必须回滚')
    for table, rows in preserved.items():
        if rows != [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]:
            raise ValueError('保留表发生变化，必须回滚')
    return {'removed': before, 'preserved': {table: len(rows) for table, rows in preserved.items()},
            'preserved_rows_unchanged': True}
