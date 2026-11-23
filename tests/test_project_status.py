"""会员/KA独立暂停、需求默认隐藏、历史回调与迁移兼容。"""
import copy
import json
import tempfile
import unittest

from domain import Fault, Platform
from demand_sync import DemandSync
from partner_edit import update_partner
from test_synced_demands import record

ACTOR = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}


class ProjectStatusTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.p = Platform(self.folder.name)
        self.p.seed()
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET status='CLOSED',sync_state='REMOVED'")
        self.sync = DemandSync(self.p, self.fetch)
        self.sources, self.demands = {}, {}
        for project in ('MEMBER', 'KA'):
            source = self.sync.save(ACTOR, {'name': project, 'url': f'https://docs.qq.com/sheet/DSTATUS{project}?tab=ONE',
                                          'partner_id': 'AGENCY_A', 'media_id': 'MEDIA_A', 'sale_project': project,
                                          'route_id': 'AGENCY_TO_A', 'enabled': 0})
            self.assertEqual(self.sync.run(ACTOR, source['id'])['status'], 'SUCCESS')
            self.sources[project] = source
            self.demands[project] = self.sync.projects(ACTOR, source['id'])['items'][0]['demand']
        self.sequence = 0

    @staticmethod
    def fetch(url):
        is_ka = 'STATUSKA' in url
        row = record(key='STATUS-KA' if is_ka else 'STATUS-MEMBER',
                     brand='奥迪' if is_ka else '宝马', model='奥迪 A6L' if is_ka else '宝马 X5',
                     regions='北京市' if is_ka else '上海市', qty='3')
        return {'title': '隔离项目需求', 'headers': list(row['values']), 'records': [row]}

    def config(self, project):
        conn = self.p.connect()
        try:
            return dict(conn.execute("SELECT * FROM partner_projects WHERE org_id='demo' AND partner_id='AGENCY_A' AND project=?", (project,)).fetchone())
        finally:
            conn.close()

    def status(self, project, enabled):
        return self.p.project_status_update(ACTOR, 'AGENCY_A', project,
                                            {'version': self.config(project)['version'], 'enabled': enabled})

    def lead(self, project):
        self.sequence += 1
        return self.p.create_lead(ACTOR, {'supplier_id':'SOURCE_A','name': '隔离项目启停', 'phone': f'19900009{self.sequence:03}',
                                         'brand_name': '奥迪' if project == 'KA' else '宝马',
                                         'model': '奥迪 A6L' if project == 'KA' else '宝马 X5',
                                         'city': '北京市' if project == 'KA' else '上海市', 'is_sold': '否'})['lead_id']

    def dispatch(self, project):
        demand = self.demands[project]
        result = self.p.dispatch(ACTOR, [(self.lead(project), demand['id'])], 'status-' + project,
                                 projects={demand['id']: project})
        self.assertEqual(result['created'], 1)
        return result['items'][0]['delivery_id']

    def test_ka_pause_does_not_pause_member_and_hides_all_default_views(self):
        member_version = self.config('MEMBER')['version']
        self.status('KA', 0)
        self.assertEqual(self.config('MEMBER')['enabled'], 1)
        self.assertEqual(self.config('MEMBER')['version'], member_version)
        self.assertEqual(self.config('KA')['enabled'], 0)
        party = next(p for p in self.p.catalogs(ACTOR)['partners'] if p['id'] == 'AGENCY_A')
        self.assertEqual(party['enabled'], 1)
        rows = self.sync.all_projects(ACTOR)
        self.assertEqual(rows['total'], 1)
        self.assertEqual(rows['items'][0]['source_id'], self.sources['MEMBER']['id'])
        self.assertEqual(rows['summary']['brand_count'], 1)
        self.assertEqual(rows['summary']['order_quantities']['daily'], 3)
        self.assertEqual(rows['filter_options']['brand'], [{'value': '宝马', 'label': '宝马'}])
        self.assertEqual(rows['source_ids'], [self.sources['MEMBER']['id']])
        self.assertEqual(self.p.list(ACTOR, 'demands')['total'], 1)
        groups = [g for g in self.p.demand_summary(ACTOR)['items'] if g['buyer_id'] == 'AGENCY_A']
        self.assertTrue(groups)
        self.assertTrue(all(g['project'] != 'KA' for g in groups))
        self.assertEqual(self.p.reports(ACTOR)['overview']['total_demands'], 1)
        self.assertEqual(self.p.matching_leads(ACTOR, buyer_ids=['AGENCY_A|KA'])['total'], 0)
        lead_id = self.lead('KA')
        preview = self.p.preview(ACTOR, [lead_id], [self.demands['KA']['id']])
        self.assertEqual(preview['items'][0]['reason'], 'PROJECT_PAUSED')
        self.assertEqual(self.p.dispatch(ACTOR, [(lead_id, self.demands['KA']['id'])], 'blocked-ka')['created'], 0)
        metadata = next(s for s in self.sync.sources(ACTOR) if s['id'] == self.sources['KA']['id'])
        self.assertFalse(metadata['project_enabled'])
        self.assertFalse(metadata['available'])

    def test_pause_only_cancels_ka_unsent_and_resume_preserves_raw_demand(self):
        member_delivery, ka_delivery = self.dispatch('MEMBER'), self.dispatch('KA')
        before = self.sync.detail(ACTOR, self.sync.projects(ACTOR, self.sources['KA']['id'])['items'][0]['id'])['values']
        self.status('KA', 0)
        self.assertEqual(self.p.detail(ACTOR, 'deliveries', member_delivery)['send_status'], 'QUEUED')
        self.assertEqual(self.p.detail(ACTOR, 'deliveries', ka_delivery)['send_status'], 'CANCELED')
        self.assertTrue(self.p.integrity(ACTOR)['passed'])
        self.assertEqual(self.sync.run(ACTOR, self.sources['KA']['id'])['status'], 'SUCCESS')
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 1)
        self.status('KA', 1)
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 2)
        restored = self.sync.projects(ACTOR, self.sources['KA']['id'])['items'][0]
        self.assertEqual(restored['demand']['id'], self.demands['KA']['id'])
        self.assertEqual(restored['values'], before)
        self.assertEqual(self.p.detail(ACTOR, 'deliveries', ka_delivery)['send_status'], 'CANCELED')

    def test_sent_callback_survives_pause(self):
        delivery_id = self.dispatch('KA')
        self.p.worker(ACTOR)
        snapshot = self.p.detail(ACTOR, 'deliveries', delivery_id)['demand_snapshot_public']
        self.status('KA', 0)
        self.p.mock_callback(ACTOR, delivery_id, 'VALID')
        detail = self.p.detail(ACTOR, 'deliveries', delivery_id)
        self.assertEqual(detail['validity_status'], 'VALID')
        self.assertEqual(snapshot, detail['demand_snapshot_public'])
        self.assertTrue(self.p.integrity(ACTOR)['passed'])

    def test_master_switch_is_explicit_and_does_not_reset_project_switches(self):
        self.status('KA', 0)
        update_partner(self.p, ACTOR, 'AGENCY_A', {'enabled': 0})
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 0)
        self.assertEqual(self.p.list(ACTOR, 'demands')['total'], 0)
        update_partner(self.p, ACTOR, 'AGENCY_A', {'enabled': 1})
        self.assertEqual(self.sync.all_projects(ACTOR)['total'], 1)
        self.assertEqual(self.config('KA')['enabled'], 0)
        self.assertEqual(self.config('MEMBER')['enabled'], 1)

    def test_permissions_version_and_paused_project_config(self):
        for actor, code in [({'id':'u_operator','org_id':'demo','role':'operator'}, 'FORBIDDEN'),
                            ({'id':'u_other','org_id':'other','role':'admin'}, 'NOT_FOUND')]:
            with self.subTest(code=code), self.assertRaises(Fault) as caught:
                self.p.project_status_update(actor, 'AGENCY_A', 'KA', {'version':1,'enabled':0})
            self.assertEqual(caught.exception.code, code)
        old_version = self.config('KA')['version']
        self.status('KA', 0)
        with self.assertRaises(Fault) as caught:
            self.p.project_status_update(ACTOR, 'AGENCY_A', 'KA', {'version':old_version,'enabled':1})
        self.assertEqual(caught.exception.code, 'VERSION_CONFLICT')
        with self.assertRaises(Fault) as caught:
            self.sync.save(ACTOR, {'version':self.sources['KA']['version'],'name':'不能隐式启用'}, self.sources['KA']['id'])
        self.assertEqual(caught.exception.code, 'PROJECT_PAUSED')
        with self.assertRaises(Fault) as caught:
            self.p.project_mode_update(ACTOR, 'AGENCY_A', 'KA', {'version':self.config('KA')['version'],'matching_mode':'AUTO'})
        self.assertEqual(caught.exception.code, 'PROJECT_PAUSED')

    def test_migration_preserves_global_stop_and_runs_once(self):
        update_partner(self.p, ACTOR, 'AGENCY_A', {'enabled': 0})
        with self.p.tx() as conn:
            conn.execute('ALTER TABLE partner_projects DROP COLUMN enabled')
            conn.execute('DELETE FROM schema_version WHERE version=28')
        for _ in range(2):
            upgraded = Platform(self.folder.name)
            conn = upgraded.connect()
            try:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_version WHERE version=28').fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT enabled FROM partners WHERE id='AGENCY_A'").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT enabled FROM partner_projects WHERE partner_id='AGENCY_A' AND project='MEMBER'").fetchone()[0], 1)
            finally:
                conn.close()
