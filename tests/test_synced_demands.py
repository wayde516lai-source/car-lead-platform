import copy,json,unittest
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from unittest.mock import patch
from domain import Platform,Fault,now,later,day
from demand_sync import DemandSync
A={'id':'u_admin','org_id':'demo','role':'admin'}
URL='https://docs.qq.com/sheet/DVFNzZWxTWkNZV3RQ?tab=BB08J2'
def record(key='BMW-DAILY',brand='宝马',model='全系',regions='保山市,上海市,北京市',qty='1',state='',end='待定'):
 v={'下单日期':'2026-11-01','品牌':brand,'车型':model,'城市&地区':regions,'类型':'品牌城市','需求编号':key,'开始时间':'2026-11-01','结束时间':end,'下单量':qty,'需求执行状态（渠道维护）':state,'结束时间（渠道编辑）':'','其他信息备注（渠道编辑）':'完整备注'}
 return {'key':key,'row_number':2,'values':v,'raw_values':v.copy()}
class SyncedDemandTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.p=Platform(self.temp.name);self.p.seed();self.seq=0
  with self.p.tx() as c:c.execute("UPDATE demands SET status='PAUSED'");c.execute("UPDATE leads SET status='BLOCKED'")
  self.data={'title':'会员需求','headers':list(record()['values']),'records':[record()]}
  self.sync=DemandSync(self.p,lambda url:copy.deepcopy(self.data));self.source=self.sync.save(A,{'name':'易车会员','url':URL,'partner_id':'MEDIA_A','media_id':'MEDIA_A','sale_project':'MEMBER','enabled':0});self.run_sync()
 def tearDown(self):self.temp.cleanup()
 def run_sync(self):
  result=self.sync.run(A,self.source['id']);self.assertEqual(result['status'],'SUCCESS',result)
  c=self.p.connect();self.ds=[dict(r) for r in c.execute('SELECT * FROM demands WHERE external_project_id IS NOT NULL ORDER BY demand_no')];c.close();self.d=self.ds[0] if self.ds else None;return result
 def lead(self,model='宝马 X5',city='保山市',brand='宝马'):
  self.seq+=1;return self.p.create_lead(A,{'supplier_id':'SOURCE_A','name':'同步匹配测试','phone':f'13900005{self.seq:03}','brand_name':brand,'model':model,'city':city,'is_sold':'否'})['lead_id']
 def preview(self,lid,d=None):return self.p.preview(A,[lid],[(d or self.d)['id']])['items'][0]
 def send(self,lid,key='send',mode='MANUAL'):
  return self.p.dispatch(A,[(lid,self.d['id'])],key,projects={self.d['id']:'MEMBER'},mode=mode)
 def test_complete_fields_active_membership_and_multi_regions(self):
  self.assertEqual(self.d['status'],'ACTIVE');self.assertEqual(self.d['quantity_basis'],'DAILY');self.assertEqual(self.d['sale_project'],'MEMBER');self.assertEqual(self.d['auto_enabled'],0)
  detail=self.p.detail(A,'demands',self.d['id']);self.assertEqual(detail['source_values'],record()['values']);self.assertEqual(len(json.loads(detail['match_regions'])),3)
  for city in ['保山','保山市','上海市','北京市']:self.assertTrue(self.preview(self.lead(city=city))['eligible'])
  self.assertEqual(self.preview(self.lead(city='广州市'))['reason'],'CITY_MISMATCH')
 def test_brand_full_series_specific_model_and_nationwide(self):
  self.assertTrue(self.preview(self.lead())['eligible']);self.assertEqual(self.preview(self.lead(brand='奥迪',model='奥迪 A6L'))['reason'],'MODEL_MISMATCH')
  self.data['records']=[record(model='宝马 X5',regions='全国')];self.run_sync();self.assertTrue(self.preview(self.lead(model='X5',city='深圳市'))['eligible']);self.assertEqual(self.preview(self.lead(model='宝马 3系'))['reason'],'MODEL_MISMATCH')
 def test_end_remove_and_reopen_keep_same_id_and_cancel_unsent(self):
  lid=self.lead();delivery=self.send(lid)['items'][0]['delivery_id'];id=self.d['id'];self.data['records'][0]['values']['需求执行状态（渠道维护）']='结束';self.run_sync();self.assertEqual(self.d['status'],'CLOSED');self.assertEqual(self.d['id'],id);self.assertEqual(self.p.detail(A,'deliveries',delivery)['send_status'],'CANCELED')
  self.data['records'][0]['values']['需求执行状态（渠道维护）']='';self.run_sync();self.assertEqual(self.d['status'],'ACTIVE');self.assertEqual(self.d['id'],id)
  self.data['records']=[record(key='OTHER')];self.run_sync();c=self.p.connect();old=dict(c.execute('SELECT * FROM demands WHERE id=?',(id,)).fetchone());c.close();self.assertEqual(old['status'],'CLOSED');self.assertEqual(old['sync_state'],'REMOVED')
 def test_daily_quota_atomic_and_resets_without_resetting_sale_history(self):
  leads=[self.lead(),self.lead()]
  with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda pair:self.send(*pair),zip(leads,['a','b'])))
  self.assertEqual(sum(r['created'] for r in results),1);winner=next(r['items'][0] for r in results if r['created']);self.p.worker(A);self.assertEqual(self.preview(self.lead())['reason'],'DAILY_QUOTA_FULL')
  self.p.mock_callback(A,winner['delivery_id'],'STORED');self.p.mock_callback(A,winner['delivery_id'],'VALID');detail=self.p.detail(A,'demands',self.d['id']);self.assertEqual(detail['status'],'ACTIVE');self.assertEqual(detail['effective_valid'],1);self.assertTrue(detail['target_achieved'])
  tomorrow=__import__('datetime').date.fromisoformat(day())+__import__('datetime').timedelta(days=1)
  with patch('domain.day',return_value=tomorrow.isoformat()):
   self.assertEqual(self.p.detail(A,'demands',self.d['id'])['remaining_today'],1);self.assertTrue(self.preview(self.lead())['eligible']);self.assertEqual(self.preview(winner['lead_id'])['reason'],'FINAL_MEDIA_DUPLICATE')

 def test_ended_with_undetermined_date_clears_current_quantity_and_preserves_source(self):
  self.data['records']=[record(qty='4')]
  self.run_sync()
  before=self.p.reports(A)['overview']['order_quantities']['daily']
  self.data['records'][0]['values']['需求执行状态（渠道维护）']='结束'
  self.run_sync()
  detail=self.p.detail(A,'demands',self.d['id'])
  self.assertEqual((detail['status'],detail['sync_state']),('CLOSED','ENDED'))
  self.assertEqual((detail['target_total'],detail['daily_limit'],detail['remaining_today']),(0,0,0))
  self.assertEqual(detail['source_values']['下单量'],'4')
  self.assertFalse(self.preview(self.lead())['eligible'])
  listing=self.sync.all_projects(A)
  self.assertEqual(listing['items'],[])
  self.assertEqual(listing['total'],0)
  self.assertEqual(listing['summary']['order_quantities'],{'daily':0,'total':0,'invalid_rows':0})
  self.assertEqual(self.sync.projects(A,self.source['id'])['items'][0]['order_quantity'],'0')
  self.assertEqual(self.p.reports(A)['overview']['order_quantities']['daily'],before-4)
  self.data['records'][0]['values']['需求执行状态（渠道维护）']=''
  self.run_sync()
  self.assertEqual(self.p.detail(A,'demands',self.d['id'])['target_total'],4)
  self.assertTrue(self.preview(self.lead())['eligible'])

 def test_zero_quantity_is_allowed_only_for_explicitly_ended_qq_rows(self):
  for state in ('结束',' 已结束 ','停止','关闭'):
   with self.subTest(state=state):
    self.data['records']=[record(qty='0',state=state)]
    result=self.run_sync()
    self.assertEqual(result['demands']['errors'],0)
    self.assertEqual(self.d['status'],'CLOSED')
    self.assertEqual(self.p.detail(A,'demands',self.d['id'])['target_total'],0)
  self.data['records']=[record(qty='0')]
  result=self.run_sync()
  self.assertEqual(result['demands']['errors'],1)
  self.assertNotEqual(self.d['status'],'ACTIVE')

 def test_ending_keeps_sent_delivery_snapshot_and_ledger(self):
  delivery_id=self.send(self.lead())['items'][0]['delivery_id']
  self.p.worker(A)
  before=self.p.detail(A,'deliveries',delivery_id)
  self.data['records'][0]['values']['需求执行状态（渠道维护）']='结束'
  self.run_sync()
  after=self.p.detail(A,'deliveries',delivery_id)
  self.assertEqual(after['send_status'],before['send_status'])
  self.assertEqual(after['demand_snapshot_public'],before['demand_snapshot_public'])
  self.assertEqual(self.p.detail(A,'demands',self.d['id'])['effective_reserved'],1)
  self.p.mock_callback(A,delivery_id,'VALID')
  detail=self.p.detail(A,'demands',self.d['id'])
  self.assertEqual(detail['effective_valid'],1)
  self.assertEqual(detail['remaining_today'],0)
 def test_invalid_releases_daily_demand_quota_but_not_sale_slot(self):
  lid=self.lead();r=self.send(lid);self.p.worker(A);self.p.mock_callback(A,r['items'][0]['delivery_id'],'INVALID');self.assertTrue(self.preview(self.lead())['eligible']);self.assertEqual(self.preview(lid)['reason'],'FINAL_MEDIA_DUPLICATE')
 def test_auto_distribution_uses_same_demand_and_rule_checks(self):
  config=next(p for p in self.p.catalogs(A)['partners'] if p['id']=='MEDIA_A')['cooperation_projects'][0];self.p.project_mode_update(A,'MEDIA_A','MEMBER',{'matching_mode':'AUTO','version':config['version']})
  lid=self.lead();settings=self.p.catalogs(A)['settings'];self.p.settings_update(A,{'version':settings['version'],'reason':'自动同步需求测试','auto_dispatch_enabled':1});r=self.p.auto_run(A);self.assertEqual(r['created'],1)
  c=self.p.connect();d=dict(c.execute('SELECT * FROM deliveries WHERE lead_id=?',(lid,)).fetchone());c.close();self.assertEqual(d['dispatch_mode'],'AUTO');self.assertEqual(d['demand_id'],self.d['id']);self.assertEqual(d['send_status'],'ACKED');self.assertEqual(d['sale_project'],'MEMBER')
 def test_local_simulation_never_uses_yiche_network(self):
  lid=self.lead();r=self.send(lid)
  with patch('yiche_transport.send',side_effect=AssertionError('禁止真实媒体请求')):self.assertEqual(self.p.worker(A)['count'],1)
  detail=self.p.detail(A,'deliveries',r['items'][0]['delivery_id']);self.assertEqual(detail['send_status'],'ACKED');self.assertTrue(detail['outbound_payload']['simulation_only']);self.assertEqual(detail['demand_snapshot_public']['demand_no'],'BMW-DAILY')
  self.p.yiche_live=object();self.assertEqual(self.preview(self.lead())['reason'],'YICHE_CONFIG_REQUIRED')
 def test_project_fixed_and_sync_owned_fields_protected(self):
  lid=self.lead();self.assertEqual(self.p.preview(A,[lid],[self.d['id']],{self.d['id']:'KA'})['items'][0]['reason'],'PROJECT_MISMATCH')
  with self.assertRaises(Fault):self.p.edit_demand(A,self.d['id'],{'version':self.d['version'],'target_total':99})
  self.p.demand_action(A,self.d['id'],'pause',self.d['version']);self.run_sync();self.assertEqual(self.d['status'],'PAUSED');self.p.demand_action(A,self.d['id'],'resume',self.d['version']);self.run_sync();self.assertEqual(self.d['status'],'ACTIVE')
 def test_source_route_relay_and_daily_quantity_updates(self):
  s=self.sync.sources(A)[0];self.sync.save(A,{'version':s['version'],'partner_id':'AGENCY_A','route_id':'AGENCY_TO_A'},s['id']);self.run_sync();self.assertEqual(self.d['receiver_id'],'AGENCY_A');self.assertEqual(self.d['media_id'],'MEDIA_A')
  self.data['records'][0]['values']['下单量']='2';self.run_sync();self.assertEqual(self.d['target_total'],2);self.assertEqual(self.d['daily_limit'],2)
 def test_simulation_preserves_media_requirement_identifier(self):
  lid=self.lead();r=self.send(lid);self.p.worker(A);detail=self.p.detail(A,'deliveries',r['items'][0]['delivery_id']);self.assertEqual(detail['outbound_payload']['source_project']['demand_no'],'BMW-DAILY');self.assertEqual(detail['outbound_payload']['source_project']['regions'],['保山市','上海市','北京市'])

 def test_total_quantity_does_not_reset_after_midnight(self):
  source=self.sync.sources(A)[0];self.sync.save(A,{'version':source['version'],'quantity_basis':'TOTAL'},source['id']);self.run_sync()
  r=self.send(self.lead());self.p.worker(A);self.p.mock_callback(A,r['items'][0]['delivery_id'],'VALID')
  self.assertEqual(self.p.detail(A,'demands',self.d['id'])['status'],'COMPLETED')
  tomorrow=__import__('datetime').date.fromisoformat(day())+__import__('datetime').timedelta(days=1)
  with patch('domain.day',return_value=tomorrow.isoformat()):self.assertFalse(self.preview(self.lead())['eligible'])
