"""发送配置全部使用临时数据库及模拟传输，不启用真实接口。"""
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from domain import Fault, Platform
from route_sending import get_status, update
from zhemanhui import Config, ProtocolError, prepare, send_request, send_test
from api_connection_status import connection_statuses
import test_zhemanhui_integration as zmh


class RouteSendingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = zmh.ZmhIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.p = self.fixture.p
        self.a = zmh.A
        self.route = 'AGENCY_TO_A'

    def save(self, environment='TEST', enabled=0, **extra):
        config = get_status(self.p, self.a, self.route)
        return update(self.p, self.a, self.route,
                      dict(environment=environment, enabled=enabled, version=config['version'], reason='隔离测试', **extra))

    def test_server_guard_readonly_and_per_route_isolation(self):
        with patch.dict(os.environ, {'ZMH_ENABLE_TEST_PUSH': '0'}):
            before = get_status(self.p, self.a, self.route)
            self.assertIn('服务器发送总开关关闭，需维护人员开启', before['blockers'])
            self.save(enabled=1)
            self.assertEqual(os.environ['ZMH_ENABLE_TEST_PUSH'], '0')
            self.assertFalse(get_status(self.p, self.a, self.route)['server_enabled'])
            self.assertEqual(self.fixture.f.preview(self.fixture.lid)['reason'], 'ZMH_SERVER_DISABLED')
            self.assertEqual(self.fixture.f.send(self.fixture.lid)['created'], 0)
        self.save(enabled=0)
        self.assertEqual(self.fixture.f.preview(self.fixture.lid)['reason'], 'ROUTE_UNAVAILABLE')
        with self.p.tx() as conn:
            self.assertEqual(conn.execute('SELECT enabled FROM routes WHERE id=?', ('DIRECT_A',)).fetchone()[0], 1)

    def test_switch_requires_pause_then_separate_enable_and_audit(self):
        with self.assertRaises(Fault) as error:
            self.save('PRODUCTION', 1)
        self.assertEqual(error.exception.code, 'ROUTE_ENV_REQUIRES_PAUSE')
        self.save(enabled=0)
        changed = self.save('PRODUCTION', 0)
        self.assertEqual(changed['endpoint'], 'http://yiche.asoapple.cn/api/v1/saveClue')
        self.assertFalse(changed['enabled'])
        self.save('PRODUCTION', 1)
        result = get_status(self.p, self.a, self.route)
        self.assertTrue(result['enabled'])
        self.assertEqual(len(result['history']), 3)
        self.assertEqual(result['history'][0]['detail']['after']['environment'], 'PRODUCTION')
        self.assertNotIn('callback_url', json.dumps(result))

    def test_permissions_tenants_versions_and_unsupported(self):
        data = dict(environment='TEST', enabled=0, version=0, reason='隔离测试')
        for role in ('operator', 'engineer', 'business'):
            with self.subTest(role=role), self.assertRaises(Fault):
                update(self.p, dict(self.a, role=role), self.route, data)
        self.assertNotIn('history', get_status(self.p, dict(self.a, role='operator'), self.route))
        with self.assertRaises(Fault):
            update(self.p, dict(self.a, org_id='other'), self.route, data)
        with self.assertRaises(Fault):
            update(self.p, self.a, 'DIRECT_A', data)
        self.save()
        with self.assertRaises(Fault) as error:
            update(self.p, self.a, self.route, data)
        self.assertEqual(error.exception.code, 'VERSION_CONFLICT')
        for change in ({'enabled': True}, {'environment': 'OTHER'}, {'reason': ''}, {'version': '1'}):
            with self.subTest(change=change), self.assertRaises(Fault):
                update(self.p, self.a, self.route, dict(data, **change))

    def test_cancel_unsent_release_quota_and_invalidate_preview(self):
        preview = self.p.preview(self.a, [self.fixture.lid], [self.fixture.f.d['id']])
        item = self.fixture.f.send(self.fixture.lid)['items'][0]
        self.save()
        delivery = self.p.detail(self.a, 'deliveries', item['delivery_id'])
        self.assertEqual(delivery['send_status'], 'CANCELED')
        self.assertEqual(delivery['quota_state'], 'RELEASED')
        self.assertTrue(self.p.integrity(self.a)['passed'])
        with self.assertRaises(Fault) as error:
            self.p.dispatch(self.a, [(self.fixture.lid, self.fixture.f.d['id'])], 'stale', preview['preview_version'])
        self.assertEqual(error.exception.code, 'VERSION_CONFLICT')

    def test_production_snapshot_and_no_real_network(self):
        self.save()
        self.save('PRODUCTION', 0)
        self.save('PRODUCTION', 1)
        sent = self.fixture.f.send(self.fixture.lid)
        delivery_id = sent['items'][0]['delivery_id']
        requests = []
        def fake(request, *, environment, enabled):
            requests.append((request, environment, enabled))
            return {'status': 'ACKED', 'validity': 'PENDING'}
        with patch('zhemanhui.send_request', side_effect=fake):
            self.p.worker(self.a)
        self.assertEqual(len(requests), 1)
        request, environment, enabled = requests[0]
        self.assertEqual(environment, 'PRODUCTION')
        self.assertTrue(enabled)
        self.assertEqual(urlsplit(request.full_url).path, '/api/v1/saveClue')
        delivery = self.p.detail(self.a, 'deliveries', delivery_id)
        self.assertEqual(delivery['validity_status'], 'PENDING')
        with self.p.tx() as conn:
            snapshot = conn.execute('SELECT route_snapshot FROM deliveries WHERE id=?', (delivery_id,)).fetchone()[0]
        self.assertEqual(json.loads(snapshot)['send_environment'], 'PRODUCTION')
        # 停用及改回测试不改变已发送请求快照，也不影响其鉴权回调。
        self.save('PRODUCTION', 0)
        self.save('TEST', 0)
        from urllib.parse import parse_qs
        params = {key: value[0] for key, value in parse_qs(urlsplit(parse_qs(urlsplit(request.full_url).query)['sourceUrl'][0]).query).items()}
        result = self.p.receive_zmh_callback(dict(params, resultCode='0', code='2001'))
        self.assertEqual(result['validity'], 'VALID')
        with self.p.tx() as conn:
            self.assertNotIn(('AGENCY_A', 'MEMBER'), connection_statuses(conn, 'demo')[1])

    def test_legacy_defaults_and_migration_repeat(self):
        self.assertEqual(get_status(self.p, self.a, self.route)['environment'], 'TEST')
        Platform(self.fixture.f.temp.name)
        with self.p.tx() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_version WHERE version=31').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM route_send_controls').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT enabled FROM zmh_routes WHERE route_id=?', (self.route,)).fetchone()[0], 1)


class RouteTransportTests(unittest.TestCase):
    def request(self, environment):
        return prepare(Config('https://callback.example/open/v1/zhemanhui/callback', environment=environment),
                       demand_no='0001', city_id='1', series_id='2', name='虚构测试', mobile='19900000000',
                       delivery_id='fake', callback_token='x' * 40)

    def test_transport_rejects_environment_mismatch_and_disabled(self):
        production = self.request('PRODUCTION')
        with self.assertRaises(ProtocolError):
            send_test(production, enabled=True)
        with self.assertRaises(ProtocolError):
            send_request(production, environment='PRODUCTION')
        with self.assertRaises(ProtocolError):
            self.request('INVALID')
        production.full_url = 'http://other.example/api/v1/saveClue'
        with self.assertRaises(ProtocolError):
            send_request(production, environment='PRODUCTION', enabled=True)
