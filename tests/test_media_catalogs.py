"""临时数据库及假字典传输，覆盖媒体隔离、完整快照及上报自动编码。"""
import copy
from contextlib import contextmanager
from datetime import datetime
import json
import os
import tempfile
import unittest
import threading
import time
import urllib.request
import urllib.error
import server
from security import digest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from domain import Fault, Platform
from media_catalogs import DEFAULT_ENDPOINTS, MediaCatalogs, validated_endpoint
from media_catalog_matching import delivery_media_codes, resolve_media_codes
from media_vehicles import normalize_yiche
import test_synced_demands as fixtures

A = fixtures.A


@contextmanager
def business_clock(value: str):
    """冻结底层业务时钟，使now/later/北京时间day使用同一个时刻。"""
    instant = datetime.fromisoformat(value)
    with patch('domain.datetime', wraps=datetime) as clock:
        clock.now.side_effect = lambda tz=None: instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
        yield


def car(code=1, series=701, name='宝马X5'):
    return {'carId': code, 'carName': name + '车款', 'seriesId': series, 'seriesName': name,
            'factoryId': 12, 'factoryName': '宝马', 'brandId': 13, 'brandName': '宝马'}


def page(rows, number=1, total=1, count=None):
    return {'errorCode': '1', 'message': {'data': rows, 'page': number, 'total': total,
                                         'count': len(rows) if count is None else count}}


def add_library(conn, org='demo', media='MEDIA_A', series=701, city='2501', name='宝马X5', city_name='上海'):
    conn.execute('INSERT INTO media_vehicle_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (org, media, *normalize_yiche(car(series=series, name=name))))
    conn.execute('INSERT INTO media_city_items VALUES(?,?,?,?,?,?)', (org, media, city, city_name, '31', '上海市'))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.p = Platform(temp.name)
        self.p.seed()
        self.service = MediaCatalogs(self.p)

    def configure(self, media='MEDIA_A', kind='vehicles', **overrides):
        data = {'endpoint': DEFAULT_ENDPOINTS[kind], 'enabled': True, 'version': 0, **overrides}
        return self.service.configure(A, media, kind, data)

    def sync(self, responses, kind='vehicles', media='MEDIA_A'):
        replies = iter(responses)
        self.service.fetcher = lambda *args: next(replies)
        self.service._sync(*self.service._claim(A, media, kind, 'manual'))

    def test_complete_paginated_snapshot_media_isolation_and_city_code(self):
        self.configure()
        self.configure(kind='cities')
        self.sync([page([car(1)], 1, 2, 2), page([car(2)], 2, 2, 2)])
        self.sync([page([{'id': 99, 'cityCode': 2501, 'cityName': '上海', 'parentName': '上海市'}])], 'cities')
        self.assertEqual(self.service.list(A, 'MEDIA_A', 'vehicles')['total'], 2)
        self.assertEqual(self.service.list(A, 'MEDIA_B', 'vehicles')['total'], 0)
        self.assertEqual(self.service.list(A, 'MEDIA_A', 'cities')['items'][0]['city_code'], '2501')
        with self.p.connect() as conn:
            lead = {'series_id': 'BMW_X5', 'city_code': '310100'}
            codes = resolve_media_codes(conn, 'demo', 'MEDIA_A', lead)
            self.assertEqual((codes['series_id'], codes['city_id']), (701, '2501'))
            with self.assertRaisesRegex(Fault, 'MEDIA_VEHICLE_MISSING'):
                resolve_media_codes(conn, 'demo', 'MEDIA_B', lead)

    def test_later_failure_empty_duplicate_and_total_change_preserve_old_snapshot(self):
        self.configure()
        self.sync([page([car(1)])])
        cases = [
            [page([car(2)], 1, 2, 2), {'errorCode': '-1'}],
            [page([])],
            [page([car(2)], 1, 2, 2), page([car(2)], 2, 2, 2)],
            [page([car(2)], 1, 2, 2), page([car(3)], 2, 3, 3)],
            [page([car(2)], 1, 1, 2)],
        ]
        for responses in cases:
            with self.subTest(responses=responses):
                self.sync(responses)
                self.assertEqual([row['carId'] for row in self.service.list(A, 'MEDIA_A', 'vehicles')['items']], [1])
                self.assertEqual(next(x for x in self.service.overview(A, 'vehicles') if x['id'] == 'MEDIA_A')['runs'][0]['state'], 'FAILED')

    def test_configuration_version_and_late_result_cannot_overwrite(self):
        self.configure()
        claim = self.service._claim(A, 'MEDIA_A', 'vehicles', 'manual')
        self.configure(version=1, enabled=False)
        self.service.fetcher = lambda *args: page([car()])
        self.service._sync(*claim)
        self.assertEqual(self.service.list(A, 'MEDIA_A', 'vehicles')['total'], 0)
        with self.assertRaisesRegex(Fault, 'VERSION_CONFLICT'):
            self.configure(version=1)

    def test_real_environment_manual_demands_cannot_fall_back_to_demo_models(self):
        self.p.mock_enabled = False
        self.assertTrue(self.service.entry_options(A, 'MEDIA_A')['configured'])
        with self.assertRaisesRegex(Fault, 'MEDIA_VEHICLE_MISSING'):
            self.p.create_demand(A, {'demand_no': 'catalog-no-demo-fallback', 'route_id': 'DIRECT_A',
                                    'series_id': 'BMW_X5', 'city_codes': ['上海']})

    def test_org_role_and_secret_protection(self):
        with self.assertRaises(Fault):
            self.service.configure({**A, 'role': 'analyst'}, 'MEDIA_A', 'vehicles', {})
        with self.assertRaises(Fault):
            self.service.list({**A, 'org_id': 'other'}, 'MEDIA_A', 'cities')
        with self.assertRaises(Fault):
            self.configure(media='AGENCY_A')
        self.configure(endpoint='https://public.example.invalid/cars', token='synthetic-private-token')
        public = json.dumps(self.service.overview(A, 'vehicles'))
        self.assertNotIn('synthetic-private-token', public)
        with self.p.connect() as conn:
            stored = conn.execute('SELECT token_cipher FROM media_dictionary_sources').fetchone()[0]
            self.assertNotEqual(stored, 'synthetic-private-token')
        with self.assertRaises(Fault):
            self.configure(version=1, endpoint=DEFAULT_ENDPOINTS['vehicles'])
        self.configure(version=1, clear_token=True)

    def test_direct_yiche_request_uses_final_media_codes_instead_of_old_fields(self):
        from test_yiche_push import CONFIG
        import yiche_transport
        self.configure()
        with self.p.tx() as conn:
            add_library(conn)
        lead = next(row for row in self.p.list(A, 'leads', 100)['items'] if row['lead_no'] == 'L000001')
        demand = next(row for row in self.p.list(A, 'demands', 100)['items'] if row['demand_no'] == '000123')
        self.p.yiche_live = CONFIG
        queued = self.p.dispatch(A, [(lead['id'], demand['id'])], 'catalog-direct')
        self.assertEqual(queued['created'], 1)
        with patch.object(yiche_transport, 'send', return_value=('ACKED', 200, '')) as send:
            self.p.worker(A)
        request = send.call_args.args[1]
        payload = request['param']
        self.assertEqual((str(payload['cityId']), payload['serialId'], payload['manufacturerId']), ('2501', 701, 12))

    def test_url_and_pagination_inputs(self):
        self.assertEqual(validated_endpoint(DEFAULT_ENDPOINTS['vehicles'] + '?page=3'), DEFAULT_ENDPOINTS['vehicles'])
        for endpoint in ('http://127.0.0.1/api', 'http://172.24.1.2/api', 'file:///etc/passwd',
                         'http://localhost/api', 'https://a.invalid/?token=secret', 'https://a.invalid:22/api'):
            with self.subTest(endpoint=endpoint), self.assertRaises(Fault):
                validated_endpoint(endpoint)
        with self.assertRaises(Fault):
            self.service.list(A, 'MEDIA_A', 'cities', page_size=30)

    def test_schedule_and_duplicate_claim(self):
        self.configure(schedule_hour=8)
        with business_clock('2026-11-24T23:00:00+00:00'):
            self.assertIsNone(self.service._claim(A, 'MEDIA_A', 'vehicles', 'auto'))
        with business_clock('2026-11-25T00:00:00+00:00'):
            self.assertIsNotNone(self.service._claim(A, 'MEDIA_A', 'vehicles', 'auto'))
            self.assertIsNone(self.service._claim(A, 'MEDIA_A', 'vehicles', 'auto'))

    def test_running_claim_is_replaced_only_after_business_clock_timeout(self):
        self.configure(schedule_hour=8)
        with business_clock('2031-01-01T00:00:00+00:00'):
            first = self.service._claim(A, 'MEDIA_A', 'vehicles', 'auto')
        with business_clock('2031-01-01T00:59:00+00:00'):
            self.assertIsNone(self.service._claim(A, 'MEDIA_A', 'vehicles', 'auto'))
        with business_clock('2031-01-01T01:01:00+00:00'):
            replacement = self.service._claim(A, 'MEDIA_A', 'vehicles', 'auto')
        self.assertIsNotNone(replacement)
        self.assertNotEqual(first[0], replacement[0])
        with self.p.connect() as conn:
            first_run = conn.execute('SELECT state,sync_day FROM media_dictionary_runs WHERE id=?',
                                     (first[0],)).fetchone()
            self.assertEqual((first_run['state'], first_run['sync_day']), ('FAILED', '2031-01-01'))
            self.assertEqual(conn.execute("SELECT count(*) FROM media_dictionary_runs WHERE state='RUNNING'").fetchone()[0], 1)

    def test_ambiguous_names_and_catalog_change_do_not_guess(self):
        with self.p.tx() as conn:
            add_library(conn)
            lead = {'series_id': 'BMW_X5', 'city_code': '310100'}
            codes = resolve_media_codes(conn, 'demo', 'MEDIA_A', lead)
            delivery = {'media_id': 'MEDIA_A', 'route_snapshot': json.dumps({'media_id': 'MEDIA_A'})}
            conn.execute("UPDATE media_city_items SET city_code='9999'")
            with self.assertRaisesRegex(Fault, 'MEDIA_CATALOG_CHANGED'):
                delivery_media_codes(conn, 'demo', delivery, lead, {'media_dictionary': codes})
            conn.execute("INSERT INTO media_city_items VALUES('demo','MEDIA_A','9998','上海','','另一个省')")
            with self.assertRaisesRegex(Fault, 'MEDIA_CITY_AMBIGUOUS'):
                resolve_media_codes(conn, 'demo', 'MEDIA_A', lead)

    def test_http_api_requires_csrf_and_respects_org(self):
        token, csrf = 'catalog-test-session', 'catalog-test-csrf'
        with self.p.tx() as conn:
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), A['id'], time.time() + 60, csrf))
        with patch.object(server, 'P', self.p):
            http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            def request(path, method='GET', include_csrf=True):
                headers = {'Cookie': 'lead_session=' + token, 'Content-Type': 'application/json'}
                if include_csrf:
                    headers['X-CSRF-Token'] = csrf
                body = json.dumps({'endpoint': DEFAULT_ENDPOINTS['cities'], 'version': 0, 'enabled': False}).encode() if method == 'PATCH' else None
                req = urllib.request.Request('http://127.0.0.1:' + str(http.server_port) + path, data=body, headers=headers, method=method)
                try:
                    response = urllib.request.urlopen(req)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, json.load(response)
            try:
                self.assertEqual(request('/api/v1/media-catalogs/cities')[0], 200)
                path = '/api/v1/media-catalogs/cities/MEDIA_A/config'
                self.assertEqual(request(path, 'PATCH', False)[0], 403)
                self.assertEqual(request(path, 'PATCH')[0], 200)
                status, result = request('/api/v1/media-catalogs/cities/MEDIA_A?page_size=50')
                self.assertEqual(status, 200)
                self.assertEqual(result['data']['page_size'], 50)
                self.assertEqual(request('/api/v1/media-catalogs/cities/other-org-media')[0], 404)
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=2)

    def test_manual_demand_choices_use_final_media_and_fill_codes(self):
        self.configure()
        with self.p.tx() as conn:
            add_library(conn)
            add_library(conn, media='MEDIA_B', series=702, city='2701')
        options = self.service.entry_options(A, 'MEDIA_A')
        self.assertEqual([row['value'] for row in options['models']], ['media:MEDIA_A:13:701'])
        demand = self.p.create_demand(A, {'demand_no': 'catalog-manual-1', 'route_id': 'DIRECT_A',
            'series_id': options['models'][0]['value'], 'city_codes': ['上海'], 'target_total': 5, 'daily_limit': 5,
            'yiche': {'platFormId': 900001, 'plat': 'Web'}})
        config = json.loads(demand['yiche_config'])
        self.assertEqual((str(config['cityId']), config['serialId'], config['manufacturerId']), ('2501', 701, 12))
        with self.assertRaisesRegex(Fault, 'MEDIA_VEHICLE_UNMATCHED'):
            self.p.create_demand(A, {'demand_no': 'catalog-manual-2', 'route_id': 'DIRECT_A',
                'series_id': 'media:MEDIA_B:13:702', 'city_codes': ['上海']})

    def test_upgrade_from_30_keeps_existing_vehicle_ids(self):
        with self.p.tx() as conn:
            add_library(conn)
            conn.execute('DROP TABLE media_city_items')
            conn.execute('DROP TABLE media_dictionary_sources')
            conn.execute('DROP TABLE media_dictionary_runs')
            conn.execute('DROP TABLE route_send_controls')
            conn.execute('DELETE FROM schema_version WHERE version>=31')
        upgraded = Platform(self.p.root)
        with upgraded.connect() as conn:
            self.assertEqual(conn.execute('SELECT series_id FROM media_vehicle_items').fetchone()[0], 701)
            self.assertEqual(conn.execute('SELECT max(version) FROM schema_version').fetchone()[0], 32)
            self.assertEqual(conn.execute('SELECT count(*) FROM media_city_items').fetchone()[0], 0)

    def test_migration_reopen_preserves_dictionary_and_old_records(self):
        self.configure()
        self.sync([page([car()])])
        reopened = Platform(self.p.root)
        with reopened.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_version WHERE version=32').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM media_vehicle_items').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM media_city_items').fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])


class CatalogDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SyncedDemandTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.p = self.fixture.p
        with self.p.tx() as conn:
            conn.execute("UPDATE routes SET adapter_version='zhemanhui-v1' WHERE id='AGENCY_TO_A'")
            conn.execute("UPDATE demands SET buyer_id='AGENCY_A',receiver_id='AGENCY_A',route_id='AGENCY_TO_A' WHERE id=?", (self.fixture.d['id'],))
            conn.execute("INSERT INTO zmh_routes VALUES('AGENCY_TO_A','demo','https://callback.example.invalid/open/v1/zhemanhui/callback',1)")
        self.lead_id = self.fixture.lead(city='上海市')

    def test_preview_blocks_missing_city_before_inventory_is_reserved(self):
        with self.p.tx() as conn:
            add_library(conn)
            conn.execute('DELETE FROM media_city_items')
        result = self.fixture.preview(self.lead_id)
        self.assertEqual(result['reason'], 'MEDIA_CITY_MISSING')
        result = self.fixture.send(self.lead_id)
        self.assertEqual(result['created'], 0)

    def test_sends_final_media_codes_without_route_specific_mapping(self):
        with self.p.tx() as conn:
            add_library(conn)
            add_library(conn, media='MEDIA_B', series=777, city='8888')
        result = self.fixture.send(self.lead_id)
        delivery_id = result['items'][0]['delivery_id']
        requests = []
        def send(request, **kwargs):
            requests.append(request)
            return {'status': 'ACKED', 'validity': 'PENDING'}
        with patch.dict(os.environ, {'ZMH_ENABLE_TEST_PUSH': '1'}), patch('zhemanhui.send_test', side_effect=send):
            self.p.worker(A)
        params = parse_qs(urlsplit(requests[0].full_url).query)
        self.assertEqual((params['cityId'], params['serialId']), (['2501'], ['701']))
        with self.p.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM zmh_mappings').fetchone()[0], 0)
        self.assertEqual(self.p.detail(A, 'deliveries', delivery_id)['send_status'], 'ACKED')

    def test_detail_redacts_new_code_snapshot_with_lead_field_permissions(self):
        with self.p.tx() as conn:
            add_library(conn)
        result = self.fixture.send(self.lead_id)
        with self.p.tx() as conn:
            conn.execute("INSERT OR REPLACE INTO lead_field_permissions VALUES('u_operator','[\"lead_no\"]')")
        detail = self.p.detail({**A, 'id': 'u_operator', 'role': 'operator'}, 'deliveries', result['items'][0]['delivery_id'])
        codes = detail['outbound_payload']['media_dictionary']
        self.assertNotIn('city_id', codes)
        self.assertNotIn('series_id', codes)
        self.assertNotIn('brand_id', codes)

    def test_changed_dictionary_cancels_unsent_and_preserves_snapshot(self):
        with self.p.tx() as conn:
            add_library(conn)
        result = self.fixture.send(self.lead_id)
        delivery_id = result['items'][0]['delivery_id']
        with self.p.tx() as conn:
            conn.execute("UPDATE media_city_items SET city_code='2502'")
        with patch.dict(os.environ, {'ZMH_ENABLE_TEST_PUSH': '1'}), patch('zhemanhui.send_test') as send:
            self.p.worker(A)
        send.assert_not_called()
        delivery = self.p.detail(A, 'deliveries', delivery_id)
        self.assertEqual(delivery['error'], 'MEDIA_CATALOG_CHANGED')
        self.assertEqual(delivery['quota_state'], 'RELEASED')
