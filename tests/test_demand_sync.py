import base64,copy,json,struct,unittest,zlib
from tempfile import TemporaryDirectory
from domain import Platform,Fault,later
from demand_sync import DemandSync
from qq_sheet import QQSheetError,read_qq_sheet,sheet_url,normalize_grid,get_bytes
A={'id':'u_admin','org_id':'demo','role':'admin'}
URL='https://docs.qq.com/sheet/DVFNzZWxTWkNZV3RQ?tab=BB08J2'
HEAD=['需求编号','品牌','下单日期','下单量','结束时间']
ROWS=[HEAD,['000123','宝马',46241.0,1,'待定'],[103903.0,'长安引力','2026-11-11','1','待定']]
def vi(n):
 b=bytearray()
 while n>=128:b.append((n&127)|128);n>>=7
 b.append(n);return bytes(b)
def f(n,v):return vi(n*8)+vi(v) if isinstance(v,int) else vi(n*8+2)+vi(len(v))+v
def payload(rows,can_read=1,total=None,end=None,offset=0):
 strings=[];numbers=[];cells=[]
 for r,row in enumerate(rows):
  for c,v in enumerate(row):
   if isinstance(v,(int,float)):
    if isinstance(v,int) and 0<=v<=128:ref=v
    else:numbers.append(float(v));ref=128+len(numbers)
    d=f(1,2)+f(2,f(1,ref))
   elif v!='':strings.append(str(v));d=f(1,4)+f(2,f(1,len(strings)-1))
   else:d=b''
   cells.append(f(6,f(1,r+offset)+f(2,c)+f(3,d)))
 pool=b''.join(f(1,f(1,s.encode())) for s in strings)+b''.join(f(3,vi(9)+struct.pack('<d',n)) for n in numbers)
 sheet=f(3,f(1,b'BB08J2'))+f(5,pool)+b''.join(cells);binary=f(1,f(5,f(1,18)+f(19,sheet)))
 return {'clientVars':{'initialTitle':'测试需求表','privilegeAttribute':{'can_read':can_read},'collab_client_vars':{'padSubId':'BB08J2','maxRow':total or len(rows),'initialAttributedText':{'text':[{'block_datas':[{'end_row_index':end if end is not None else len(rows)-1,'related_sheet':base64.b64encode(zlib.compress(binary)).decode()}]}]}}}}
def fetched(rows):return normalize_grid({(r,c):v for r,row in enumerate(rows) for c,v in enumerate(row)},'测试需求表',URL,'BB08J2')
class QQSheetTests(unittest.TestCase):
 def getter(self,p):
  return lambda u,r:b'<script src="//docs.qq.com/dop-api/opendoc?id=DVFNzZWxTWkNZV3RQ&amp;tab=BB08J2&amp;block_start_row=0&amp;block_end_row=255"></script>' if '/sheet/' in u else ('clientVarsCallback('+json.dumps(p)+');').encode()
 def test_number_date_and_leading_zero(self):
  r=read_qq_sheet(URL,self.getter(payload(ROWS)));self.assertEqual(r['count'],2);self.assertEqual([v['key'] for v in r['records']],['000123','103903']);self.assertEqual(r['records'][0]['values']['下单日期'],'2026-08-07');self.assertEqual(r['records'][0]['values']['下单量'],'1');self.assertEqual(r['records'][0]['raw_values']['下单日期'],46241.0)
 def test_url_allowlist(self):
  for u in ['http://docs.qq.com/sheet/AAAAAA?tab=A','https://127.0.0.1/sheet/AAAAAA?tab=A','https://docs.qq.com.evil/sheet/AAAAAA?tab=A','https://docs.qq.com/sheet/AAAAAA']:
   with self.subTest(url=u),self.assertRaises(QQSheetError):sheet_url(u)
  with self.assertRaises(QQSheetError):get_bytes('https://127.0.0.1/private',URL)
 def test_permission_and_wrong_tab(self):
  with self.assertRaises(QQSheetError):read_qq_sheet(URL,self.getter(payload(ROWS,can_read=0)))
  p=payload(ROWS);p['clientVars']['collab_client_vars']['padSubId']='wrong'
  with self.assertRaises(QQSheetError):read_qq_sheet(URL,self.getter(p))
 def test_default_sheet_different_from_share_tab(self):
  requested=[]
  def getter(url,ref):
   requested.append(url)
   if '/sheet/' in url:return b'<script src="//docs.qq.com/dop-api/opendoc?id=DVFNzZWxTWkNZV3RQ&amp;tab=xbwe8r&amp;callback=clientVarsCallback"></script>'
   self.assertEqual(__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).query)['tab'],['BB08J2'])
   return ('clientVarsCallback('+json.dumps(payload(ROWS))+');').encode()
  result=read_qq_sheet(URL,getter);self.assertEqual(result['count'],2);self.assertEqual(result['tab'],'BB08J2')
 def test_wrong_doc_or_external_bootstrap_rejected(self):
  for api in ('https://docs.qq.com/dop-api/opendoc?id=DWRONGDOC&tab=BB08J2','https://other.example/dop-api/opendoc?id=DVFNzZWxTWkNZV3RQ&tab=BB08J2'):
   calls=[]
   def getter(url,ref):calls.append(url);return ('<script src="'+api+'"></script>').encode()
   with self.subTest(api=api),self.assertRaises(QQSheetError):read_qq_sheet(URL,getter)
   self.assertEqual(len(calls),1)
 def test_unique_visible_sheet_resolves_obsolete_tab(self):
  rows=[['需求编号','品牌','车型','城市&地区','下单量'],['000123','宝马','全系','上海市',1]];p=payload(rows);p['clientVars']['collab_client_vars']['header']=[{'type':'ms','d':[{'type':'grid','id':'BB08J2','name':'需求表','hidden':False}]}]
  result=read_qq_sheet(URL.replace('BB08J2','OLDTAB'),self.getter(p));self.assertEqual(result['tab'],'BB08J2');self.assertIn('BB08J2',result['url']);self.assertEqual(result['requested_tab'],'OLDTAB');self.assertTrue(result['notice'])
 def test_multiple_sheets_or_wrong_columns_never_auto_switch(self):
  for sheets in ([{'type':'grid','id':'BB08J2'},{'type':'grid','id':'SECOND'}],[{'type':'grid','id':'BB08J2'}]):
   p=payload(ROWS);p['clientVars']['collab_client_vars']['header']=[{'type':'ms','d':sheets}]
   with self.subTest(sheets=sheets),self.assertRaises(QQSheetError):read_qq_sheet(URL.replace('BB08J2','OLDTAB'),self.getter(p))
 def test_multiple_pages_are_combined(self):
  initial=payload(ROWS[:2],total=300,end=255);second=payload([ROWS[2]],total=300,end=299,offset=256)
  def getter(url,ref):
   if '/sheet/' in url:return self.getter(initial)(url,ref)
   selected=second if 'block_start_row=256' in url else initial
   return ('clientVarsCallback('+json.dumps(selected)+');').encode()
  result=read_qq_sheet(URL,getter);self.assertEqual(result['count'],2);self.assertEqual(result['records'][-1]['row_number'],257)
 def test_truncated_pagination(self):
  with self.assertRaises(QQSheetError):read_qq_sheet(URL,self.getter(payload(ROWS,total=500,end=255)))
 def test_headers_duplicate_and_missing_keys(self):
  for rows in [[['品牌'],['宝马']],ROWS+[ROWS[1]],ROWS+[['','宝马','','','']],[HEAD]]:
   with self.subTest(rows=rows),self.assertRaises(QQSheetError):fetched(rows)
 def test_corrupt_payload(self):
  p=payload(ROWS);p['clientVars']['collab_client_vars']['initialAttributedText']['text'][0]['block_datas'][0]['related_sheet']='invalid'
  with self.assertRaises(QQSheetError):read_qq_sheet(URL,self.getter(p))
class DemandSyncTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.p=Platform(self.temp.name);self.p.seed();self.data=fetched(ROWS);self.calls=0
  def fetch(url):self.calls+=1;return copy.deepcopy(self.data)
  self.s=DemandSync(self.p,fetch);self.source=self.s.save(A,{'name':'会员需求','url':URL,'interval_minutes':30,'partner_id':'MEDIA_A','media_id':'MEDIA_A','sale_project':'MEMBER'})
 def tearDown(self):self.temp.cleanup()
 def test_upsert_change_remove_restore(self):
  self.assertEqual(self.s.run(A,self.source['id'])['added'],2);self.assertEqual(self.s.run(A,self.source['id'])['unchanged'],2)
  rows=copy.deepcopy(ROWS);rows[1][3]=3;self.data=fetched(rows);self.assertEqual(self.s.run(A,self.source['id'])['updated'],1)
  self.data=fetched(rows[:2]);self.assertEqual(self.s.run(A,self.source['id'])['removed'],1);self.assertEqual(self.s.projects(A,self.source['id'],present='0')['total'],1)
  self.data=fetched(ROWS);self.assertEqual(self.s.run(A,self.source['id'])['updated'],2)
 def test_failure_preserves_data(self):
  self.s.run(A,self.source['id'])
  def fail(url):raise QQSheetError('分享权限失效')
  self.s.fetcher=fail;self.assertEqual(self.s.run(A,self.source['id'])['status'],'FAILED');self.assertEqual(self.s.projects(A,self.source['id'])['total'],2);self.assertEqual(self.s.sources(A)[0]['last_error'],'分享权限失效')
 def test_resolved_tab_updates_only_same_document_source(self):
  self.data.update(tab='NEW_TAB',requested_tab='BB08J2',notice='唯一工作表已定位',url=URL.replace('BB08J2','NEW_TAB'))
  result=self.s.run(A,self.source['id']);self.assertEqual(result['status'],'SUCCESS');self.assertTrue(result['notice']);source=self.s.sources(A)[0];self.assertEqual(source['tab_id'],'NEW_TAB');self.assertIn('NEW_TAB',source['url']);self.assertEqual(source['version'],2)
 def test_resolved_tab_collision_does_not_overwrite_existing_source(self):
  self.s.save(A,{'name':'已存在新表','url':URL.replace('BB08J2','NEW_TAB'),'partner_id':'MEDIA_A','media_id':'MEDIA_A','sale_project':'MEMBER','enabled':0})
  self.data.update(tab='NEW_TAB',requested_tab='BB08J2',notice='唯一工作表已定位',url=URL.replace('BB08J2','NEW_TAB'))
  result=self.s.run(A,self.source['id']);self.assertEqual(result['status'],'FAILED');self.assertEqual(next(s for s in self.s.sources(A) if s['id']==self.source['id'])['tab_id'],'BB08J2')
 def test_duplicate_fetch_rolls_back(self):
  self.s.run(A,self.source['id']);self.data['records'].append(copy.deepcopy(self.data['records'][0]));self.assertEqual(self.s.run(A,self.source['id'])['status'],'FAILED');self.assertEqual(self.s.projects(A,self.source['id'])['total'],2)
 def test_claim_and_recovery(self):
  with self.p.tx() as c:c.execute('UPDATE demand_sync_sources SET running_until=?,run_id=? WHERE id=?',(later(hours=1),'old',self.source['id']))
  with self.assertRaises(Fault) as cm:self.s.run(A,self.source['id'])
  self.assertEqual(cm.exception.code,'SYNC_RUNNING')
  with self.p.tx() as c:c.execute('UPDATE demand_sync_sources SET running_until=? WHERE id=?',(later(hours=-1),self.source['id']))
  self.assertEqual(self.s.run(A,self.source['id'])['status'],'SUCCESS')
 def test_schedule_frequency_pause_and_manual(self):
  self.s.tick();self.assertEqual(self.calls,1);self.s.tick();self.assertEqual(self.calls,1)
  source=self.s.sources(A)[0];self.s.save(A,{'version':source['version'],'interval_minutes':10,'enabled':0},source['id'])
  with self.p.tx() as c:c.execute('UPDATE demand_sync_sources SET next_run_at=?',(later(hours=-1),))
  self.s.tick();self.assertEqual(self.calls,1);self.s.run(A,self.source['id']);self.assertEqual(self.calls,2);self.assertEqual(self.s.sources(A)[0]['interval_minutes'],10)
 def test_permissions_and_tenant_isolation(self):
  with self.assertRaises(Fault):self.s.save({'id':'u_operator','role':'operator','org_id':'demo'},{'name':'x','url':URL})
  other={'id':'u_other','role':'admin','org_id':'other'};self.assertEqual(self.s.sources(other),[])
  with self.assertRaises(Fault):self.s.projects(other,self.source['id'])
  self.s.run(A,self.source['id']);row=self.s.projects(A,self.source['id'])['items'][0]
  with self.assertRaises(Fault):self.s.detail(other,row['id'])
 def test_config_validation(self):
  for data in [{'name':'x','url':URL},{'name':'','url':URL},{'name':'x','url':URL,'interval_minutes':0}]:
   with self.assertRaises(Fault):self.s.save(A,data)
  with self.assertRaises(Fault):self.s.save(A,{'version':0},self.source['id'])
  with self.assertRaises(Fault):self.s.save(A,{'version':1,'media_id':'AGENCY_A'},self.source['id'])
 def test_fields_search_and_no_automatic_activation(self):
  n=self.p.list(A,'demands')['total'];self.s.run(A,self.source['id']);self.assertEqual(self.p.list(A,'demands')['total'],n)
  r=self.s.projects(A,self.source['id'],q='宝马');self.assertEqual(r['total'],1);self.assertEqual(r['headers'],HEAD);d=self.s.detail(A,r['items'][0]['id']);self.assertEqual(d['values']['结束时间'],'待定');self.assertEqual(d['raw_values']['下单日期'],46241.0)

 def test_new_source_requires_subject_media_and_project(self):
  base={'name':'新来源','url':'https://docs.qq.com/sheet/DNEWTEST99?tab=BB08J2','partner_id':'MEDIA_A','media_id':'MEDIA_A','sale_project':'MEMBER'}
  for field in ('partner_id','media_id','sale_project'):
   data=base.copy();data.pop(field)
   with self.subTest(field=field),self.assertRaises(Fault):self.s.save(A,data)
  with self.assertRaises(Fault):self.s.save(A,dict(base,partner_id='SOURCE_A'))
  with self.assertRaises(Fault):self.s.save(A,dict(base,route_id='AGENCY_TO_A'))
  source=self.s.save(A,base);self.assertEqual(source['partner_id'],'MEDIA_A')
