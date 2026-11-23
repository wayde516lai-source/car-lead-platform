import unittest
from tempfile import TemporaryDirectory
from domain import Platform,Fault
from demand_sync import DemandSync
from test_synced_demands import record
A={'id':'u_admin','org_id':'demo','role':'admin'}
class ProjectModesTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.p=Platform(self.temp.name);self.p.seed()
  with self.p.tx() as c:c.execute("UPDATE demands SET status='PAUSED'");c.execute("UPDATE leads SET status='BLOCKED'")
  self.data={'title':'test','headers':list(record()['values']),'records':[record(qty='10')]};self.sync=DemandSync(self.p,lambda url:self.data)
  self.ids={}
  for project in ('MEMBER','KA'):
   source=self.sync.save(A,{'name':project,'url':'https://docs.qq.com/sheet/DTESTMODE'+project+'?tab=BB08J2','partner_id':'MEDIA_A','media_id':'MEDIA_A','sale_project':project,'enabled':0})
   # Different media requirement numbers keep membership and KA records distinct.
   self.data['records']=[record(key=project,qty='10')];self.sync.run(A,source['id']);self.ids[project]=self.sync.projects(A,source['id'])['items'][0]['demand']['id']
 def tearDown(self):self.temp.cleanup()
 def config(self,project):return next(p for p in self.p.catalogs(A)['partners'] if p['id']=='MEDIA_A')['cooperation_projects'][0 if project=='MEMBER' else 1]
 def mode(self,project,mode):return self.p.project_mode_update(A,'MEDIA_A',project,{'matching_mode':mode,'version':self.config(project)['version']})
 def lead(self,n=1):return self.p.create_lead(A,{'supplier_id':'SOURCE_A','name':'独立项目测试','phone':f'13900009{n:03}','brand_name':'宝马','model':'宝马 X5','city':'保山市','is_sold':'否'})['lead_id']
 def test_two_projects_and_separate_modes(self):
  self.assertEqual({c['project'] for c in next(p for p in self.p.catalogs(A)['partners'] if p['id']=='MEDIA_A')['cooperation_projects']},{'KA','MEMBER'})
  self.mode('KA','AUTO');self.assertEqual(self.config('KA')['matching_mode'],'AUTO');self.assertEqual(self.config('MEMBER')['matching_mode'],'MANUAL')
  rows=self.p.demand_summary(A)['items'];self.assertEqual({g['project'] for g in rows if g['buyer_id']=='MEDIA_A' and g['media_id']=='MEDIA_A' and g['active_count']},{'MEMBER','KA'})
 def test_auto_excludes_manual_and_manual_rejects_auto(self):
  self.mode('KA','AUTO');lid=self.lead();r=self.p.preview(A,[lid],[self.ids['KA']],{self.ids['KA']:'KA'});self.assertEqual(r['items'][0]['reason'],'PROJECT_AUTO_ONLY')
  self.assertEqual(self.p.dispatch(A,[(lid,self.ids['KA'])],'blocked',projects={self.ids['KA']:'KA'})['created'],0)
  self.assertEqual(self.p.auto_run(A)['created'],1)
  c=self.p.connect();row=c.execute('SELECT demand_id,dispatch_mode FROM deliveries WHERE lead_id=?',(lid,)).fetchone();c.close();self.assertEqual(tuple(row),(self.ids['KA'],'AUTO'))
 def test_manual_same_rules_and_no_auto_after_switch(self):
  self.mode('KA','AUTO');self.mode('KA','MANUAL');lid=self.lead();self.assertEqual(self.p.auto_run(A)['created'],0)
  self.assertEqual(self.p.dispatch(A,[(lid,self.ids['MEMBER'])],'manual',projects={self.ids['MEMBER']:'MEMBER'})['created'],1)
  self.assertEqual(self.p.preview(A,[lid],[self.ids['KA']],{self.ids['KA']:'KA'})['items'][0]['reason'],'FINAL_MEDIA_DUPLICATE')
 def test_permissions_stale_versions_and_tenant(self):
  version=self.config('MEMBER')['version'];self.mode('MEMBER','AUTO')
  with self.assertRaises(Fault):self.p.project_mode_update(A,'MEDIA_A','MEMBER',{'matching_mode':'MANUAL','version':version})
  with self.assertRaises(Fault):self.p.project_mode_update({'id':'u_analyst','role':'analyst','org_id':'demo'},'MEDIA_A','KA',{'matching_mode':'AUTO','version':1})
  with self.assertRaises(Fault):self.p.project_mode_update({'id':'u_other','role':'admin','org_id':'other'},'MEDIA_A','KA',{'matching_mode':'AUTO','version':1})
 def test_new_sync_inherits_project_mode(self):
  self.mode('MEMBER','AUTO');self.data['records']=[record(key='NEW',qty='10')]
  source=next(s for s in self.sync.sources(A) if s['sale_project']=='MEMBER');self.sync.run(A,source['id'])
  self.assertTrue(all(row['demand']['auto_enabled']==1 for row in self.sync.projects(A,source['id'])['items']))

 def test_switch_manual_cancels_unsent_auto_and_keeps_integrity(self):
  self.mode('KA','AUTO');lid=self.lead();did=self.ids['KA'];r=self.p.dispatch(A,[(lid,did)],'queued-auto',projects={did:'KA'},mode='AUTO');self.assertEqual(r['created'],1)
  self.mode('KA','MANUAL');delivery=self.p.detail(A,'deliveries',r['items'][0]['delivery_id']);self.assertEqual(delivery['send_status'],'CANCELED');self.assertTrue(self.p.integrity(A)['passed'])
 def test_project_modes_persist_after_reopen(self):
  self.mode('KA','AUTO');self.p=Platform(self.temp.name);self.assertEqual(self.config('KA')['matching_mode'],'AUTO');self.assertEqual(self.config('MEMBER')['matching_mode'],'MANUAL')
