"""删除整份来源不删除业务历史，迟到同步不能复活来源。"""
import copy
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from demand_sync import DemandSync
from domain import Fault, Platform
from qq_sheet import QQSheetError
from security import digest
import server
import test_synced_demands as fixtures

ACTOR = fixtures.A
OTHER_URL = 'https://docs.qq.com/sheet/DOTHERPROJECT?tab=BB08J2'


class SourceDeletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SyncedDemandTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.p, self.sync = self.fixture.p, self.fixture.sync
        self.source = self.sync.sources(ACTOR)[0]

    def delete(self, source=None):
        source = source or self.source
        return self.sync.delete(ACTOR, source['id'], source['version'])

    def test_failed_empty_source_can_be_deleted_without_touching_other_source(self):
        wrong = self.sync.save(ACTOR, {'name': '错误链接', 'url': OTHER_URL, 'partner_id': 'MEDIA_A',
                                      'media_id': 'MEDIA_A', 'sale_project': 'MEMBER', 'enabled': 1})
        self.sync.fetcher = Mock(side_effect=QQSheetError('首行缺少需求编号表头'))
        self.assertEqual(self.sync.run(ACTOR, wrong['id'])['status'], 'FAILED')
        self.delete(wrong)
        self.assertEqual([row['id'] for row in self.sync.sources(ACTOR)], [self.source['id']])
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 1)
        self.assertEqual(self.p.detail(ACTOR, 'demands', self.fixture.d['id'])['status'], 'ACTIVE')

    def test_delete_closes_demands_cancels_only_unsent_preserves_callbacks_and_source(self):
        self.fixture.data['records'] = [fixtures.record(qty='2')]
        self.fixture.run_sync()
        sent_id = self.fixture.send(self.fixture.lead())['items'][0]['delivery_id']
        self.p.worker(ACTOR)
        queued_id = self.fixture.send(self.fixture.lead(), key='queued')['items'][0]['delivery_id']
        before = self.p.detail(ACTOR, 'deliveries', sent_id)
        result = self.delete()
        self.assertEqual(result['closed_demands'], 1)
        self.assertEqual(self.sync.sources(ACTOR), [])
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 0)
        demand = self.p.detail(ACTOR, 'demands', self.fixture.d['id'])
        self.assertEqual((demand['status'], demand['sync_state']), ('CLOSED', 'REMOVED'))
        self.assertEqual(self.p.detail(ACTOR, 'deliveries', queued_id)['send_status'], 'CANCELED')
        after = self.p.detail(ACTOR, 'deliveries', sent_id)
        self.assertEqual(after['demand_snapshot_public'], before['demand_snapshot_public'])
        self.assertEqual(after['quota_state'], 'RESERVED')
        self.p.mock_callback(ACTOR, sent_id, 'VALID')
        self.assertEqual(self.p.detail(ACTOR, 'deliveries', sent_id)['quota_state'], 'VALID')
        self.assertEqual(self.sync.projects(ACTOR, self.source['id'])['items'][0]['values']['下单量'], '2')
        self.assertNotIn(self.fixture.d['id'], [row['id'] for row in self.p.list(ACTOR, 'demands')['items']])

    def test_permissions_versions_tenant_scope_and_idempotency(self):
        for actor, version in [({**ACTOR, 'role': 'operator'}, self.source['version']),
                               ({**ACTOR, 'org_id': 'other'}, self.source['version']),
                               (ACTOR, self.source['version'] + 1), (ACTOR, None)]:
            with self.subTest(actor=actor, version=version), self.assertRaises(Fault):
                self.sync.delete(actor, self.source['id'], version)
        self.assertEqual(len(self.sync.sources(ACTOR)), 1)
        self.delete()
        self.assertTrue(self.delete()['already_deleted'])
        with self.p.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit_log WHERE action='DEMAND_SOURCE_DELETED'").fetchone()[0], 1)

    def test_late_sync_is_invalidated_and_cannot_revive_deleted_source(self):
        records = copy.deepcopy(self.fixture.data)
        def fetch(url):
            self.delete()
            return records
        self.sync.fetcher = fetch
        result = self.sync.run(ACTOR, self.source['id'])
        self.assertEqual(result['status'], 'FAILED')
        self.assertIn('已删除', result['error'])
        with self.p.connect() as conn:
            source = self.sync._source(conn, ACTOR, self.source['id'])
        self.assertEqual(source['enabled'], 0)
        self.assertIsNone(source['run_id'])
        self.assertTrue(source['deleted_at'])
        run = next(row for row in self.sync.runs(ACTOR, self.source['id']) if row['id'] == result['id'])
        self.assertIn('来源已删除', run['error'])
        with self.p.tx() as conn:
            self.p._reconcile_source(conn, ACTOR, self.source['id'])
        self.assertEqual(self.p.detail(ACTOR, 'demands', self.fixture.d['id'])['status'], 'CLOSED')
        for operation in (
                lambda: self.sync.run(ACTOR, self.source['id']),
                lambda: self.sync.save(ACTOR, {'version': source['version']}, self.source['id']),
                lambda: self.sync.save(ACTOR, {'version': source['version'], 'url': OTHER_URL}, self.source['id'])):
            with self.assertRaises(Fault) as caught:
                operation()
            self.assertEqual(caught.exception.code, 'SOURCE_DELETED')
        self.sync.fetcher = Mock(side_effect=AssertionError('deleted source must not be fetched'))
        self.sync.tick()
        self.sync.fetcher.assert_not_called()

    def test_excel_source_deletion_and_new_correct_link_keep_old_identity(self):
        with self.p.tx() as conn:
            conn.execute("UPDATE demand_sync_sources SET source_kind='EXCEL' WHERE id=?", (self.source['id'],))
        self.delete()
        new = self.sync.save(ACTOR, {'name': '更正链接', 'url': OTHER_URL, 'partner_id': 'MEDIA_A',
                                    'media_id': 'MEDIA_A', 'sale_project': 'MEMBER', 'enabled': 0})
        result = self.sync.run(ACTOR, new['id'])
        self.assertEqual(result['demands']['errors'], 0)
        self.assertNotEqual(new['id'], self.source['id'])
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 1)
        self.assertEqual(self.p.detail(ACTOR, 'demands', self.fixture.d['id'])['status'], 'CLOSED')

    def test_migration_preserves_existing_sources_and_only_runs_once(self):
        with self.p.tx() as conn:
            conn.execute('ALTER TABLE demand_sync_sources DROP COLUMN deleted_at')
            conn.execute('ALTER TABLE demand_sync_sources DROP COLUMN deleted_by')
            conn.execute('DELETE FROM schema_version WHERE version=30')
            before = [tuple(row) for row in conn.execute('SELECT * FROM demand_sync_sources ORDER BY id')]
        Platform(self.p.root)
        Platform(self.p.root)
        with self.p.connect() as conn:
            rows = conn.execute('SELECT * FROM demand_sync_sources ORDER BY id').fetchall()
            self.assertEqual([tuple(row)[:-2] for row in rows], before)
            self.assertTrue(all(row['deleted_at'] is None and row['deleted_by'] is None for row in rows))
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_version WHERE version=30').fetchone()[0], 1)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_http_project_filter_and_delete_require_session_csrf(self):
        token, csrf = 'isolated-delete-test-session', 'isolated-delete-test-csrf'
        with self.p.tx() as conn:
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                         (digest(token), ACTOR['id'], time.time() + 60, csrf))
        with patch.object(server, 'P', self.p), patch.object(server, 'DEMAND_SYNC', self.sync):
            http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            def request(path, method='GET', include_csrf=True):
                headers = {'Cookie': 'lead_session=' + token, 'Content-Type': 'application/json'}
                if include_csrf:
                    headers['X-CSRF-Token'] = csrf
                body = json.dumps({'version': self.source['version']}).encode() if method == 'DELETE' else None
                req = urllib.request.Request(f'http://127.0.0.1:{http.server_port}' + path,
                                             data=body, headers=headers, method=method)
                try:
                    response = urllib.request.urlopen(req)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, json.load(response)
            try:
                status, body = request('/api/v1/demand-sync/projects?project=MEMBER')
                self.assertEqual(status, 200)
                self.assertEqual(body['data']['total'], 1)
                path = '/api/v1/demand-sync/sources/' + self.source['id']
                self.assertEqual(request(path, 'DELETE', False)[0], 403)
                self.assertEqual(len(self.sync.sources(ACTOR)), 1)
                status, body = request(path, 'DELETE')
                self.assertEqual(status, 200)
                self.assertTrue(body['data']['deleted'])
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=2)

    def test_member_ka_filter_is_combined_and_scopes_source_cards(self):
        self.fixture.data['records'] = [fixtures.record(key='KA-ONLY')]
        ka = self.sync.save(ACTOR, {'name': 'KA需求', 'url': OTHER_URL, 'partner_id': 'MEDIA_A',
                                   'media_id': 'MEDIA_A', 'sale_project': 'KA', 'enabled': 0})
        self.sync.run(ACTOR, ka['id'])
        member = self.sync.all_projects(ACTOR, filters={'project': 'MEMBER'})
        self.assertEqual(member['total'], 1)
        self.assertEqual(member['source_ids'], [self.source['id']])
        selected = self.sync.all_projects(ACTOR, filters={'project': 'KA', 'brand': '宝马',
                                                        'city': '上海', 'match_status': 'ACTIVE'})
        self.assertEqual(selected['total'], 1)
        self.assertEqual(selected['source_ids'], [ka['id']])
        self.assertEqual(selected['items'][0]['sale_project'], 'KA')
        self.assertEqual(selected['summary']['order_quantities']['daily'], 1)
        self.assertEqual(self.sync.all_projects(ACTOR, filters={'project': ''})['total'], 2)
        with self.assertRaises(Fault):
            self.sync.all_projects(ACTOR, filters={'project': 'INVALID'})
        self.assertEqual(self.sync.all_projects({**ACTOR, 'org_id': 'other'}, filters={'project': 'KA'})['total'], 0)


if __name__ == '__main__':
    unittest.main()
