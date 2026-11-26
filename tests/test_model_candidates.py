"""车型候选、协议所需字典编码以及回调原因的临时库回归。"""
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from domain import Fault
from media_catalog_matching import delivery_media_codes, resolve_media_codes
from synced_demands import model_name
import test_media_catalogs as catalogs
from test_media_catalogs import add_library
import test_matching_pool as matching
import test_zhemanhui_integration as zmh


class ModelCandidateTests(unittest.TestCase):
    def setUp(self):
        fixture = matching.MatchingPoolTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.p, self.create, self.pool = fixture.p, fixture.create, fixture.pool

    def test_symbols_are_ignored_but_model_numbers_and_letters_are_preserved(self):
        self.assertEqual(model_name('铂·智（７） / EV', ''), '铂智7ev')
        self.assertNotEqual(model_name('铂智7', ''), model_name('铂智8', ''))
        with self.p.tx() as conn:
            conn.execute("UPDATE catalog SET brand_name='厂商不同',series_name=' X / ５ ' WHERE series_id='BMW_X5'")
            conn.execute("INSERT INTO catalog VALUES('OTHER_X5','OTHER','不同品牌','X·5')")
        lead = self.create(series_id='OTHER_X5', brand_name='不同品牌')
        self.assertIn(lead, self.pool()['filtered_ids'])
        demand = next(row for row in self.p.list(A, 'demands')['items'] if row['route_id'] == 'DIRECT_B')
        self.assertTrue(self.p.preview(A, [lead], [demand['id']], {demand['id']: 'MEMBER'})['items'][0]['eligible'])

    def test_city_and_quota_filter_candidates_before_preview(self):
        lead = self.create(city='北京市')
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET city_codes='[\"310100\"]'")
        self.assertNotIn(lead, self.pool()['filtered_ids'])
        good = self.create()
        self.assertIn(good, self.pool()['filtered_ids'])
        with self.p.tx() as conn:
            conn.execute('UPDATE demands SET reserved_count=target_total')
        self.assertNotIn(good, self.pool()['filtered_ids'])

    def test_sending_switch_still_hides_candidate_and_blocks_creation(self):
        lead = self.create()
        with self.p.tx() as conn:
            conn.execute('UPDATE settings SET dispatch_enabled=0')
        self.assertNotIn(lead, self.pool()['filtered_ids'])
        demand = next(row for row in self.p.list(A, 'demands')['items'] if row['route_id'] == 'DIRECT_B')
        result = self.p.dispatch(A, [(lead, demand['id'])], 'blocked-candidate', projects={demand['id']: 'MEMBER'})
        self.assertEqual(result['created'], 0)


class RequiredMediaCodesTests(unittest.TestCase):
    def setUp(self):
        fixture = catalogs.CatalogDeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.p, self.lead_id = fixture.p, fixture.lead_id
        self.source_fixture = fixture.fixture

    def test_model_resolves_media_brand_and_factory_without_original_brand_gate(self):
        with self.p.tx() as conn:
            add_library(conn)
            conn.execute("UPDATE catalog SET brand_name='原始厂商',series_name='X / 5' WHERE series_id=?", (self.p.one(conn, 'leads', self.lead_id, A)['series_id'],))
            conn.execute("UPDATE media_vehicle_items SET brand_name='媒体品牌',factory_name='媒体厂商',series_name='X·5'")
            codes = resolve_media_codes(conn, 'demo', 'MEDIA_A', self.p.one(conn, 'leads', self.lead_id, A), adapter_version='zhemanhui-v1')
            self.assertEqual((codes['series_id'], codes['brand_name'], codes['factory_name']), (701, '媒体品牌', '媒体厂商'))

    def test_create_and_send_use_media_codes_despite_different_source_brand(self):
        with self.p.tx() as conn:
            add_library(conn)
            lead = self.p.one(conn, 'leads', self.lead_id, A)
            conn.execute("UPDATE catalog SET brand_name='广汽丰田',series_name='铂智（７）' WHERE series_id=?", (lead['series_id'],))
            conn.execute("UPDATE demands SET match_brand='丰田',match_models='[\"铂智·7\"]',all_models=0 WHERE id=?", (self.source_fixture.d['id'],))
            conn.execute("UPDATE media_vehicle_items SET brand_id=7,brand_name='丰田',factory_id=20387,factory_name='广汽丰田·新能源',series_id=11355,series_name='铂智7'")
        self.assertIn(self.lead_id, self.p.matching_leads(A)['filtered_ids'])
        result = self.source_fixture.send(self.lead_id)
        self.assertEqual(result['created'], 1)
        requests = []
        def send(request, **kwargs):
            requests.append(request)
            return {'status': 'ACKED', 'validity': 'PENDING'}
        with patch('zhemanhui.send_test', side_effect=send):
            self.p.worker(A)
        params = parse_qs(urlsplit(requests[0].full_url).query)
        self.assertEqual(params['serialId'], ['11355'])
        self.assertEqual(params['cityId'], ['2501'])
        self.assertNotIn('manufacturerId', params)
        self.assertNotIn('brandId', params)

    def test_other_media_cannot_supply_missing_target_model(self):
        with self.p.tx() as conn:
            add_library(conn, media='MEDIA_B')
            add_library(conn, name='其他车型')
            lead = self.p.one(conn, 'leads', self.lead_id, A)
            with self.assertRaisesRegex(Fault, 'MEDIA_VEHICLE_UNMATCHED'):
                resolve_media_codes(conn, 'demo', 'MEDIA_A', lead, adapter_version='zhemanhui-v1')

    def test_unused_factory_ambiguity_does_not_block_softmi_but_blocks_direct(self):
        with self.p.tx() as conn:
            add_library(conn)
            row = list(conn.execute('SELECT * FROM media_vehicle_items').fetchone())
            row[2] = 2
            row[6] = 99
            conn.execute('INSERT INTO media_vehicle_items VALUES(?,?,?,?,?,?,?,?,?,?,?)', row)
            lead = self.p.one(conn, 'leads', self.lead_id, A)
            codes = resolve_media_codes(conn, 'demo', 'MEDIA_A', lead, adapter_version='zhemanhui-v1')
            self.assertEqual(codes['factory_id'], '')
            self.assertEqual(codes['series_id'], 701)
            with self.assertRaisesRegex(Fault, 'MEDIA_VEHICLE_AMBIGUOUS'):
                resolve_media_codes(conn, 'demo', 'MEDIA_A', lead, adapter_version='yiche-v1')
            delivery = {'media_id': 'MEDIA_A', 'route_snapshot': json.dumps({'media_id': 'MEDIA_A', 'adapter_version': 'zhemanhui-v1'})}
            conn.execute("UPDATE media_vehicle_items SET brand_id=999 WHERE org_id='demo'")
            self.assertEqual(delivery_media_codes(conn, 'demo', delivery, lead, {'media_dictionary': codes})['series_id'], 701)
            conn.execute("UPDATE media_vehicle_items SET series_id=702 WHERE org_id='demo'")
            with self.assertRaisesRegex(Fault, 'MEDIA_CATALOG_CHANGED'):
                delivery_media_codes(conn, 'demo', delivery, lead, {'media_dictionary': codes})


class CallbackReasonTests(unittest.TestCase):
    def setUp(self):
        self.fixture = zmh.ZmhIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.p = self.fixture.p

    def send(self):
        self.fixture.send()
        self.id, self.params = self.fixture.id, self.fixture.params

    def test_softmi_invalid_reason_uses_current_org_code_dictionary(self):
        self.send()
        self.p.receive_zmh_callback(dict(self.params, resultCode='1', code='-12'))
        with self.p.tx() as conn:
            conn.execute("INSERT INTO yiche_code_overrides VALUES('demo','-12','INVALID','超量，无经销商售卖')")
        row = self.p.detail(A, 'deliveries', self.id)
        self.assertEqual(row['validity_status'], 'INVALID')
        self.assertEqual(row['yiche_code'], {'code': '-12', 'description': '超量，无经销商售卖'})
        listed = next(row for row in self.p.list(A, 'deliveries')['items'] if row['id'] == self.id)
        self.assertEqual(listed['yiche_code'], row['yiche_code'])

    def test_unknown_invalid_code_is_explicit_and_success_code_cannot_override_failure(self):
        self.send()
        self.p.receive_zmh_callback(dict(self.params, resultCode='1', code='2001'))
        row = self.p.detail(A, 'deliveries', self.id)
        self.assertEqual(row['validity_status'], 'INVALID')
        self.assertNotIn('有效', row['yiche_code']['description'])


A = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}
