import unittest
from tempfile import TemporaryDirectory
from domain import Platform,Fault,later,now
A={'id':'u_admin','org_id':'demo','role':'admin'}
class MatchingPoolTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.p=Platform(self.temp.name);self.p.seed()
  self.n=0
 def tearDown(self):self.temp.cleanup()
 def create(self,**changes):
  self.n+=1;return self.p.create_lead(A,dict({'name':'筛选测试','phone':f'13900008{self.n:03}','brand_name':'宝马','series_id':'BMW_X5','city':'上海市','is_sold':'否'},**changes))['lead_id']
 def pool(self,**kw):return self.p.matching_leads(A,**kw)
 def rows(self,**kw):return self.pool(**kw)['items']
 def test_unavailable_hidden_and_no_status_column(self):
  good=self.create();bad=[]
  for status in ['BLOCKED','DELETED','QUARANTINED']:
   lid=self.create();bad.append(lid)
   with self.p.tx() as c:c.execute('UPDATE leads SET status=? WHERE id=?',(status,lid))
  expired=self.create();bad.append(expired)
  with self.p.tx() as c:c.execute('UPDATE leads SET expires_at=? WHERE id=?',(later(hours=-1),expired))
  data=self.pool();self.assertIn('BMW',[v['value'] for v in data['filter_options']['brand']]);ids={r['id'] for r in data['items']};self.assertIn(good,ids);self.assertFalse(ids&set(bad))
  self.assertEqual([c['label'] for c in data['columns']],['编号','城市','品牌','车型','已匹配媒体','项目']);self.assertNotIn('status',data['items'][0])
 def test_exhausted_sales_hidden_but_one_sale_visible(self):
  lid=self.create(is_sold='是',sale_media_id='MEDIA_A',sale_project='KA',sold_at=now());self.assertIn(lid,{r['id'] for r in self.rows()})
  d=next(d for d in self.p.list(A,'demands')['items'] if d['route_id']=='DIRECT_B');self.p.dispatch(A,[(lid,d['id'])],'two-sales',projects={d['id']:'MEMBER'})
  self.assertNotIn(lid,{r['id'] for r in self.rows()})
 def test_pending_media_and_project_visible_and_filterable(self):
  lid=self.create();d=next(d for d in self.p.list(A,'demands')['items'] if d['route_id']=='DIRECT_A');self.p.dispatch(A,[(lid,d['id'])],'pending-project',projects={d['id']:'MEMBER'})
  row=next(r for r in self.rows() if r['id']==lid);self.assertEqual(row['sales_count'],1);self.assertEqual(row['project'],'会员')
  self.assertIn(lid,{r['id'] for r in self.rows(filters={'matched_media':'MEDIA_A','project':'MEMBER','sales_count':'1'})})
  self.assertNotIn(lid,{r['id'] for r in self.rows(filters={'project':'KA'})})
 def test_each_field_filters_and_combined_filters(self):
  lid=self.create(is_sold='是',sale_media_id='MEDIA_A',sale_project='KA',sold_at=now());row=next(r for r in self.rows() if r['id']==lid)
  for key,value in [('lead_no',row['lead_no']),('city','310100'),('brand','BMW'),('model','BMW_X5'),('sales_count','1'),('matched_media','MEDIA_A'),('project','KA')]:
   with self.subTest(key=key):self.assertIn(lid,{r['id'] for r in self.rows(filters={key:value})})
  data=self.rows(filters={'lead_no':row['lead_no'],'city':'310100','brand':'BMW','model':'BMW_X5','matched_media':'MEDIA_A','project':'KA','sales_count':'1'});self.assertEqual([r['id'] for r in data],[lid])
  self.assertEqual(self.rows(filters={'lead_no':row['lead_no'],'brand':'AUDI'}),[])
 def test_unmatched_filter_and_selection_survives_paging_filters(self):
  lid=self.create();expired=self.create()
  with self.p.tx() as c:c.execute('UPDATE leads SET expires_at=? WHERE id=?',(later(hours=-1),expired))
  self.assertIn(lid,{r['id'] for r in self.rows(filters={'project':'NONE','sales_count':'0','matched_media':'NONE'})})
  d=self.pool(filters={'lead_no':'does-not-exist'},selected_ids=[lid,expired]);self.assertEqual(d['items'],[]);self.assertEqual(d['eligible_selected_ids'],[lid])
 def test_filtered_before_pagination(self):
  ids=[self.create() for _ in range(22)];self.assertTrue(self.pool()['has_more'])
  row=next(r for r in self.pool(page_size=100)['items'] if r['id']==ids[0]);self.assertEqual(self.rows(filters={'lead_no':row['lead_no']})[0]['id'],ids[0])
  self.assertEqual(self.pool(page_size=100)['total'],len(self.pool(page_size=100)['items']))
 def test_hidden_fields_have_no_filters_or_columns(self):
  self.create();self.p.update_lead_fields(A,'u_operator',['city','project']);op={'id':'u_operator','org_id':'demo','role':'operator'}
  result=self.p.matching_leads(op,filters={'lead_no':'does-not-exist','matched_media':'MEDIA_C'})
  self.assertEqual([c['key'] for c in result['columns']],['city','project']);self.assertNotIn('matched_media',result['filter_options']);self.assertNotIn('brand',result['filter_options']);self.assertGreater(result['total'],0)
  self.assertNotIn('lead_no',result['items'][0]);self.assertNotIn('sales_count',result['items'][0])
 def test_no_eligible_demand_and_global_pause_hide_rows(self):
  self.create()
  with self.p.tx() as c:c.execute("UPDATE demands SET status='PAUSED'")
  empty=self.pool();self.assertEqual(empty['total'],0)
  options={o['value']:o['label'] for o in empty['filter_options']['matched_media']}
  self.assertTrue({'MEDIA_A','MEDIA_B','MEDIA_C'}<=options.keys());self.assertNotIn('AGENCY_A',options);self.assertNotIn('SOURCE_A',options)
  with self.p.tx() as c:
   c.execute('INSERT INTO partners(id,org_id,name,role,enabled,contract_end) VALUES(?,?,?,?,?,?)',('MEDIA_NEW','demo','新增筛选媒体','MEDIA',1,later(days=365)))
   c.execute('INSERT INTO partners(id,org_id,name,role,enabled,contract_end) VALUES(?,?,?,?,?,?)',('MEDIA_FOREIGN','other','其他组织媒体','MEDIA',1,later(days=365)))
  options={o['value']:o['label'] for o in self.pool()['filter_options']['matched_media']}
  self.assertEqual(options['MEDIA_NEW'],'新增筛选媒体');self.assertNotIn('MEDIA_FOREIGN',options)
  with self.p.tx() as c:c.execute("UPDATE demands SET status='ACTIVE'");c.execute('UPDATE settings SET dispatch_enabled=0')
  self.assertEqual(self.pool()['total'],0)
 def test_buyer_scope_and_no_buyer(self):
  lid=self.create(is_sold='是',sale_media_id='MEDIA_A',sale_project='MEMBER',sold_at=now())
  self.assertNotIn(lid,self.pool(buyer_ids=['MEDIA_A'])['filtered_ids'])
  self.assertIn(lid,self.pool(buyer_ids=['MEDIA_B'])['filtered_ids'])
  self.assertEqual(self.pool(buyer_ids=[])['total'],self.pool()['total'])
  self.assertEqual(self.pool(buyer_ids=['FOREIGN'])['total'],0)
 def test_buyer_selection_pruned_and_select_all_across_pages(self):
  ids=[self.create() for _ in range(22)]
  data=self.pool(buyer_ids=['MEDIA_A'],selected_ids=ids)
  self.assertTrue(data['has_more']);self.assertTrue(set(ids)<=set(data['filtered_ids']))
  self.assertEqual(len(data['items']),20)
  sold=self.create(is_sold='是',sale_media_id='MEDIA_A',sale_project='MEMBER',sold_at=now())
  self.assertNotIn(sold,self.pool(buyer_ids=['MEDIA_A'],selected_ids=[sold])['eligible_selected_ids'])
