"""使用临时库验证首次初始化与演示造数的隔离。"""
import tempfile
import unittest

from bootstrap import initialize_admin, prepare_startup
from domain import Platform
from security import verify_password


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.platform = Platform(self.temp.name)

    def counts(self):
        conn = self.platform.connect()
        try:
            return {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                    for table in ('users', 'leads', 'demands', 'partners', 'settings')}
        finally:
            conn.close()

    def test_empty_normal_start_does_not_seed(self):
        with self.assertRaisesRegex(ValueError, '尚未初始化管理员'):
            prepare_startup(self.platform)
        self.assertEqual(self.counts(), dict(users=0, leads=0, demands=0, partners=0, settings=0))

    def test_admin_only_and_restart_preserves_state(self):
        password = 'test-only-long-password'
        initialize_admin(self.platform, 'first-admin', password)
        prepare_startup(self.platform)
        prepare_startup(self.platform)
        self.assertEqual(self.counts(), dict(users=1, leads=0, demands=0, partners=0, settings=1))
        conn = self.platform.connect()
        try:
            user = conn.execute('SELECT * FROM users').fetchone()
            self.assertTrue(verify_password(password, user['password_hash']))
            self.assertEqual(user['role'], 'admin')
            settings = conn.execute('SELECT * FROM settings').fetchone()
            self.assertEqual(settings['dispatch_enabled'], 0)
            self.assertEqual(settings['auto_dispatch_enabled'], 0)
            self.assertNotIn(password, conn.execute('SELECT detail FROM audit_log').fetchone()[0])
        finally:
            conn.close()
        with self.assertRaisesRegex(ValueError, '已有账号'):
            initialize_admin(self.platform, 'replacement', password)
        self.assertEqual(self.counts()['users'], 1)

    def test_short_password_rejected_without_records(self):
        with self.assertRaisesRegex(ValueError, '密码长度'):
            initialize_admin(self.platform, 'first-admin', 'short')
        self.assertEqual(self.counts()['users'], 0)
        self.assertEqual(self.counts()['settings'], 0)

    def test_explicit_local_demo_only(self):
        for flags in ({'host': '0.0.0.0'}, {'public_test': True}, {'external_push': True}):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, '本机回环'):
                prepare_startup(self.platform, seed_demo=True, **flags)
            self.assertEqual(self.counts()['users'], 0)
        prepare_startup(self.platform, seed_demo=True)
        self.assertGreater(self.counts()['leads'], 0)
        before = self.counts()
        prepare_startup(self.platform)
        self.assertEqual(before, self.counts())
