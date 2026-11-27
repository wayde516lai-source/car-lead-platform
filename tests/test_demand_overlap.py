"""临时数据库验证重合分组、媒体去重、共享配额和组织/字段权限。"""
import unittest

from demand_overlap import analyze
import test_distribution as fixtures
from domain import later

A = fixtures.A


class DemandOverlapTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DistributionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.p = self.fixture.p
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET status='CLOSED'")
            for media, quantity in [('MEDIA_A', 100), ('MEDIA_B', 80)]:
                conn.execute("UPDATE demands SET status='ACTIVE',quantity_basis='DAILY',target_total=?,daily_limit=?,sale_project='MEMBER',city_codes='[\"上海市\"]' WHERE id=?", (quantity, quantity, self.fixture.demands[media]['id']))

    def test_two_media_show_overlap_and_separate_independent_leads(self):
        result = analyze(self.p, A)
        self.assertEqual(result['total'], 1)
        row = result['items'][0]
        self.assertEqual((row['media_count'], row['daily_quantity'], row['common_quantity']), (2, 180, 80))
        self.assertEqual(row['unique_reference'], max(detail['remaining'] for detail in row['details']))
        self.assertEqual(row['inventory'], 1)
        self.assertIsNone(row['suggested'])
        self.assertEqual(len(row['details']), 2)

    def test_same_media_different_routes_not_double_counted(self):
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET media_id='MEDIA_A' WHERE id=?", (self.fixture.demands['MEDIA_B']['id'],))
        row = analyze(self.p, A)['items'][0]
        self.assertEqual((row['media_count'], row['daily_quantity'], row['common_quantity']), (1, 100, 0))

    def test_ended_foreign_and_unselected_projects_not_counted(self):
        with self.p.tx() as conn:
            conn.execute('UPDATE demands SET end_at=? WHERE id=?', (later(hours=-1), self.fixture.demands['MEDIA_A']['id']))
        self.assertEqual(analyze(self.p, A)['total'], 0)
        self.assertEqual(analyze(self.p, A, overlap_only=False)['total'], 1)
        self.assertEqual(analyze(self.p, A, project='KA')['total'], 0)
        self.assertEqual(analyze(self.p, {'id': 'other', 'org_id': 'other', 'role': 'admin'})['total'], 0)

    def test_city_scope_does_not_multiply_order_and_shared_note_is_visible(self):
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET city_codes='[\"上海市\",\"北京市\"]' WHERE id=?", (self.fixture.demands['MEDIA_A']['id'],))
        result = analyze(self.p, A, overlap_only=False)
        self.assertEqual(result['total'], 2)
        self.assertTrue(any(detail['shared_scope'] for row in result['items'] for detail in row['details']))
        selected = analyze(self.p, A, query='北京', overlap_only=False)
        self.assertEqual(selected['total'], 1)
        self.assertEqual(selected['items'][0]['daily_quantity'], 100)

    def test_hidden_lead_fields_prevent_inventory_inference(self):
        self.p.update_lead_fields(A, 'u_operator', ['lead_no'])
        result = analyze(self.p, {'id': 'u_operator', 'org_id': 'demo', 'role': 'operator'})
        self.assertFalse(result['inventory_visible'])
        self.assertIsNone(result['items'][0]['inventory'])

    def test_execution_blocks_explained_without_reserving_or_sending(self):
        with self.p.tx() as conn:
            conn.execute('UPDATE settings SET dispatch_enabled=0')
            before = conn.execute('SELECT count(*) FROM deliveries').fetchone()[0]
        result = analyze(self.p, A)
        self.assertEqual(result['items'][0]['inventory'], 0)
        self.assertTrue(all('DISPATCH_PAUSED' in detail['block_reasons'] for detail in result['items'][0]['details']))
        with self.p.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM deliveries').fetchone()[0], before)

    def test_nationwide_and_all_series_cover_explicit_model_city_without_multiplying(self):
        from test_synced_demands import SyncedDemandTests, record
        fixture = SyncedDemandTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        with fixture.p.tx() as conn:
            conn.execute("UPDATE demands SET status='CLOSED' WHERE external_project_id IS NULL")
        fixture.data['records'] = [record(key='national', model='全系', regions='全国', qty='100'),
                                   record(key='specific', model='宝马 X5', regions='上海市', qty='80')]
        fixture.run_sync()
        result = analyze(fixture.p, A)
        self.assertEqual(result['total'], 1)
        row = result['items'][0]
        self.assertEqual(row['city'], '上海')
        self.assertEqual((row['daily_quantity'], row['media_count']), (100, 1))
        self.assertTrue(any(detail['shared_scope'] for detail in row['details']))

    def test_more_than_twenty_thousand_groups_are_sorted_and_paginated_without_cutoff(self):
        from test_synced_demands import SyncedDemandTests, record
        fixture = SyncedDemandTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        with fixture.p.tx() as conn:
            conn.execute("UPDATE demands SET status='CLOSED' WHERE external_project_id IS NULL")
            conn.executemany('INSERT INTO catalog VALUES(?,?,?,?)',
                [(f'large-model-{i}', 'large-brand', '范围测试品牌', f'范围车型{i:03}') for i in range(160)])
        cities = ','.join(f'范围城市{i:03}' for i in range(160))
        fixture.data['records'] = [record(key='large-one', brand='范围测试品牌', model='全系', regions=cities, qty='100'),
                                   record(key='large-two', brand='范围测试品牌', model='全系', regions=cities, qty='80')]
        fixture.run_sync()
        with fixture.p.connect() as conn:
            before = conn.execute('SELECT count(*) FROM demands').fetchone()[0]
        first = analyze(fixture.p, A, page_size=20)
        second = analyze(fixture.p, A, page=2, page_size=20)
        self.assertEqual(first['total'], 25600)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(first['items']), 20)
        self.assertFalse({(row['model'], row['city']) for row in first['items']} &
                         {(row['model'], row['city']) for row in second['items']})
        self.assertTrue(all(row['daily_quantity'] == 100 and row['demand_count'] == 2 for row in first['items']))
        filtered = analyze(fixture.p, A, query='范围车型159')
        self.assertEqual(filtered['total'], 160)
        self.assertEqual(filtered['items'][0]['model'], '范围车型159')
        with fixture.p.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM demands').fetchone()[0], before)
