"""需求来源更正原子更新，跨主体同名外部编号不混账。"""
import copy
import json
import unittest

from domain import Fault
import test_synced_demands as fixture_module

ACTOR = fixture_module.A


class SourceReassignmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.SyncedDemandTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.p, self.sync = self.fixture.p, self.fixture.sync

    def move(self, **extra):
        source = self.sync.sources(ACTOR)[0]
        return self.sync.save(ACTOR, {'version': source['version'], 'partner_id': 'AGENCY_A',
                                     'route_id': 'AGENCY_TO_A', **extra}, source['id'])

    def test_move_keeps_ids_and_cancels_unsent_delivery(self):
        lead = self.fixture.lead()
        delivery = self.fixture.send(lead)['items'][0]['delivery_id']
        demand_id = self.fixture.d['id']
        source = self.move()
        self.assertEqual(source['partner_id'], 'AGENCY_A')
        self.assertEqual(self.p.detail(ACTOR, 'demands', demand_id)['buyer_id'], 'AGENCY_A')
        self.assertEqual(self.p.detail(ACTOR, 'deliveries', delivery)['send_status'], 'CANCELED')
        self.assertTrue(self.p.integrity(ACTOR)['passed'])
        with self.assertRaises(Fault) as caught:
            self.sync.save(ACTOR, {'version': source['version'] - 1}, source['id'])
        self.assertEqual(caught.exception.code, 'VERSION_CONFLICT')

    def test_sent_source_cannot_change_owner_but_can_rename(self):
        delivery = self.fixture.send(self.fixture.lead())['items'][0]['delivery_id']
        self.p.worker(ACTOR)
        before = self.p.detail(ACTOR, 'deliveries', delivery)['demand_snapshot_public']
        with self.assertRaises(Fault) as caught:
            self.move()
        self.assertEqual(caught.exception.code, 'SOURCE_ASSIGNMENT_LOCKED')
        source = self.sync.sources(ACTOR)[0]
        renamed = self.sync.save(ACTOR, {'version': source['version'], 'name': '更正来源显示名'}, source['id'])
        self.assertEqual(renamed['partner_id'], 'MEDIA_A')
        self.assertEqual(before, self.p.detail(ACTOR, 'deliveries', delivery)['demand_snapshot_public'])

    def create_second(self):
        data = copy.deepcopy(self.fixture.data)
        self.sync.fetcher = lambda url: copy.deepcopy(data)
        source = self.sync.save(ACTOR, {'name': '另一乙方', 'url': 'https://docs.qq.com/sheet/DANOTHEROWNER?tab=BB08J2',
                                      'partner_id': 'AGENCY_A', 'media_id': 'MEDIA_A', 'route_id': 'AGENCY_TO_A',
                                      'sale_project': 'MEMBER', 'enabled': 0})
        result = self.sync.run(ACTOR, source['id'])
        self.assertEqual(result['demands']['errors'], 0)
        return source

    def test_cross_owner_same_number_preserved_in_display_and_payload(self):
        source = self.create_second()
        rows = self.sync.all_projects(ACTOR)['items']
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['demand']['demand_no'] for row in rows}, {'BMW-DAILY'})
        target = next(row['demand'] for row in rows if row['source_id'] == source['id'])
        result = self.p.dispatch(ACTOR, [(self.fixture.lead(), target['id'])], 'same-external-number',
                                 projects={target['id']: 'MEMBER'})
        self.assertEqual(result['created'], 1)
        conn = self.p.connect()
        try:
            cipher = conn.execute('SELECT payload_cipher FROM deliveries WHERE id=?',
                                  (result['items'][0]['delivery_id'],)).fetchone()[0]
            self.assertEqual(json.loads(self.p.vault.decrypt(cipher))['demand_no'], 'BMW-DAILY')
        finally:
            conn.close()

    def test_conflicting_reassignment_rolls_back_entire_source(self):
        source = self.create_second()
        with self.assertRaises(Fault) as caught:
            self.sync.save(ACTOR, {'version': source['version'], 'partner_id': 'MEDIA_A', 'route_id': 'DIRECT_A'}, source['id'])
        self.assertEqual(caught.exception.code, 'SOURCE_REASSIGN_FAILED')
        self.assertEqual(next(s for s in self.sync.sources(ACTOR) if s['id'] == source['id'])['partner_id'], 'AGENCY_A')

    def test_excel_metadata_edit_does_not_enable_sync_and_tenant_isolation(self):
        source = self.sync.sources(ACTOR)[0]
        with self.p.tx() as conn:
            conn.execute("UPDATE demand_sync_sources SET source_kind='EXCEL' WHERE id=?", (source['id'],))
        changed = self.move()
        self.assertEqual(changed['enabled'], 0)
        with self.assertRaises(Fault) as caught:
            self.sync.save(ACTOR, {'version': changed['version'], 'enabled': 1}, source['id'])
        self.assertEqual(caught.exception.code, 'FILE_SOURCE_NO_SYNC')
        with self.assertRaises(Fault) as caught:
            self.sync.save({'id':'u_other','role':'admin','org_id':'other'}, {'version': changed['version']}, source['id'])
        self.assertEqual(caught.exception.code, 'NOT_FOUND')
