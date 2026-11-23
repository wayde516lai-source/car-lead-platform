"""即使绕过前端提交未开通项目，链接新增与修改也不能写入错误配置。"""
import tempfile
import unittest

from domain import Fault, Platform
from demand_sync import DemandSync

ACTOR = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}


class DemandProjectGuardTests(unittest.TestCase):
    def test_unopened_project_cannot_create_or_update_source(self):
        with tempfile.TemporaryDirectory() as folder:
            platform = Platform(folder)
            platform.seed()
            with platform.tx() as conn:
                conn.execute("DELETE FROM partner_projects WHERE org_id='demo' AND partner_id='MEDIA_A' AND project='KA'")
            sync = DemandSync(platform)
            config = {'name': '隔离需求', 'url': 'https://docs.qq.com/sheet/DPROJECTGUARD?tab=BB08J2',
                      'partner_id': 'MEDIA_A', 'media_id': 'MEDIA_A', 'sale_project': 'KA', 'enabled': 0}
            with self.assertRaises(Fault) as caught:
                sync.save(ACTOR, config)
            self.assertEqual(caught.exception.code, 'SYNC_PROJECT_REQUIRED')
            self.assertEqual(sync.sources(ACTOR), [])
            source = sync.save(ACTOR, {**config, 'sale_project': 'MEMBER'})
            with self.assertRaises(Fault) as caught:
                sync.save(ACTOR, {'version': source['version'], 'sale_project': 'KA'}, source['id'])
            self.assertEqual(caught.exception.code, 'SYNC_PROJECT_REQUIRED')
            self.assertEqual(sync.sources(ACTOR)[0]['sale_project'], 'MEMBER')
