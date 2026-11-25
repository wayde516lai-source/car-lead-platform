"""接口归属按组织、主体、会员项目和媒体隔离，探针不制造业务交付。"""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from domain import Fault, now
from security import digest
from zhemanhui_binding import PARTNER_NAME, bind_member_interface
import test_synced_demands as synced

ACTOR = synced.A


class ZmhBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = synced.SyncedDemandTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.platform = self.fixture.p
        with self.platform.tx() as conn:
            conn.execute('UPDATE partners SET name=? WHERE id=?', (PARTNER_NAME, 'AGENCY_A'))
            conn.execute("UPDATE partners SET name='易车' WHERE id='MEDIA_A'")

    def probe(self, status='ACKED', probe_id='local-success-probe'):
        with self.platform.tx() as conn:
            conn.execute('''INSERT INTO zmh_submissions(id,org_id,token_hash,created_at,send_status)
                            VALUES(?,?,?,?,?)''',
                         (probe_id, 'demo', digest('fictional-test-only-token'), now(), status))
        return probe_id

    def status(self, partner='AGENCY_A', project='MEMBER'):
        subject = next(row for row in self.platform.catalogs(ACTOR)['partners'] if row['id'] == partner)
        return next(row['api_status'] for row in subject['cooperation_projects'] if row['project'] == project)

    def test_preview_is_read_only_and_exact_subject_required(self):
        probe_id = self.probe()
        plan = bind_member_interface(self.platform, ACTOR, probe_id=probe_id)
        self.assertEqual((plan['buyer_id'], plan['media_id'], plan['project']), ('AGENCY_A', 'MEDIA_A', 'MEMBER'))
        self.assertEqual(self.status(), 'NOT_CONNECTED')
        with self.platform.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM zmh_routes').fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT adapter_version FROM routes WHERE id='AGENCY_TO_A'").fetchone()[0], 'mock-v1')
        with self.assertRaises(Fault):
            bind_member_interface(self.platform, ACTOR, partner_name='北京软米科技有限公司(会员 2)')

    def test_cli_previews_then_backs_up_before_explicit_apply(self):
        probe_id = self.probe()
        script = Path(__file__).resolve().parents[1] / 'scripts/bind_ruanmi_member.py'
        command = [sys.executable, str(script), '--data', str(self.platform.root),
                   '--admin-user', 'admin', '--probe-id', probe_id]
        preview = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(preview.stdout)['mode'], 'read-only preview')
        self.assertEqual(self.status(), 'NOT_CONNECTED')
        applied = subprocess.run(command + ['--apply'], capture_output=True, text=True, check=True)
        result = json.loads(applied.stdout)
        self.assertTrue((self.platform.root / result['backup_file']).is_file())
        self.assertTrue(result['backup_file'].endswith('.sqlite.enc'))
        self.assertEqual(self.status(), 'CONNECTED')

    def test_successful_probe_is_bound_only_to_member_and_keeps_business_counts(self):
        probe_id = self.probe()
        with self.platform.connect() as conn:
            before = conn.execute('SELECT count(*) FROM deliveries').fetchone()[0]
        result = bind_member_interface(self.platform, ACTOR, apply=True, probe_id=probe_id)
        self.assertEqual(self.status(), 'CONNECTED')
        self.assertEqual(self.status(project='KA'), 'NOT_CONNECTED')
        self.assertEqual(self.status(partner='MEDIA_A'), 'NOT_CONNECTED')
        with self.platform.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM deliveries').fetchone()[0], before)
            self.assertIsNone(conn.execute('SELECT delivery_id FROM zmh_submissions WHERE id=?', (probe_id,)).fetchone()[0])
            self.assertEqual(conn.execute('SELECT enabled FROM zmh_routes WHERE route_id=?', (result['route_id'],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT enabled FROM routes WHERE id='AGENCY_TO_A'").fetchone()[0], 1)
        bind_member_interface(self.platform, ACTOR, apply=True, probe_id=probe_id)
        with self.platform.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit_log WHERE action='ZMH_PROBE_BINDING'").fetchone()[0], 1)
            conn.execute("UPDATE routes SET media_id='MEDIA_B' WHERE id='AGENCY_TO_A'")
        self.assertEqual(self.status(), 'NOT_CONNECTED')

    def test_unconfirmed_and_cross_org_probe_cannot_be_claimed(self):
        for status in ('UNKNOWN', 'FAILED'):
            probe_id = self.probe(status, status)
            with self.subTest(status=status), self.assertRaises(Fault):
                bind_member_interface(self.platform, ACTOR, apply=True, probe_id=probe_id)
        self.probe()
        with self.assertRaises(Fault):
            bind_member_interface(self.platform, {**ACTOR, 'org_id': 'other'}, apply=True,
                                  probe_id='local-success-probe')
        with self.assertRaises(Fault):
            bind_member_interface(self.platform, {**ACTOR, 'role': 'operator'}, apply=True)
        self.assertEqual(self.status(), 'NOT_CONNECTED')

    def test_no_probe_does_not_manufacture_connected_status(self):
        bind_member_interface(self.platform, ACTOR, apply=True)
        self.assertEqual(self.status(), 'NOT_CONNECTED')

    def test_mock_route_with_enabled_interface_config_cannot_be_activated_by_binding(self):
        with self.platform.tx() as conn:
            conn.execute('INSERT INTO zmh_routes VALUES(?,?,?,1)',
                         ('AGENCY_TO_A', 'demo', 'https://saas.asoapple.cn/open/v1/zhemanhui/callback'))
        with self.assertRaises(Fault) as caught:
            bind_member_interface(self.platform, ACTOR, apply=True)
        self.assertEqual(caught.exception.code, 'ZMH_ACTIVE_CONFIG_CONFLICT')
        with self.platform.connect() as conn:
            self.assertEqual(conn.execute("SELECT adapter_version FROM routes WHERE id='AGENCY_TO_A'").fetchone()[0], 'mock-v1')

    def test_existing_route_history_is_not_rewritten(self):
        with self.platform.tx() as conn:
            conn.execute("UPDATE demands SET buyer_id='AGENCY_A',receiver_id='AGENCY_A',route_id='AGENCY_TO_A' WHERE id=?",
                         (self.fixture.d['id'],))
        self.fixture.send(self.fixture.lead())
        with self.assertRaises(Fault) as caught:
            bind_member_interface(self.platform, ACTOR, apply=True)
        self.assertEqual(caught.exception.code, 'ZMH_ROUTE_HISTORY')
        with self.platform.connect() as conn:
            self.assertEqual(conn.execute("SELECT adapter_version FROM routes WHERE id='AGENCY_TO_A'").fetchone()[0], 'mock-v1')

    def test_member_interface_rejects_ka_in_preview_and_worker_preparation(self):
        bind_member_interface(self.platform, ACTOR, apply=True)
        with self.platform.tx() as conn:
            conn.execute("UPDATE demands SET buyer_id='AGENCY_A',receiver_id='AGENCY_A',route_id='AGENCY_TO_A',sale_project='KA' WHERE id=?",
                         (self.fixture.d['id'],))
        self.assertEqual(self.fixture.preview(self.fixture.lead())['reason'], 'KA_DELIVERY_NOT_CONFIGURED')
        with self.platform.connect() as conn, self.assertRaises(Fault) as caught:
            self.platform._zmh_request(conn, ACTOR, {'sale_project': 'KA'}, {}, {})
        self.assertEqual(caught.exception.code, 'ZMH_PROJECT_MISMATCH')

    def test_new_route_stays_disabled_and_other_probe_binding_is_rejected(self):
        with self.platform.tx() as conn:
            conn.execute("UPDATE partners SET name='旧媒体' WHERE id='MEDIA_A'")
            conn.execute("UPDATE partners SET name='易车' WHERE id='MEDIA_C'")
        probe_id = self.probe()
        result = bind_member_interface(self.platform, ACTOR, apply=True, probe_id=probe_id)
        self.assertTrue(result['create_disabled_route'])
        self.assertEqual(self.status(), 'CONNECTED')
        with self.platform.connect() as conn:
            self.assertEqual(conn.execute('SELECT enabled FROM routes WHERE id=?', (result['route_id'],)).fetchone()[0], 0)
        with self.platform.tx() as conn:
            conn.execute('INSERT INTO routes(id,org_id,buyer_id,receiver_id,media_id) VALUES(?,?,?,?,?)',
                         ('another-route', 'demo', 'AGENCY_A', 'AGENCY_A', 'MEDIA_C'))
        with self.assertRaises(Fault) as caught:
            bind_member_interface(self.platform, ACTOR, apply=True, route_id='another-route', probe_id=probe_id)
        self.assertEqual(caught.exception.code, 'ZMH_PROBE_ALREADY_BOUND')


if __name__ == '__main__':
    unittest.main()
