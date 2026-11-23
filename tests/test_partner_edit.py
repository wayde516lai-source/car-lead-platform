"""合作主体资料编辑覆盖权限、并发及项目限制。"""
import tempfile
import unittest

from domain import Fault, Platform, later
from partner_edit import update_partner

ACTOR = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}


class PartnerEditTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.p = Platform(self.folder.name)
        self.p.seed()
        with self.p.tx() as conn:
            conn.execute('INSERT INTO partners(id,org_id,name,role,contract_end,cooperation_project) VALUES(?,?,?,?,?,?)',
                         ('test-party', 'demo', '旧主体名', 'AGENCY', later(days=30), 'MEMBER'))
            self.p._ensure_partner_projects(conn, 'demo', 'test-party', ('MEMBER',))

    def test_rename_price_and_note_do_not_open_extra_projects(self):
        update_partner(self.p, ACTOR, 'test-party', {'name': '新主体名', 'cooperation_price_yuan': '40.25',
                                                   'cooperation_note': '更正基本资料', 'expected': {'name': '旧主体名'}})
        conn = self.p.connect()
        try:
            row = self.p.one(conn, 'partners', 'test-party', ACTOR)
            self.assertEqual(row['name'], '新主体名')
            self.assertEqual(row['cooperation_price_fen'], 4025)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM partner_projects WHERE partner_id='test-party'").fetchone()[0], 1)
        finally:
            conn.close()
        for data, code in [({'expected': {'name': '旧主体名'}, 'name': '冲突名'}, 'VERSION_CONFLICT'),
                           ({'role':'MEDIA'}, 'PARTNER_ROLE_IMMUTABLE'),
                           ({'cooperation_project':'KA'}, 'COOPERATION_PROJECT_REQUIRED'),
                           ({'cooperation_price_yuan':'1.005'}, 'PRICE_INVALID')]:
            with self.subTest(code=code), self.assertRaises(Fault) as caught:
                update_partner(self.p, ACTOR, 'test-party', data)
            self.assertEqual(caught.exception.code, code)

    def test_permissions_and_org_isolation(self):
        for actor, code in [({'id':'u_operator','org_id':'demo','role':'operator'}, 'FORBIDDEN'),
                            ({'id':'u_other','org_id':'other','role':'admin'}, 'NOT_FOUND')]:
            with self.subTest(code=code), self.assertRaises(Fault) as caught:
                update_partner(self.p, actor, 'test-party', {'name':'越权名'})
            self.assertEqual(caught.exception.code, code)
