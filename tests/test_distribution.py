import concurrent.futures
import json
import unittest
from tempfile import TemporaryDirectory
from domain import Platform,Fault,now,later

A={'id':'u_admin','org_id':'demo','role':'admin'}
class DistributionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=TemporaryDirectory();self.p=Platform(self.tmp.name);self.p.seed()
  c=self.p.connect()
  self.demands={r['media_id']:dict(r) for r in c.execute("SELECT * FROM demands WHERE route_id IN ('DIRECT_A','DIRECT_B')")};c.close()
  self.demands['MEDIA_C']=self.p.create_demand(A,{'demand_no':'C-TEST','route_id':'DIRECT_C','series_id':'BMW_X5','target_total':30,'daily_limit':30})
  d=self.demands['MEDIA_C'];self.p.demand_action(A,d['id'],'submit',1);self.p.demand_action({'id':'u_business','role':'business','org_id':'demo'},d['id'],'approve',2)
  with self.p.tx() as c:c.execute("UPDATE leads SET status='BLOCKED'")
  self.l=self.p.create_lead(A,{'name':'分发测试','phone':'13900008765','brand_name':'宝马','series_id':'BMW_X5','city':'上海市','is_sold':'否'})['lead_id']
 def tearDown(self):self.tmp.cleanup()
 def rules(self,**data):
  s=self.p.catalogs(A)['settings'];return self.p.settings_update(A,dict(version=s['version'],reason='业务验收',**data))
 def send(self,media,project='MEMBER',key=None):
  d=self.demands[media]['id'];return self.p.dispatch(A,[(self.l,d)],key or media+project,projects={d:project})
 def reason(self,media,project='MEMBER'):
  d=self.demands[media]['id'];return self.p.preview(A,[self.l],[d],{d:project})['items'][0]['reason']
 def enable(self,*media):
  self.rules(auto_dispatch_enabled=1)
  for m in media:
   d=self.p.detail(A,'demands',self.demands[m]['id']);self.p.edit_demand(A,d['id'],{'version':d['version'],'auto_enabled':1,'sale_project':'MEMBER'})
 def test_star_boundaries(self):
  for n,stars in [(0,5),(19,5),(20,4),(39,4),(40,3),(60,2),(80,1),(99,1),(100,0),(110,0)]:
   with self.subTest(n=n):self.assertEqual(self.p.demand_progress({'valid_count':n,'target_total':100})['urgency_stars'],stars)
  d=self.p.detail(A,'demands',self.demands['MEDIA_A']['id']);self.assertIn('attainment_rate',d)
 def test_cap_and_change_to_three(self):
  self.assertEqual(self.send('MEDIA_A')['created'],1);self.assertEqual(self.send('MEDIA_B')['created'],1)
  self.assertEqual(self.reason('MEDIA_C'),'MAX_SALES_REACHED')
  self.rules(max_media_count=3);self.assertEqual(self.send('MEDIA_C')['created'],1)
 def test_ka_and_member_combinations(self):
  self.assertEqual(self.send('MEDIA_A','KA')['created'],1)
  self.assertEqual(self.reason('MEDIA_B','KA'),'KA_LIMIT_REACHED')
  self.assertEqual(self.reason('MEDIA_B','MEMBER'),None)
  self.assertEqual(self.send('MEDIA_B','MEMBER')['created'],1)
 def test_imported_history_consumes_slot_and_ka(self):
  old=self.p.create_lead(A,{'name':'已售','phone':'13900008766','brand_name':'宝马','series_id':'BMW_X5','city':'上海市','is_sold':'是','sale_media_id':'MEDIA_A','sale_project':'KA','sold_at':now()})
  self.l=old['lead_id'];self.assertEqual(self.reason('MEDIA_A'),'FINAL_MEDIA_DUPLICATE');self.assertEqual(self.reason('MEDIA_B','KA'),'KA_LIMIT_REACHED')
  self.assertEqual(self.send('MEDIA_B')['created'],1);self.assertEqual(self.reason('MEDIA_C'),'MAX_SALES_REACHED')
 def test_invalid_and_unknown_still_consume(self):
  r=self.send('MEDIA_A');self.p.worker(A);self.p.mock_callback(A,r['items'][0]['delivery_id'],'STORED');self.p.mock_callback(A,r['items'][0]['delivery_id'],'INVALID')
  with self.p.tx() as c:c.execute("UPDATE routes SET mode='TIMEOUT' WHERE id='DIRECT_B'")
  self.send('MEDIA_B');self.p.worker(A);self.assertEqual(self.reason('MEDIA_C'),'MAX_SALES_REACHED')
 def test_same_media_across_routes_and_idempotency(self):
  first=self.send('MEDIA_A',key='one');self.assertEqual(self.send('MEDIA_A',key='one'),first)
  self.assertEqual(self.reason('MEDIA_A'),'FINAL_MEDIA_DUPLICATE')
  c=self.p.connect();d=dict(c.execute("SELECT * FROM demands WHERE route_id='AGENCY_TO_A'").fetchone());c.close()
  self.assertEqual(self.p.preview(A,[self.l],[d['id']])['items'][0]['reason'],'FINAL_MEDIA_DUPLICATE')
 def test_concurrent_dispatch_never_oversells(self):
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:res=list(pool.map(lambda m:self.send(m),['MEDIA_A','MEDIA_B','MEDIA_C']))
  self.assertEqual(sum(r['created'] for r in res),2)
 def test_duplicate_brand_window_and_lifetime_are_independent(self):
  self.send('MEDIA_A');self.send('MEDIA_B');self.p.worker(A)
  with self.p.tx() as c:
   c.execute('UPDATE deliveries SET first_sent_at=? WHERE lead_id=?',(later(days=-20),self.l))
   c.execute('UPDATE leads SET collected_at=? WHERE id=?',(later(days=-20),self.l))
  old=self.l
  self.l=self.p.create_lead(A,{'name':'同品牌不同车型','phone':'13900008765','brand_name':'宝马','series_id':'BMW_3','city':'上海市','is_sold':'否'})['lead_id']
  c=self.p.connect();lead=self.p.one(c,'leads',self.l,A);self.assertEqual(len(self.p._policy_records(c,A,lead)),2);c.close()
  self.rules(dedup_window_days=10)
  c=self.p.connect();self.assertEqual(len(self.p._policy_records(c,A,lead)),0);lead=self.p.one(c,'leads',old,A);self.assertEqual(len(self.p._policy_records(c,A,lead)),2);c.close()
 def test_rule_permission_version_and_reason(self):
  s=self.p.catalogs(A)['settings']
  for actor,data in [({'id':'u_operator','role':'operator','org_id':'demo'},dict(version=s['version'],reason='x')), (A,dict(version=0,reason='x')),(A,dict(version=s['version'],reason='')),(A,dict(version=s['version'],reason='x',dedup_window_days=0))]:
   with self.assertRaises(Fault):self.p.settings_update(actor,data)
  self.rules(dedup_window_days=10)
  c=self.p.connect();r=c.execute("SELECT detail FROM audit_log WHERE action='DISTRIBUTION_RULES_UPDATED'").fetchone();c.close();self.assertEqual(json.loads(r[0])['after']['dedup_window_days'],10)
 def test_auto_disabled_and_unique_match(self):
  self.assertTrue(self.p.auto_run(A)['paused']);self.enable('MEDIA_A')
  r=self.p.auto_run(A);self.assertGreater(r['created'],0)
  c=self.p.connect();row=c.execute("SELECT * FROM deliveries WHERE lead_id=?",(self.l,)).fetchone();c.close()
  self.assertEqual(row['dispatch_mode'],'AUTO');self.assertEqual(row['send_status'],'ACKED');self.assertEqual(row['sale_project'],'MEMBER');self.assertIsNotNone(row['rule_version'])
  self.assertEqual(self.p.auto_run(A)['created'],0)
 def test_auto_ambiguity_binding_and_shared_ka_rules(self):
  self.enable('MEDIA_A','MEDIA_B');self.assertEqual(self.p.auto_run(A)['created'],0)
  waiting=next(x for x in self.p.auto_status(A)['items'] if x['lead_id']==self.l);self.assertEqual(waiting['status'],'WAITING_MANUAL')
  self.p.auto_bind(A,[(self.l,self.demands['MEDIA_A']['id'])]);self.assertGreater(self.p.auto_run(A)['created'],0)
  # The remaining media can take a second MEMBER sale; never a third.
  self.p.auto_run(A);self.assertEqual(self.reason('MEDIA_C'),'MAX_SALES_REACHED')
 def test_auto_never_calls_live_adapter(self):
  self.enable('MEDIA_A');self.p.yiche_live=object();self.assertEqual(self.p.auto_run(A)['reason'],'LOCAL_ONLY')
 def test_lower_cap_rechecks_queue_preserves_sent_history(self):
  self.send('MEDIA_A');self.send('MEDIA_B');self.rules(max_media_count=1)
  c=self.p.connect();rows=list(c.execute('SELECT send_status FROM deliveries WHERE lead_id=?',(self.l,)));c.close()
  self.assertEqual(sum(r[0]=='QUEUED' for r in rows),1);self.assertEqual(sum(r[0]=='CANCELED' for r in rows),1)
  self.assertTrue(self.p.integrity(A)['passed'])
 def test_cancel_releases_sale_slot(self):
  r=self.send('MEDIA_A')
  with self.p.tx() as c:self.p._cancel(c,A,r['items'][0]['delivery_id'],'TEST_UNSENT')
  self.assertEqual(self.reason('MEDIA_A'),None)
 def test_auto_and_manual_share_ka_limit(self):
  self.send('MEDIA_A','KA');self.enable('MEDIA_B')
  d=self.p.detail(A,'demands',self.demands['MEDIA_B']['id']);self.p.edit_demand(A,d['id'],{'version':d['version'],'sale_project':'KA','auto_enabled':1})
  self.assertEqual(self.p.auto_run(A)['created'],0)
  waiting=next(x for x in self.p.auto_status(A)['items'] if x['lead_id']==self.l)
  self.assertEqual(waiting['candidates'][0]['reason'],'KA_LIMIT_REACHED')
  d=self.p.detail(A,'demands',d['id']);self.p.edit_demand(A,d['id'],{'version':d['version'],'sale_project':'MEMBER'})
  self.assertEqual(self.p.auto_run(A)['created'],1)
  c=self.p.connect();manual=c.execute("SELECT send_status FROM deliveries WHERE lead_id=? AND dispatch_mode='MANUAL'",(self.l,)).fetchone()[0];c.close();self.assertEqual(manual,'QUEUED')
 def test_failed_unsent_releases_but_stored_failure_does_not(self):
  r=self.send('MEDIA_A')
  with self.p.tx() as c:c.execute("UPDATE deliveries SET send_status='FAILED' WHERE id=?",(r['items'][0]['delivery_id'],))
  self.assertEqual(self.reason('MEDIA_A'),None)
  with self.p.tx() as c:c.execute("UPDATE deliveries SET storage_status='STORED' WHERE id=?",(r['items'][0]['delivery_id'],))
  self.assertEqual(self.reason('MEDIA_A'),'FINAL_MEDIA_DUPLICATE')
 def test_global_pause_and_unknown_history(self):
  d=self.demands['MEDIA_A']['id'];self.p.dispatch(A,[(self.l,d)],'legacy')
  self.assertEqual(self.reason('MEDIA_B','KA'),'SALE_PROJECT_UNKNOWN')
  self.rules(dispatch_enabled=0,auto_dispatch_enabled=1)
  self.assertEqual(self.reason('MEDIA_B'),'DISPATCH_PAUSED');self.assertTrue(self.p.auto_run(A)['paused'])
