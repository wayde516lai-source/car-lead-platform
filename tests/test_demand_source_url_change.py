"""更换链接失败不损坏现有需求，不同文档不复用项目身份，历史交付保持不变。"""
import copy
import unittest

from domain import Fault, Platform
from qq_sheet import QQSheetError
import test_synced_demands as fixtures

ACTOR = fixtures.A
NEW_URL = 'https://docs.qq.com/sheet/DNEWPROJECTDOC?tab=NEW1'


class SourceUrlChangeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SyncedDemandTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.p, self.sync = self.fixture.p, self.fixture.sync
        self.old = self.sync.sources(ACTOR)[0]
        self.records = {'title': '新表', 'headers': list(fixtures.record()['values']),
                        'records': [fixtures.record(qty='2')]}
        self.sync.fetcher = lambda url: copy.deepcopy(self.records)

    def replace(self, source=None, url=NEW_URL):
        source = source or self.sync.sources(ACTOR)[0]
        return self.sync.save(ACTOR, {'version': source['version'], 'url': url}, source['id'])

    def test_change_archives_old_cancels_unsent_and_preserves_external_number(self):
        delivery_id = self.fixture.send(self.fixture.lead())['items'][0]['delivery_id']
        old_demand_id = self.fixture.d['id']
        changed = self.replace()
        self.assertNotEqual(changed['id'], self.old['id'])
        self.assertEqual(changed['url'], NEW_URL)
        self.assertEqual(changed['project_count'], 1)
        self.assertEqual(changed['replaced_sources'][0]['url'], self.old['url'])
        self.assertEqual(len(self.sync.sources(ACTOR)), 1)
        row = self.sync.all_projects(ACTOR)['items'][0]
        self.assertEqual(row['demand']['demand_no'], 'BMW-DAILY')
        self.assertNotEqual(row['demand']['id'], old_demand_id)
        self.assertEqual(self.p.detail(ACTOR, 'deliveries', delivery_id)['send_status'], 'CANCELED')
        archived = self.p.detail(ACTOR, 'demands', old_demand_id)
        self.assertEqual(archived['sync_state'], 'REMOVED')
        self.assertEqual(archived['status'], 'CLOSED')
        for action in (lambda: self.sync.run(ACTOR, self.old['id']),
                       lambda: self.sync.save(ACTOR, {'version': self.old['version']}, self.old['id'])):
            with self.assertRaises(Fault) as caught:
                action()
            self.assertEqual(caught.exception.code, 'SOURCE_REPLACED')
        self.assertTrue(self.p.integrity(ACTOR)['passed'])

    def test_unreadable_or_empty_new_table_keeps_old_source_and_pending_task(self):
        delivery_id = self.fixture.send(self.fixture.lead())['items'][0]['delivery_id']
        for result in ('FAIL', 'EMPTY'):
            def fetch(url):
                if result == 'FAIL':
                    raise QQSheetError('无读取权限')
                return {'title': '空表', 'headers': [], 'records': []}
            self.sync.fetcher = fetch
            with self.subTest(result=result), self.assertRaises(Fault) as caught:
                self.replace()
            self.assertEqual(caught.exception.code, 'SYNC_URL_UNREADABLE')
            self.assertEqual(self.sync.sources(ACTOR)[0]['url'], self.old['url'])
            self.assertEqual(self.p.detail(ACTOR, 'deliveries', delivery_id)['send_status'], 'QUEUED')
        self.assertTrue(self.p.integrity(ACTOR)['passed'])

    def test_bad_mapping_rolls_back_source_archive_and_quota_release(self):
        delivery_id = self.fixture.send(self.fixture.lead())['items'][0]['delivery_id']
        self.records['records'][0]['values']['下单量'] = '错误数量'
        with self.assertRaises(Fault) as caught:
            self.replace()
        self.assertEqual(caught.exception.code, 'SYNC_URL_REPLACEMENT_INVALID')
        self.assertEqual(self.sync.sources(ACTOR)[0]['id'], self.old['id'])
        self.assertEqual(self.p.detail(ACTOR, 'deliveries', delivery_id)['send_status'], 'QUEUED')
        self.assertTrue(self.p.integrity(ACTOR)['passed'])

    def test_sent_delivery_snapshot_is_preserved_after_change(self):
        delivery_id = self.fixture.send(self.fixture.lead())['items'][0]['delivery_id']
        self.p.worker(ACTOR)
        self.p.mock_callback(ACTOR, delivery_id, 'VALID')
        before = self.p.detail(ACTOR, 'deliveries', delivery_id)
        self.replace()
        after = self.p.detail(ACTOR, 'deliveries', delivery_id)
        self.assertEqual(before['demand_snapshot_public'], after['demand_snapshot_public'])
        self.assertEqual(after['validity_status'], 'VALID')
        self.assertEqual(after['demand_id'], before['demand_id'])
        self.assertTrue(self.p.integrity(ACTOR)['passed'])

    def test_can_restore_old_link_without_mixing_document_rows(self):
        changed = self.replace()
        restored = self.replace(changed, self.old['url'])
        self.assertEqual(restored['id'], self.old['id'])
        self.assertEqual(restored['url'], self.old['url'])
        self.assertEqual(len(self.sync.sources(ACTOR)), 1)
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 1)
        self.assertEqual(self.sync.all_projects(ACTOR)['items'][0]['source_id'], self.old['id'])

    def test_duplicate_active_target_and_wrong_tenant_are_rejected(self):
        target = self.sync.save(ACTOR, {'name': '另一来源', 'url': NEW_URL, 'partner_id': 'MEDIA_B',
                                       'media_id': 'MEDIA_B', 'sale_project': 'MEMBER', 'enabled': 0})
        with self.assertRaises(Fault) as caught:
            self.replace(self.old)
        self.assertEqual(caught.exception.code, 'SYNC_DUPLICATE')
        with self.assertRaises(Fault) as caught:
            self.sync.save({'id':'u_other','org_id':'other','role':'admin'},
                           {'version': self.old['version'], 'url': NEW_URL}, self.old['id'])
        self.assertEqual(caught.exception.code, 'NOT_FOUND')
        self.assertEqual(len(self.sync.sources(ACTOR)), 2)

    def test_read_has_no_write_lock_and_rechecks_version_after_fetch(self):
        def fetch(url):
            with self.p.tx() as conn:
                conn.execute('UPDATE demand_sync_sources SET version=version+1 WHERE id=?', (self.old['id'],))
            return copy.deepcopy(self.records)
        self.sync.fetcher = fetch
        with self.assertRaises(Fault) as caught:
            self.replace()
        self.assertEqual(caught.exception.code, 'VERSION_CONFLICT')
        self.assertEqual(self.sync.sources(ACTOR)[0]['url'], self.old['url'])

    def test_invalid_url_is_rejected_before_any_fetch(self):
        self.sync.fetcher = lambda url: self.fail('非法地址不应发起网络请求')
        with self.assertRaises(Fault) as caught:
            self.replace(url='https://127.0.0.1/private')
        self.assertEqual(caught.exception.code, 'SYNC_URL_INVALID')

    def test_existing_26_database_upgrades_without_changing_business_rows(self):
        with self.p.tx() as conn:
            conn.execute('DROP INDEX demand_sync_sources_replaced')
            conn.execute('ALTER TABLE demand_sync_sources DROP COLUMN replaced_by')
            conn.execute('DELETE FROM schema_version WHERE version=27')
            before = [tuple(row) for row in conn.execute('SELECT * FROM demand_sync_projects ORDER BY id')]
        for _ in range(2):
            upgraded = Platform(self.fixture.temp.name)
            conn = upgraded.connect()
            try:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_version WHERE version=27').fetchone()[0], 1)
                self.assertEqual(before, [tuple(row) for row in conn.execute('SELECT * FROM demand_sync_projects ORDER BY id')])
                self.assertIsNone(conn.execute('SELECT replaced_by FROM demand_sync_sources').fetchone()[0])
            finally:
                conn.close()

    def test_invalid_old_rows_are_retired_and_do_not_block_new_link_or_restore(self):
        self.fixture.data['records'][0]['values']['下单量'] = '旧表错误数量'
        self.sync.fetcher = lambda url: copy.deepcopy(self.fixture.data)
        self.sync.run(ACTOR, self.old['id'])
        self.sync.fetcher = lambda url: copy.deepcopy(self.records)
        changed = self.replace(self.sync.sources(ACTOR)[0])
        old_demand = self.p.detail(ACTOR, 'demands', self.fixture.d['id'])
        self.assertEqual(old_demand['sync_state'], 'REMOVED')
        self.assertEqual(old_demand['status'], 'CLOSED')
        self.assertEqual(len(self.sync.sources(ACTOR)), 1)
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 1)
        restored = self.replace(changed, self.old['url'])
        self.assertEqual(restored['id'], self.old['id'])
        self.assertTrue(self.p.integrity(ACTOR)['passed'])
