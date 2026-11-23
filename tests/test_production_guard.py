"""正式环境开关不能让模拟发送、反馈或查询污染业务结果。"""
import tempfile
import unittest

from domain import Fault, Platform

ACTOR = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}


class ProductionGuardTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.platform = Platform(folder.name)
        self.platform.seed()
        conn = self.platform.connect()
        try:
            self.lead_id = conn.execute("SELECT id FROM leads WHERE lead_no='L000001'").fetchone()[0]
            self.demand_id = conn.execute("SELECT id FROM demands WHERE demand_no='AUTOHOME-0098'").fetchone()[0]
        finally:
            conn.close()

    def test_preview_and_creation_refuse_mock(self):
        self.platform.mock_enabled = False
        preview = self.platform.preview(ACTOR, [self.lead_id], [self.demand_id])
        self.assertEqual(preview['items'][0]['reason'], 'MOCK_DISABLED')
        result = self.platform.dispatch(ACTOR, [(self.lead_id, self.demand_id)], 'prod-block')
        self.assertEqual(result['created'], 0)

    def test_pending_mock_delivery_cannot_send_or_simulate_feedback(self):
        result = self.platform.dispatch(ACTOR, [(self.lead_id, self.demand_id)], 'pending-mock')
        self.assertEqual(result['created'], 1)
        delivery_id = result['items'][0]['delivery_id']
        self.platform.mock_enabled = False
        self.assertEqual(self.platform.worker(ACTOR)['count'], 0)
        conn = self.platform.connect()
        try:
            delivery = conn.execute('SELECT * FROM deliveries WHERE id=?', (delivery_id,)).fetchone()
            self.assertEqual(delivery['send_status'], 'CANCELED')
            self.assertIsNone(delivery['first_sent_at'])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
        finally:
            conn.close()
        for action in (lambda: self.platform.mock_callback(ACTOR, delivery_id, 'VALID'),
                       lambda: self.platform.reconcile(ACTOR, delivery_id)):
            with self.assertRaises(Fault) as caught:
                action()
            self.assertEqual(caught.exception.code, 'MOCK_DISABLED')
        self.assertTrue(self.platform.integrity(ACTOR)['passed'])
