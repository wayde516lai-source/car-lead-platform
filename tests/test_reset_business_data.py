"""用隔离数据库验证清空范围和字典保留，绝不读取真实业务库。"""
import tempfile
import unittest

from domain import Platform
from scripts.reset_business_data import CLEARED_TABLES, reset_business_data


class ResetBusinessDataTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.platform = Platform(folder.name)
        self.platform.seed()
        with self.platform.tx() as conn:
            conn.execute('INSERT INTO yiche_code_overrides VALUES(?,?,?,?)',
                         ('demo', 'TEST-CODE', 'INVALID', '隔离测试Code说明'))

    def test_reset_preserves_dictionaries_accounts_and_is_repeatable(self):
        with self.platform.tx() as conn:
            result = reset_business_data(conn)
            self.assertEqual(result['removed']['leads'], 13)
            self.assertEqual(result['removed']['partners'], 7)
            self.assertEqual(result['preserved']['catalog'], 3)
            self.assertEqual(result['preserved']['yiche_code_overrides'], 1)
            self.assertEqual(result['preserved']['users'], 8)
            for table in CLEARED_TABLES:
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        with self.platform.tx() as conn:
            result = reset_business_data(conn)
            self.assertTrue(all(count == 0 for count in result['removed'].values()))
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='audit_no_delete'").fetchone())

    def test_preserved_vehicle_reference_prevents_deleting_partner(self):
        with self.platform.tx() as conn:
            conn.execute('INSERT INTO media_vehicle_sources(org_id,media_id,adapter,cid,key_cipher,enabled) VALUES(?,?,?,?,?,?)',
                         ('demo', 'MEDIA_A', 'yiche', 'test-only-cid', 'test-only-encrypted-key', 0))
        with self.assertRaisesRegex(ValueError, '保留字典仍有关联引用'):
            with self.platform.tx() as conn:
                reset_business_data(conn)
        with self.platform.tx() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM leads').fetchone()[0], 13)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM partners').fetchone()[0], 7)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM media_vehicle_sources').fetchone()[0], 1)
