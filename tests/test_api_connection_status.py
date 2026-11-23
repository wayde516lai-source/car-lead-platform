"""API联调状态按渠道、主体项目隔离，模拟与配置不得假冒接通。"""
import json
import tempfile
import unittest
from unittest.mock import patch

from domain import Platform, now
from security import digest

ACTOR = {'id': 'u_admin', 'role': 'admin', 'org_id': 'demo'}


class ApiConnectionStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = Platform(self.temp.name)
        self.p.seed()

    def catalog(self):
        return self.p.catalogs(ACTOR)

    def channel(self, channel='ADS'):
        return next(row for row in self.catalog()['channels'] if row['id'] == channel)

    def project(self, partner, project='MEMBER'):
        subject = next(row for row in self.catalog()['partners'] if row['id'] == partner)
        return next(row for row in subject['cooperation_projects'] if row['project'] == project)

    def source_row(self):
        return {'source_record_id':'STATUS-001','phone':'19900007201','name':'接口状态虚构客户',
                'brand_name':'宝马','series_name':'宝马 X5','city':'上海市'}

    def test_key_config_manual_and_simulation_are_not_connection_proof(self):
        self.p.issue_source_key(ACTOR, 'ADS')
        self.assertEqual(self.channel()['api_status'], 'NOT_CONNECTED')
        self.p.source_ingest(ACTOR, 'ADS', self.source_row())
        self.assertEqual(self.channel()['api_status_label'], '未接通')
        self.assertTrue(all(row['api_status']=='NOT_CONNECTED'
                            for p in self.catalog()['partners'] for row in p['cooperation_projects']))
        self.p.worker(ACTOR)
        self.assertTrue(all(row['api_status']=='NOT_CONNECTED'
                            for p in self.catalog()['partners'] for row in p['cooperation_projects']))

    def test_inbound_actual_key_success_and_rotation_are_channel_scoped(self):
        key = self.p.issue_source_key(ACTOR, 'ADS')
        actor = {'id':'open:'+key['key_id'],'org_id':'demo','role':'operator'}
        self.p.source_ingest(actor, 'ADS', self.source_row())
        self.assertEqual(self.channel()['api_status'], 'CONNECTED')
        self.assertEqual(self.channel('PURCHASE')['api_status'], 'NOT_CONNECTED')
        self.assertNotIn(key['secret'], json.dumps(self.catalog()))
        self.p.issue_source_key(ACTOR, 'ADS')
        self.assertEqual(self.channel()['api_status'], 'NOT_CONNECTED')

    def test_legacy_inbound_success_supported_and_test_identity_excluded(self):
        key = self.p.issue_source_key(ACTOR, 'ADS')
        with self.p.tx() as conn:
            self.p.audit(conn, {**ACTOR,'id':'open:TEST-SOURCE_ADS'}, 'LEAD_INGEST', 'old-test',
                         {'channel_id':'ADS','status':'CREATED'})
        self.assertEqual(self.channel()['api_status'], 'NOT_CONNECTED')
        with self.p.tx() as conn:
            self.p.audit(conn, {**ACTOR,'id':'open:'+key['key_id']}, 'LEAD_INGEST', 'old-real',
                         {'channel_id':'ADS','status':'DUPLICATE'})
        self.assertEqual(self.channel()['api_status'], 'CONNECTED')
        self.assertTrue(self.channel()['api_verified_at'])

    def test_outbound_ack_is_project_scoped_not_final_media_or_ka(self):
        from test_zhemanhui_integration import ZmhIntegrationTests
        fixture = ZmhIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.p = fixture.p
        self.assertEqual(self.project('AGENCY_A')['api_status'], 'NOT_CONNECTED')
        fixture.send()
        self.assertEqual(self.project('AGENCY_A')['api_status_label'], '接通')
        self.assertEqual(self.project('AGENCY_A','KA')['api_status'], 'NOT_CONNECTED')
        self.assertEqual(self.project('MEDIA_A')['api_status'], 'NOT_CONNECTED')
        with self.p.tx() as conn:
            conn.execute("UPDATE partners SET enabled=0 WHERE id='AGENCY_A'")
        self.assertEqual(self.project('AGENCY_A')['api_status'], 'CONNECTED')
        with self.p.tx() as conn:
            conn.execute("UPDATE routes SET adapter_version='another-api' WHERE id='AGENCY_TO_A'")
        self.assertEqual(self.project('AGENCY_A')['api_status'], 'NOT_CONNECTED')

    def test_direct_media_success_does_not_connect_agency_or_ka(self):
        from test_yiche_push import YichePush
        fixture=YichePush()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.p=fixture.platform
        with self.p.tx() as conn:
            conn.execute("UPDATE demands SET sale_project='MEMBER' WHERE id=?",(fixture.demand['id'],))
        fixture.live_queue()
        with patch('yiche_transport.send',return_value=('ACKED',200,'')):
            self.p.worker(ACTOR)
        self.assertEqual(self.project('MEDIA_A')['api_status'],'CONNECTED')
        self.assertEqual(self.project('MEDIA_A','KA')['api_status'],'NOT_CONNECTED')
        self.assertEqual(self.project('AGENCY_A')['api_status'],'NOT_CONNECTED')

    def test_failed_unknown_and_unlinked_probe_do_not_connect(self):
        from test_zhemanhui_integration import ZmhIntegrationTests
        fixture = ZmhIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.p = fixture.p
        import os
        from urllib.parse import parse_qs,urlsplit
        created=fixture.f.send(fixture.lid);fixture.id=created['items'][0]['delivery_id']
        captured={}
        def unknown(request,enabled):
            captured['request']=request
            return {'status':'UNKNOWN','error':'TEST_UNKNOWN'}
        with patch.dict(os.environ,{'ZMH_ENABLE_TEST_PUSH':'1'}),patch('zhemanhui.send_test',unknown):
            self.p.worker(ACTOR)
        callback=parse_qs(urlsplit(captured['request'].full_url).query)['sourceUrl'][0]
        fixture.params={key:values[0] for key,values in parse_qs(urlsplit(callback).query).items()}
        with self.p.tx() as conn:
            self.p.audit(conn, ACTOR, 'ZMH_SEND_RESULT', fixture.id, {'status':'FAILED'})
            conn.execute('INSERT INTO zmh_submissions(id,org_id,token_hash,created_at,send_status) VALUES(?,?,?,?,?)',
                         ('unlinked-probe','demo',digest('fake-local-token'),now(),'ACKED'))
        self.assertEqual(self.project('AGENCY_A')['api_status'], 'NOT_CONNECTED')
        fixture.p.receive_zmh_callback({**fixture.params,'resultCode':'1','code':'-12'})
        self.assertEqual(self.project('AGENCY_A')['api_status'], 'CONNECTED')
        other = self.p.catalogs({**ACTOR,'org_id':'other'})
        self.assertEqual(other['channels'], [])
        self.assertEqual(other['partners'], [])


if __name__ == '__main__':
    unittest.main()
