"""结束项目展示隔离、执行天数与每日下单量排行榜。"""
import unittest
from unittest.mock import patch

from demand_statistics import execution_days, daily_demand_ranking
import test_synced_demands as fixtures

A = fixtures.A


class DemandPoolOverviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SyncedDemandTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.p, self.sync = self.fixture.p, self.fixture.sync

    def test_ended_hidden_before_paging_options_and_summary_but_history_remains(self):
        self.fixture.data['records'] = [fixtures.record(key='live', qty='3'), fixtures.record(key='ended', brand='奥迪', qty='999', state='结束')]
        self.fixture.run_sync()
        listing = self.sync.all_projects(A)
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['summary']['order_quantities']['daily'], 3)
        self.assertEqual(listing['filter_options']['brand'], [{'value': '宝马', 'label': '宝马'}])
        self.assertEqual(self.sync.projects(A, self.fixture.source['id'])['total'], 2)
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET status='CLOSED' WHERE external_project_id IS NOT NULL")
        self.assertEqual(self.sync.all_projects(A)['total'], 0)
        self.assertFalse(any(row['status'] in ('CLOSED', 'COMPLETED') for row in self.p.demand_summary(A)['items']))

    def test_execution_days_uses_start_and_current_time(self):
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET start_at='2026-11-20T00:00:00+00:00' WHERE id=?", (self.fixture.d['id'],))
        with patch('demand_sync.now', return_value='2026-11-26T05:00:00+00:00'):
            self.assertEqual(self.sync.all_projects(A)['items'][0]['execution_days'], 6)
        self.assertEqual(execution_days('2026-11-26T00:00:00+08:00', '2026-11-26T00:00:00+00:00'), 0)
        self.assertEqual(execution_days('2027-01-01T00:00:00+00:00', '2026-11-26T00:00:00+00:00'), 0)
        self.assertIsNone(execution_days('', '2026-11-26T00:00:00+00:00'))

    def test_ranking_is_top_ten_daily_quantity_not_monthly_total(self):
        base = {'id': '', 'status': 'ACTIVE', 'sync_state': '', 'end_at': '2099-01-01T00:00:00+00:00',
                'quantity_basis': 'DAILY', 'target_total': 0, 'daily_limit': 1, 'api_vehicles': '[]',
                'brand': '测试品牌', 'match_models': '["车型一","车型二"]', 'series_name': '',
                'match_regions': '["上海市","北京市"]', 'city_codes': '[]', 'external_demand_no': '', 'demand_no': '001'}
        rows = [dict(base, id=f'd-{number:02}', target_total=number) for number in range(1, 15)]
        rows += [dict(base, id='closed', target_total=999, status='CLOSED'),
                 dict(base, id='monthly', quantity_basis='TOTAL', target_total=999999, daily_limit=2)]
        result = daily_demand_ranking(rows, '2026-11-26T00:00:00+00:00')
        self.assertEqual([row['daily_quantity'] for row in result], list(range(14, 4, -1)))
        self.assertEqual(result[0]['model'], '车型一 / 车型二')
        self.assertEqual(result[0]['city'], '上海 / 北京')
        self.assertEqual(result[0]['demand_no'], '001')

    def test_report_ranking_excludes_foreign_and_disabled_project(self):
        report = self.p.reports(A)['overview']['daily_top_demands']
        self.assertTrue(any(row['id'] == self.fixture.d['id'] for row in report))
        self.assertEqual(self.p.reports({'id': 'other', 'org_id': 'other', 'role': 'admin'})['overview']['daily_top_demands'], [])
        with self.p.tx() as conn:
            conn.execute("UPDATE partner_projects SET enabled=0 WHERE org_id='demo'")
        self.assertEqual(self.p.reports(A)['overview']['daily_top_demands'], [])
