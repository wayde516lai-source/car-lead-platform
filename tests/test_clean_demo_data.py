import unittest
from tempfile import TemporaryDirectory
from domain import Platform
from scripts.clean_demo_data import cleanup


class DemoCleanupTests(unittest.TestCase):
    def test_business_lead_preserved_and_demo_not_recreated_on_restart(self):
        with TemporaryDirectory() as folder:
            platform = Platform(folder)
            platform.seed()
            actor = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}
            saved = platform.create_lead(actor, {'supplier_id':'SOURCE_A','name': '业务占位客户', 'phone': '19900008881',
                                                'brand_name': '宝马', 'model': '宝马 X5',
                                                'city': '上海市', 'is_sold': '否'})
            with platform.tx() as conn:
                result = cleanup(conn)
                self.assertEqual(result['leads'], 13)
                self.assertIsNotNone(conn.execute('SELECT 1 FROM leads WHERE id=?', (saved['lead_id'],)).fetchone())
            platform.seed()
            with platform.tx() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM leads').fetchone()[0], 1)
                self.assertFalse(cleanup(conn))
                self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())
            self.assertTrue(platform.integrity(actor)['passed'])
