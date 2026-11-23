"""KA需求API人工拉取、全量更新及提交上限，隔离数据库和模拟网络。"""
import copy
import tempfile
import unittest
from unittest.mock import patch

from domain import Fault, Platform, day, later, now
from demand_sync import DemandSync
from ka_demand_api import DemandProtocolError, parse_response
from ka_demand_sources import KaDemandSources, submission_usage
from test_ka_demand_api import sample

ACTOR = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}


class KaDemandSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = Platform(self.temp.name)
        self.p.seed()
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET status='CLOSED',sync_state='REMOVED'")
        self.payload = sample()
        row = self.payload['message']['data'][0]
        row.update(day=int(day().replace('-', '')), BeginTime='2020-01-01 00:00:00',
                   EndTime='2099-12-31 23:59:59')
        row['ListPro'] = [{'ListCity': [{'CityId': 1, 'CityName': '上海市'}, {'CityId': 2, 'CityName': '北京市'}]}]
        row['ListCmb'] = [{'CmbId': 1, 'CmbName': '宝马', 'ListCmk': [
            {'CmkId': 1, 'CmkName': '华晨宝马', 'ListCmd': [{'CmdId': 1, 'CmdName': '宝马 X5'}]}]},
            {'CmbId': 2, 'CmbName': '奥迪', 'ListCmk': [
                {'CmkId': 2, 'CmkName': '一汽奥迪', 'ListCmd': [{'CmdId': 2, 'CmdName': '奥迪 A6L'}]}]}]
        self.sync = DemandSync(self.p, api_fetcher=lambda *args: parse_response(self.payload))
        self.service = KaDemandSources(self.sync)
        self.config = {'name': '虚构KA接口', 'partner_id': 'AGENCY_A', 'media_id': 'MEDIA_A',
                       'sale_project': 'KA', 'route_id': 'AGENCY_TO_A', 'auth_mode': 'NONE'}
        self.source = self.service.save(ACTOR, self.config)
        self.sequence = 0

    def run_source(self):
        return self.sync.run(ACTOR, self.source['id'])

    def demand(self):
        conn = self.p.connect()
        try:
            return dict(conn.execute('''SELECT d.* FROM demands d JOIN demand_sync_projects p
                ON p.id=d.external_project_id WHERE p.source_id=?''', (self.source['id'],)).fetchone())
        finally:
            conn.close()

    def lead(self, brand='宝马', model='宝马 X5', city='上海市'):
        self.sequence += 1
        return self.p.create_lead(ACTOR, {'supplier_id':'SOURCE_A','name': '接口隔离测试', 'phone': f'19900008{self.sequence:03}',
                                          'brand_name': brand, 'model': model, 'city': city,
                                          'is_sold': '否'})['lead_id']

    def dispatch(self, lead_id=None):
        dm = self.demand()
        return self.p.dispatch(ACTOR, [(lead_id or self.lead(), dm['id'])], 'ka-test-' + str(self.sequence),
                               projects={dm['id']: 'KA'})

    def test_full_refresh_identity_history_missing_and_empty(self):
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        first = self.demand()
        self.assertEqual(first['api_submit_limit'], 2)
        self.assertEqual(first['api_monthly_limit'], 62)
        self.assertEqual(self.run_source()['unchanged'], 1)
        self.payload['message']['data'][0]['LimitOfDay'] = 3
        self.assertEqual(self.run_source()['updated'], 1)
        self.assertEqual(self.demand()['id'], first['id'])
        self.assertEqual(self.demand()['api_submit_limit'], 3)
        self.payload['message']['data'] = []
        self.assertEqual(self.run_source()['removed'], 1)
        self.assertEqual(self.demand()['sync_state'], 'REMOVED')
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 0)
        self.payload = sample()
        self.payload['message']['data'][0]['day'] = 19990101
        self.assertEqual(self.run_source()['status'], 'FAILED')
        self.assertEqual(self.demand()['sync_state'], 'REMOVED')

    def test_multi_brand_model_city_pairs_share_one_quota(self):
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        self.assertEqual(self.dispatch()['created'], 1)
        audi = self.lead('奥迪', '奥迪 A6L', '北京市')
        self.assertEqual(self.dispatch(audi)['created'], 1)
        self.assertEqual(self.dispatch()['created'], 0)
        conn = self.p.connect()
        try:
            self.assertEqual(submission_usage(conn, self.demand()['id']), (2, 0))
        finally:
            conn.close()

    def test_brand_filter_preserves_pairs_and_counts_brands_once(self):
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        result = self.sync.all_projects(ACTOR)
        self.assertEqual(result['summary']['brand_count'], 2)
        self.assertEqual({x['value'] for x in result['filter_options']['brand']}, {'宝马', '奥迪'})
        selected = self.sync.all_projects(ACTOR, filters={'brand': '宝马'})
        self.assertEqual([x['value'] for x in selected['filter_options']['model']], ['宝马 X5'])
        self.assertEqual(self.sync.all_projects(ACTOR, filters={'brand': '宝马', 'model': '奥迪'})['total'], 0)
        self.assertEqual(self.p.reports(ACTOR)['overview']['brand_count'], 2)
        self.payload['message']['data'][0]['LimitOfDay'] = 0
        self.run_source()
        self.assertEqual(self.sync.all_projects(ACTOR)['summary']['order_quantities']['invalid_rows'], 0)
        self.assertEqual(self.p.reports(ACTOR)['overview']['order_quantities']['daily'], 0)

    def test_ka_cannot_use_existing_member_transport(self):
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        with self.p.tx() as conn:
            conn.execute("UPDATE routes SET adapter_version='zhemanhui-v1' WHERE id='AGENCY_TO_A'")
        result = self.p.preview(ACTOR, [self.lead()], [self.demand()['id']],
                                projects={self.demand()['id']: 'KA'})
        self.assertIn('KA_DELIVERY_NOT_CONFIGURED', str(result))
        self.assertEqual(self.dispatch()['created'], 0)

    def test_failed_sent_still_counts_and_monthly_not_cap(self):
        self.payload['message']['data'][0].update(limit=0, LimitOfDay=1)
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        result = self.dispatch()
        delivery = result['items'][0]['delivery_id']
        self.p.worker(ACTOR)
        with self.p.tx() as conn:
            # 模拟最终无效释放业务配额，提交次数仍不可补回。
            row = dict(conn.execute('SELECT * FROM deliveries WHERE id=?', (delivery,)).fetchone())
            if row['quota_state'] == 'RESERVED':
                self.p._ledger(conn, delivery, 'api-invalid-test', -1, 0, 'TEST_INVALID')
            conn.execute("UPDATE deliveries SET quota_state='RELEASED',validity_status='INVALID' WHERE id=?", (delivery,))
        self.assertEqual(self.dispatch()['created'], 0)
        summary = self.p.demand_summary(ACTOR)['items']
        group = next(row for row in summary if row['buyer_id'] == 'AGENCY_A' and row['project'] == 'KA')
        self.assertEqual(group['available_count'], 0)

    def test_updated_limit_cancels_unsent_and_preserves_sent_usage(self):
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        self.dispatch()
        self.p.worker(ACTOR)
        self.dispatch()
        self.payload['message']['data'][0]['LimitOfDay'] = 1
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        conn = self.p.connect()
        try:
            self.assertEqual(submission_usage(conn, self.demand()['id']), (0, 1))
        finally:
            conn.close()
        self.assertEqual(self.dispatch()['created'], 0)
        self.payload['message']['data'][0]['LimitOfDay'] = 2
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        self.assertEqual(self.dispatch()['created'], 1)

    def test_zero_stale_and_next_day_no_auto_refresh(self):
        self.payload['message']['data'][0]['LimitOfDay'] = 0
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        self.assertEqual(self.dispatch()['created'], 0)
        row = self.sync.all_projects(ACTOR)['items'][0]['demand']
        self.assertEqual(row['target_total'], 0)
        self.payload['message']['data'][0]['LimitOfDay'] = 2
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        with patch('domain.day', return_value='2099-01-01'):
            self.assertEqual(self.dispatch()['created'], 0)
        with patch.object(self.sync, 'api_fetcher', side_effect=AssertionError('must not poll')):
            self.sync.tick()

    def test_failures_are_atomic_and_credentials_private(self):
        self.assertEqual(self.run_source()['status'], 'SUCCESS')
        old = self.demand()
        self.payload['message']['data'][0]['ListPro'] = 'bad'
        self.assertEqual(self.run_source()['status'], 'FAILED')
        self.assertEqual(self.demand(), old)
        saved = self.service.save(ACTOR, {**self.config, 'version': self.source['version'],
                                  'auth_mode': 'BEARER', 'bearer_token': 'fake-secret-only-test',
                                  'url': 'https://yiche.asoapple.cn/api/v2/pullKaCrid'}, self.source['id'])
        self.assertTrue(saved['has_token'])
        self.assertNotIn('fake-secret', str(saved))
        self.assertNotIn('token_cipher', str(saved))
        with self.assertRaises(Fault):
            self.service.save(ACTOR, {**self.config, 'version': saved['version'], 'sale_project': 'MEMBER'}, saved['id'])
        with self.assertRaises(Fault):
            self.service.save({**ACTOR, 'org_id': 'other'}, self.config, saved['id'])
        with self.assertRaises(Fault):
            self.service.save({**ACTOR, 'role': 'viewer'}, self.config)
        with self.assertRaises(Fault):
            self.service.save(ACTOR, {**self.config, 'version': saved['version'], 'url': 'http://127.0.0.1/private'}, saved['id'])

class DailyScheduleTests(unittest.TestCase):
    def test_next_eight_am_shanghai_and_manual_refresh_keeps_clock(self):
        from ka_demand_sources import next_daily_pull
        cases = [('2026-11-23T23:59:00+00:00', '2026-11-24T00:00:00+00:00'),
                 ('2026-11-24T00:00:00+00:00', '2026-11-25T00:00:00+00:00'),
                 ('2026-11-24T02:00:00+00:00', '2026-11-25T00:00:00+00:00')]
        for current, expected in cases:
            with self.subTest(current=current), patch('ka_demand_sources.now', return_value=current):
                self.assertEqual(next_daily_pull(), expected)

    def test_due_api_is_pulled_once_and_rescheduled(self):
        fixture = KaDemandSourceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.p.tx() as conn:
            conn.execute('UPDATE demand_sync_sources SET next_run_at=? WHERE id=?',
                         (later(hours=-1), fixture.source['id']))
        with patch.object(fixture.sync, 'api_fetcher', wraps=fixture.sync.api_fetcher) as fetch:
            fixture.sync.tick()
            fixture.sync.tick()
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fixture.sync.runs(ACTOR, fixture.source['id'])[0]['trigger_kind'], 'scheduled')
        self.assertEqual(fixture.demand()['api_submit_limit'], 2)

    def test_migration_is_once_and_existing_demands_unchanged(self):
        fixture = KaDemandSourceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.p.tx() as conn:
            conn.execute('DELETE FROM ka_demand_sources')
            conn.execute('DROP TABLE ka_demand_sources')
            for column in ('api_submit_limit','api_quota_day','api_monthly_limit','api_vehicles'):
                conn.execute('ALTER TABLE demands DROP COLUMN '+column)
            conn.execute('DELETE FROM schema_version WHERE version=29')
            before = [tuple(row) for row in conn.execute('SELECT * FROM demands ORDER BY id')]
        Platform(fixture.temp.name)
        Platform(fixture.temp.name)
        conn = fixture.p.connect()
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_version WHERE version=29').fetchone()[0], 1)
            rows = [tuple(row)[:-4] for row in conn.execute('SELECT * FROM demands ORDER BY id')]
            self.assertEqual(rows, before)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
