import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from domain import Platform,later,now
A={'id':'u_admin','org_id':'demo','role':'admin'}
class LeadExpiryTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.p=Platform(self.temp.name);self.p.seed()
  self.l=self.p.create_lead(A,{'name':'时效测试','phone':'13900008651','brand_name':'宝马','series_id':'BMW_X5','city':'上海市','is_sold':'否'})['lead_id']
  self.d=next(d['id'] for d in self.p.list(A,'demands')['items'] if d['route_id']=='DIRECT_B')
 def tearDown(self):self.temp.cleanup()
 def test_expired_list_detail_preview_dispatch_and_filters_agree_without_maintenance(self):
  with self.p.tx() as c:c.execute("UPDATE leads SET expires_at=?,status='READY' WHERE id=?",(later(hours=-1),self.l))
  detail=self.p.detail(A,'leads',self.l);self.assertEqual(detail['status'],'EXPIRED')
  rows=self.p.list(A,'leads')['items'];self.assertEqual(next(l for l in rows if l['id']==self.l)['status'],'EXPIRED')
  self.assertNotIn(self.l,[l['id'] for l in self.p.list(A,'leads',status='READY')['items']])
  self.assertIn(self.l,[l['id'] for l in self.p.list(A,'leads',status='EXPIRED')['items']])
  preview=self.p.preview(A,[self.l],[self.d]);self.assertEqual(preview['items'][0]['reason'],'LEAD_EXPIRED')
  result=self.p.dispatch(A,[(self.l,self.d)],'expired-test',projects={self.d:'MEMBER'});self.assertEqual(result['created'],0);self.assertEqual(result['items'][0]['reason'],'LEAD_EXPIRED')
  c=self.p.connect();self.assertEqual(c.execute('SELECT status FROM leads WHERE id=?',(self.l,)).fetchone()[0],'READY');c.close()
 def test_demand_age_has_its_own_reason_and_does_not_globally_expire(self):
  with self.p.tx() as c:
   c.execute('UPDATE leads SET collected_at=?,expires_at=? WHERE id=?',(later(hours=-48),later(days=2),self.l))
   c.execute('UPDATE demands SET max_age_hours=24 WHERE id=?',(self.d,))
  self.assertEqual(self.p.detail(A,'leads',self.l)['status'],'READY')
  self.assertEqual(self.p.preview(A,[self.l],[self.d])['items'][0]['reason'],'DEMAND_LEAD_TOO_OLD')
  with self.p.tx() as c:c.execute('UPDATE demands SET max_age_hours=72 WHERE id=?',(self.d,))
  self.assertTrue(self.p.preview(A,[self.l],[self.d])['items'][0]['eligible'])
 def test_clock_boundary_and_status_precedence(self):
  row={'status':'READY','expires_at':'2026-11-12T12:00:00+08:00'}
  with patch('domain.now',return_value='2026-11-12T03:59:59+00:00'):self.assertEqual(self.p._effective_lead(row)['status'],'READY')
  with patch('domain.now',return_value='2026-11-12T04:00:00+00:00'):self.assertEqual(self.p._effective_lead(row)['status'],'EXPIRED')
  for status in ['BLOCKED','DELETED','QUARANTINED']:
   self.assertEqual(self.p._effective_lead(dict(row,status=status),'2026-11-13T04:00:00+00:00')['status'],status)
 def test_dashboard_ready_count_matches_list(self):
  with self.p.tx() as c:c.execute('UPDATE leads SET expires_at=? WHERE id=?',(later(hours=-1),self.l))
  self.assertEqual(self.p.reports(A)['ready'],self.p.list(A,'leads',status='READY')['total'])
