"""Remove explicitly identified demo records; preserve imported business data.

Default is dry-run. --apply creates an encrypted backup and runs one transaction.
Only manifests and known seed identifiers are evidence; customer names are not.
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from domain import Platform, now, uid


def cleanup(conn, root=ROOT):
    """Delete known demo graph in the caller's transaction and repair counters."""
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if re.fullmatch(r'[a-z_]+', row[0])]
    selected = {table: set() for table in tables}
    manifests = [('manual-matching-test-batch.json', 'lead_id', 'leads'),
                 ('manual-matching-test-demands.json', 'demand_id', 'demands')]
    for filename, key, table in manifests:
        path = root / 'docs' / filename
        if path.exists():
            for item in json.loads(path.read_text()).get('items', []):
                selected[table].update(row[0] for row in conn.execute(
                    f'SELECT rowid FROM {table} WHERE id=?', (item[key],)))
    for row in conn.execute("SELECT lead_id,source_record_id FROM ingestions WHERE org_id='demo'"):
        if re.fullmatch(r'TEST-(?:\d{3}|\d{13})', row[1]):
            selected['leads'].update(value[0] for value in conn.execute(
                'SELECT rowid FROM leads WHERE id=?', (row[0],)))
    for row in conn.execute("SELECT rowid,demand_no FROM demands WHERE org_id='demo' AND external_project_id IS NULL"):
        if row[1] in ('000123', 'AUTOHOME-0098', 'AG-TO-YC-001', 'AG-TO-AH-002'):
            selected['demands'].add(row[0])
    selected['deliveries'].update(row[0] for row in conn.execute(
        "SELECT DISTINCT d.rowid FROM deliveries d JOIN audit_log a ON a.target_id=d.id "
        "WHERE d.org_id='demo' AND a.action='MOCK_SEND'"))
    changed = True
    while changed:
        changed = False
        for table in tables:
            for fk in conn.execute(f'PRAGMA foreign_key_list({table})').fetchall():
                parent, child_column, parent_column = fk[2], fk[3], fk[4]
                ids = selected.get(parent)
                if not ids:
                    continue
                placeholders = ','.join('?' for _ in ids)
                query = (f'SELECT rowid FROM {table} WHERE {child_column} IN '
                         f'(SELECT {parent_column} FROM {parent} WHERE rowid IN ({placeholders}))')
                found = {row[0] for row in conn.execute(query, tuple(ids))}
                previous = len(selected[table])
                selected[table].update(found)
                changed |= len(selected[table]) != previous
    delivery_rows = []
    for row_id in selected['deliveries']:
        delivery_rows.append(dict(conn.execute('SELECT * FROM deliveries WHERE rowid=?', (row_id,)).fetchone()))
    affected_demands = {row['demand_id'] for row in delivery_rows}
    # Defer FK checks until the whole identified dependency graph is deleted.
    conn.execute('PRAGMA defer_foreign_keys=ON')
    for table, ids in selected.items():
        for row_id in ids:
            conn.execute(f'DELETE FROM {table} WHERE rowid=?', (row_id,))
    for demand_id in affected_demands:
        totals = conn.execute('SELECT COALESCE(sum(reserved_delta),0),COALESCE(sum(valid_delta),0) '
                              'FROM quota_ledger WHERE demand_id=?', (demand_id,)).fetchone()
        conn.execute('UPDATE demands SET reserved_count=?,valid_count=? WHERE id=?', (*totals, demand_id))
    for delivery in delivery_rows:
        reserved = int(not delivery['first_sent_at'] and delivery['send_status'] == 'QUEUED')
        sent = int(bool(delivery['first_sent_at']))
        conn.execute('UPDATE daily_counter SET reserved=reserved-?,sent=sent-? WHERE demand_id=? AND day=?',
                     (reserved, sent, delivery['demand_id'], delivery['day']))
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError('Foreign key check failed; cleanup rolled back')
    if conn.execute('SELECT 1 FROM daily_counter WHERE reserved<0 OR sent<0').fetchone():
        raise RuntimeError('Counter check failed; cleanup rolled back')
    return {table: len(ids) for table, ids in selected.items() if ids}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default=str(ROOT / 'data'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    folder = Path(args.data).resolve()
    if not (folder / 'platform.sqlite').is_file():
        parser.error('Existing database directory required')
    platform = Platform(folder)
    actor = {'id': 'system:demo-cleanup', 'role': 'admin', 'org_id': 'demo'}
    backup = platform.backup(actor) if args.apply else None
    conn = platform.connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        before = {table: conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
                  for table in ('leads', 'demands', 'deliveries', 'demand_sync_projects')}
        removed = cleanup(conn)
        after = {table: conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before}
        result = {'applied': args.apply, 'before': before, 'removed': removed, 'after': after, 'backup': backup}
        if args.apply:
            platform.audit(conn, actor, 'DEMO_DATA_CLEANUP', 'demo', result)
            conn.commit()
        else:
            conn.rollback()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
