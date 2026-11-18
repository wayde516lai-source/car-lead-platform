import os,secrets,unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from domain import Fault,now
from security import digest
import test_synced_demands as synced
A=synced.A
from zhemanhui import CALLBACK_PATH
class ZmhIntegrationTests(unittest.TestCase):
 def setUp(self):
  self.f=synced.SyncedDemandTests();self.f.setUp();self.p=self.f.p
  with self.p.tx() as c:
   c.execute("UPDATE routes SET adapter_version='zhemanhui-v1' WHERE id='AGENCY_TO_A'")
   c.execute("UPDATE demands SET buyer_id='AGENCY_A',receiver_id='AGENCY_A',route_id='AGENCY_TO_A' WHERE id=?",(self.f.d['id'],))
   c.execute('INSERT INTO zmh_routes VALUES(?,?,?,1)',('AGENCY_TO_A','demo','https://saas.asoapple.cn'+CALLBACK_PATH))
  self.lid=self.f.lead()
  with self.p.tx() as c:
   l=self.p.one(c,'leads',self.lid,A)
   for k,v,r in [('city',l['city_code'],'2501'),('series',l['series_id'],'11433')]:c.execute('INSERT INTO zmh_mappings VALUES(?,?,?,?,?)',('demo','AGENCY_TO_A',k,v,r))
 def tearDown(self):self.f.tearDown()
 def send(self):
  result=self.f.send(self.lid);self.id=result['items'][0]['delivery_id'];self.request=None
  def sent(req,enabled):self.request=req;return {'status':'ACKED','validity':'PENDING'}
  with patch.dict(os.environ,{'ZMH_ENABLE_TEST_PUSH':'1'}),patch('zhemanhui.send_test',sent):self.p.worker(A)
  values=parse_qs(urlsplit(self.request.full_url).query);self.params={k:v[0] for k,v in parse_qs(urlsplit(values['sourceUrl'][0]).query).items()}
 def test_accepted_pending_then_valid_duplicate_conflict(self):
  self.send();d=self.p.detail(A,'deliveries',self.id);self.assertEqual(d['validity_status'],'PENDING');self.assertEqual(d['send_status'],'ACKED')
  params=dict(self.params,resultCode='0',code='2001');self.assertEqual(self.p.receive_zmh_callback(params)['validity'],'VALID')
  self.assertTrue(self.p.receive_zmh_callback(params)['duplicate']);d=self.p.detail(A,'deliveries',self.id);self.assertEqual(d['quota_state'],'VALID');self.assertEqual(d['evidence_level'],'AGENCY_REPORTED')
  self.assertTrue(self.p.receive_zmh_callback(dict(params,code='-12'))['conflict']);self.assertEqual(self.p.detail(A,'deliveries',self.id)['validity_status'],'VALID')
 def test_failed_callback_releases_quota_and_bad_token_rejected(self):
  self.send();params=dict(self.params,resultCode='1',code='-12')
  with self.assertRaises(Fault):self.p.receive_zmh_callback(dict(params,callback_token='wrong'))
  self.p.receive_zmh_callback(params);d=self.p.detail(A,'deliveries',self.id);self.assertEqual(d['validity_status'],'INVALID');self.assertEqual(d['quota_state'],'RELEASED');self.assertIn('姓名',d['error'])
 def test_probe_can_receive_callback_without_business_delivery(self):
  token=secrets.token_urlsafe(32)
  with self.p.tx() as c:c.execute('INSERT INTO zmh_submissions(id,org_id,token_hash,created_at) VALUES(?,?,?,?)',('probe','demo',digest(token),now()))
  self.assertEqual(self.p.receive_zmh_callback(dict(delivery_id='probe',callback_token=token,resultCode='0',code='2001'))['validity'],'VALID')
