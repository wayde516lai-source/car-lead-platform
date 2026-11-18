import json,tempfile,threading,unittest,urllib.request,urllib.error,http.cookiejar
import server
from domain import Platform
class AgencyMediaTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.p=Platform(self.temp.name);self.p.seed();server.P=self.p
  self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=self.http.serve_forever,daemon=True).start();self.base='http://127.0.0.1:'+str(self.http.server_address[1]);self.o=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));self.csrf=''
  status,r=self.call('login',{'username':'admin','password':'Demo2026!'});self.csrf=r['data']['csrf']
 def tearDown(self):self.http.shutdown();self.http.server_close();self.temp.cleanup()
 def call(self,path,data=None):
  request=urllib.request.Request(self.base+'/api/v1/'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-CSRF-Token':self.csrf})
  try:
   with self.o.open(request) as r:return r.status,json.load(r)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 def create(self,**kw):return self.call('partners',dict({'name':'测试乙方','role':'AGENCY','cooperation_project':'MEMBER'},**kw))
 def partner(self,id):return next(p for p in self.call('catalogs')[1]['data']['partners'] if p['id']==id)
 def test_agency_requires_final_media_and_rolls_back(self):
  before=len(self.call('catalogs')[1]['data']['partners']);status,r=self.create();self.assertEqual(status,422);self.assertEqual(r['code'],'FINAL_MEDIA_REQUIRED');self.assertEqual(len(self.call('catalogs')[1]['data']['partners']),before)
 def test_only_selected_project_opened_and_secondary_added_explicitly(self):
  status,r=self.create(role='MEDIA',cooperation_project='MEMBER');id=r['data']['id'];self.assertEqual([p['project'] for p in self.partner(id)['cooperation_projects']],['MEMBER'])
  status,r=self.call('partner-projects',{'partner_id':id,'project':'KA'});self.assertEqual(status,201);self.assertEqual({p['project'] for p in self.partner(id)['cooperation_projects']},{'MEMBER','KA'})
  status,r=self.call('partner-projects',{'partner_id':id,'project':'KA'});self.assertEqual(status,409)
 def test_media_does_not_require_field(self):
  status,r=self.create(role='MEDIA');self.assertEqual(status,201);p=self.partner(r['data']['id']);self.assertEqual(p['final_media_ids'],[p['id']])
 def test_existing_media_alias_reused_and_route_stored(self):
  status,r=self.create(final_media='易车');self.assertEqual(status,201);id=r['data']['id'];self.assertEqual(self.partner(id)['final_media_ids'],['MEDIA_A']);routes=self.call('catalogs')[1]['data']['routes'];self.assertTrue(any(r['buyer_id']==id and r['receiver_id']==id and r['media_id']=='MEDIA_A' for r in routes))
 def test_custom_media_saved_and_reused(self):
  status,r=self.create(final_media='测试区域汽车网');self.assertEqual(status,201);id=self.partner(r['data']['id'])['final_media_ids'][0];media=self.partner(id);self.assertEqual(media['name'],'测试区域汽车网');self.assertEqual(media['role'],'MEDIA');self.assertEqual([p['project'] for p in media['cooperation_projects']],['MEMBER'])
  status,r=self.create(name='另一乙方',final_media='测试区域汽车网');self.assertEqual(status,201);self.assertEqual(self.partner(r['data']['id'])['final_media_ids'],[id])
 def test_owned_media_is_separate_for_each_agency(self):
  ids=[]
  for name in ('甲乙方','乙乙方'):
   status,r=self.create(name=name,final_media='自有媒体');self.assertEqual(status,201);id=self.partner(r['data']['id'])['final_media_ids'][0];ids.append(id);self.assertEqual(self.partner(id)['name'],name+' · 自有媒体')
  self.assertNotEqual(*ids)
 def test_id_role_and_tenant_validation(self):
  status,r=self.create(final_media_id='SOURCE_A');self.assertEqual(status,422)
  with self.p.tx() as c:c.execute("INSERT INTO partners(id,org_id,name,role,contract_end) VALUES('FOREIGN','other','他方媒体','MEDIA','9999-01-01')")
  status,r=self.create(final_media_id='FOREIGN');self.assertEqual(status,404)
  status,r=self.create(final_media_id='MEDIA_B');self.assertEqual(status,201);self.assertEqual(self.partner(r['data']['id'])['final_media_ids'],['MEDIA_B'])
