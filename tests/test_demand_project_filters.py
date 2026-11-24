import copy,unittest
from tempfile import TemporaryDirectory
from domain import Platform
from demand_sync import DemandSync
from test_synced_demands import record
A={'id':'u_admin','org_id':'demo','role':'admin'}
class ProjectFilterTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.p=Platform(self.temp.name);self.p.seed();self.data={};self.sync=DemandSync(self.p,lambda url:copy.deepcopy(self.data[url]))
  for key,party,records in [('ONE','MEDIA_A',[record(key='A1',model='宝马 X5',regions='上海市,北京市'),record(key='A2',brand='奥迪',model='奥迪 A6L',regions='全国',state='结束')]),('TWO','MEDIA_B',[record(key='B1',model='宝马 3系',regions='广州市')])]:
   url='https://docs.qq.com/sheet/DTESTLINK'+key+'?tab=BB08J2';self.data[url]={'title':key,'headers':list(records[0]['values']),'records':records};s=self.sync.save(A,{'name':key,'url':url,'partner_id':party,'media_id':party,'sale_project':'MEMBER','enabled':0});self.assertEqual(self.sync.run(A,s['id'])['status'],'SUCCESS')
 def tearDown(self):self.temp.cleanup()
 def query(self,**f):return self.sync.all_projects(A,filters=f)
 def test_default_all_partners(self):
  r=self.query();self.assertEqual(r['total'],3);self.assertEqual({v['partner_id'] for v in r['items']},{'MEDIA_A','MEDIA_B'})
 def test_partner_filter_and_empty_partner(self):
  self.assertEqual(self.query(partner_id='MEDIA_A')['total'],2);self.assertEqual(self.query(partner_id='MEDIA_B')['total'],1);self.assertEqual(self.query(partner_id='MEDIA_C')['total'],0)
 def test_combined_brand_model_city_and_status(self):
  r=self.query(partner_id='MEDIA_A',brand='宝马',model='宝马 X5',city='北京',match_status='ACTIVE');self.assertEqual(r['total'],1);self.assertEqual(r['items'][0]['source_key'],'A1')
 def test_all_regions_and_national_scope(self):
  self.assertEqual(self.query(city='北京市')['total'],2);self.assertEqual(self.query(city='全国')['total'],1);self.assertEqual(self.query(city='广州市',match_status='ACTIVE')['total'],1)
 def test_status_filter_and_context_options(self):
  self.assertEqual(self.query(match_status='CLOSED')['total'],1);r=self.query(partner_id='MEDIA_B');self.assertEqual([o['value'] for o in r['filter_options']['brand']],['宝马']);self.assertEqual([o['value'] for o in r['filter_options']['model']],['宝马 3系'])
 def test_filters_before_paging_and_tenant_isolation(self):
  r=self.sync.all_projects(A,page=2,page_size=20,filters={'partner_id':'MEDIA_A'});self.assertEqual(r['total'],2);self.assertEqual(r['items'],[])
  other={'id':'u_other','role':'admin','org_id':'other'};self.assertEqual(self.sync.all_projects(other)['total'],0)

 def test_multi_models_split_and_typed_search(self):
  url='https://docs.qq.com/sheet/DTESTMULTI?tab=BB08J2';rows=[record(key='M1',brand='比亚迪',model='海豚、海鸥，海豹06',regions='上海市')];self.data[url]={'title':'multi','headers':list(rows[0]['values']),'records':rows};source=self.sync.save(A,{'name':'multi','url':url,'partner_id':'MEDIA_A','media_id':'MEDIA_A','sale_project':'MEMBER','enabled':0});self.sync.run(A,source['id'])
  r=self.query(brand='比亚',model='海豹',city='上海');self.assertEqual(r['total'],1);self.assertEqual(r['items'][0]['values']['车型'],'海豚、海鸥，海豹06');self.assertEqual({v['value'] for v in r['filter_options']['model']},{'海豚','海鸥','海豹06'})

 def test_summary_covers_filtered_rows_across_pages_and_separates_units(self):
  for url,data in self.data.items():
   for row in data['records']:
    row['values']['下单量']={'A1':'2','A2':'3','B1':'7'}[row['key']]
  for source in self.sync.sources(A):
   if source['partner_id']=='MEDIA_B':
    self.sync.save(A,{'version':source['version'],'quantity_basis':'TOTAL'},source['id'])
   self.assertEqual(self.sync.run(A,source['id'])['status'],'SUCCESS')
  summary=self.query()['summary']
  self.assertEqual(summary,{'brand_count':2,'city_count':3,'nationwide_demands':1,
                           'order_quantities':{'daily':2,'total':7,'invalid_rows':0}})
  second=self.sync.all_projects(A,page=2,page_size=20)
  self.assertEqual(second['items'],[]);self.assertEqual(second['summary'],summary)
  selected=self.query(partner_id='MEDIA_B')['summary']
  self.assertEqual(selected['brand_count'],1);self.assertEqual(selected['city_count'],1)
  self.assertEqual(selected['order_quantities'],{'daily':0,'total':7,'invalid_rows':0})
  closed=self.query(match_status='CLOSED')['summary']
  self.assertEqual(closed['city_count'],0);self.assertEqual(closed['nationwide_demands'],1)
  self.assertEqual(closed['order_quantities']['daily'],0)
  other=self.sync.all_projects({'id':'u_other','role':'admin','org_id':'other'})['summary']
  self.assertEqual(other,{'brand_count':0,'city_count':0,'nationwide_demands':0,
                         'order_quantities':{'daily':0,'total':0,'invalid_rows':0}})

 def test_summary_normalizes_city_suffix_and_does_not_multiply_multi_city_quantity(self):
  url=next(url for url in self.data if 'ONE' in url)
  self.data[url]['records'][0]['values']['城市&地区']='上海市、上海，北京市/北京'
  source=next(s for s in self.sync.sources(A) if s['partner_id']=='MEDIA_A')
  self.assertEqual(self.sync.run(A,source['id'])['status'],'SUCCESS')
  summary=self.query(partner_id='MEDIA_A',brand='宝马')['summary']
  self.assertEqual(summary['city_count'],2)
  self.assertEqual(summary['order_quantities']['daily'],1)
