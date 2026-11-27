"""请求内查询复用、轻量接口与交易实时校验的临时库验证。"""
import unittest
from unittest.mock import patch

from read_queries import ReadQueries
import test_distribution as fixtures

A = fixtures.A


class ReadPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DistributionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.p = self.fixture.p

    def test_read_result_is_reused_only_inside_one_request(self):
        connection = self.p.connect()
        reads = []
        connection.set_trace_callback(lambda sql: reads.append(sql) if sql.startswith('SELECT') else None)
        cached = ReadQueries(connection)
        first = cached.execute('SELECT dispatch_enabled FROM settings WHERE org_id=?', ('demo',))
        second = cached.execute('SELECT dispatch_enabled FROM settings WHERE org_id=?', ('demo',))
        self.assertEqual(first.fetchone()[0], second.fetchone()[0])
        self.assertIsNone(first.fetchone())
        self.assertEqual(len(reads), 1)
        cached.close()
        with self.p.tx() as conn:
            conn.execute("UPDATE settings SET dispatch_enabled=0 WHERE org_id='demo'")
        fresh = self.p.read_connect()
        try:
            self.assertEqual(fresh.execute("SELECT dispatch_enabled FROM settings WHERE org_id='demo'").fetchone()[0], 0)
            with self.assertRaises(ValueError):
                fresh.execute("UPDATE settings SET dispatch_enabled=1 WHERE org_id='demo'")
        finally:
            fresh.close()

    def test_delivery_report_does_not_run_matching_and_keeps_metrics(self):
        full = self.p.reports(A)
        with patch.object(self.p, '_matchable_lead_rows', side_effect=AssertionError('unexpected matching')), patch.object(self.p, '_business_match_lead_rows', side_effect=AssertionError('unexpected business matching')):
            light = self.p.reports(A, include_matching=False)
        self.assertEqual(light['metrics'], full['metrics'])
        self.assertEqual(light['groups'], full['groups'])
        self.assertNotIn('matchable', light)

    def test_compact_matching_options_have_same_active_ids_without_large_payload(self):
        full = self.p.list(A, 'demands', page_size=100, status='ACTIVE')
        light = self.p.matching_demand_options(A)
        self.assertEqual({row['id'] for row in full['items']}, {row['id'] for row in light['items']})
        self.assertTrue(all(set(row) == {'id', 'buyer_id', 'receiver_id', 'media_id', 'sale_project'} for row in light['items']))
        self.assertEqual(self.p.matching_demand_options({'id': 'other', 'role': 'admin', 'org_id': 'other'})['items'], [])

    def test_changed_route_is_not_hidden_by_previous_read_cache(self):
        self.assertGreater(self.p.matching_leads(A)['total'], 0)
        with self.p.tx() as conn:
            conn.execute("UPDATE routes SET enabled=0 WHERE org_id='demo'")
        self.assertEqual(self.p.matching_leads(A)['total'], 0)
        self.assertEqual(self.fixture.reason('MEDIA_A'), 'ROUTE_UNAVAILABLE')
        self.assertEqual(self.fixture.send('MEDIA_A')['created'], 0)

    def test_json_compression_is_negotiated_and_lossless(self):
        import gzip
        import io
        import json
        from server import Handler
        payload = {'items': [{'label': '测试数据' * 40} for _ in range(100)]}
        for encoding, compressed in [('gzip', True), ('gzip;q=0', False), ('', False)]:
            handler = Handler.__new__(Handler)
            handler.headers = {'Accept-Encoding': encoding}
            headers = {}
            handler.send_response = lambda status: None
            handler.send_header = lambda key, value: headers.update({key: value})
            handler.end_headers = lambda: None
            handler.wfile = io.BytesIO()
            handler.send(200, payload)
            body = handler.wfile.getvalue()
            self.assertEqual(headers.get('Content-Encoding') == 'gzip', compressed)
            self.assertEqual(int(headers['Content-Length']), len(body))
            self.assertEqual(json.loads(gzip.decompress(body) if compressed else body), payload)
