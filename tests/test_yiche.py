import io,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import yiche
from domain import Platform,Fault,uid
from openpyxl import Workbook

A={'org_id':'demo','id':'u_admin','role':'admin'}
CONFIG={'platFormId':900001,'plat':'Web','cityId':900002,'serialId':900003,'manufacturerId':900004}

class Yiche(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.p=Platform(self.tmp.name);self.p.seed()
  self.l=next(l for l in self.p.list(A,'leads',100)['items'] if l['lead_no']=='L000001')
  self.d=next(d for d in self.p.list(A,'demands',100)['items'] if d['demand_no']=='000123')
 def test_request_shape_and_defaults(self):
  req=yiche.build_request(CONFIG|{'brandId':9},{},'+8613800000001',' ','dl_xyz')['param']
  self.assertEqual(set(req),{'platFormId','plat','cityId','serialId','manufacturerId','clueTypeId','mobile','name','requestIp','sourceUrl'})
  self.assertEqual(req['mobile'],yiche.encrypt('13800000001',yiche.MOCK_KEY))
  self.assertEqual(req['name'],yiche.encrypt('询价客户',yiche.MOCK_KEY))
  self.assertEqual(req['requestIp'],yiche.encrypt('127.0.0.1',yiche.MOCK_KEY))
  self.assertEqual(req['sourceUrl'],yiche.encrypt(yiche.MOCK_CALLBACK+'?platFormId=900001&orderId=dl_xyz',yiche.MOCK_KEY))
 def test_type_83_and_optional_fields(self):
  with self.assertRaisesRegex(ValueError,'crId'):yiche.demand_fields(CONFIG|{'clueTypeId':83},True)
  req=yiche.build_request(CONFIG|{'clueTypeId':83,'crId':'000089','csId2':202,'dealerIds':'15, 16'}, {'longitude':'0','latitude':'0','roomId':'001','carId':'887','dvid':'test-device'}, '13800000001','测试','delivery')['param']
  self.assertEqual(req['crId'],'000089');self.assertEqual(req['dealerIds'],'15,16');self.assertEqual(req['carId'],887)
  self.assertEqual(req['roomId'],'001');self.assertNotEqual(req['dvid'],'test-device')
  self.assertNotEqual(req['longitude'],'0')
 def test_invalid_types_and_geo(self):
  for raw in ({'cityId':True},{'carId':'-1'},{'longitude':'1'},{'longitude':'181','latitude':'0'},{'requestIp':'example'}):
   with self.subTest(raw=raw),self.assertRaises(ValueError):yiche.lead_fields(raw)
 def test_missing_required_blocks_submit_and_dispatch(self):
  d=self.p.create_demand(A,{'demand_no':'YICHE-DRAFT','route_id':'DIRECT_A','target_total':1})
  with self.assertRaisesRegex(Fault,'manufacturerId'):self.p.demand_action(A,d['id'],'submit',d['version'])
  with self.p.tx() as c:c.execute("UPDATE demands SET yiche_config='{}' WHERE id=?",(self.d['id'],))
  self.assertEqual(self.p.dispatch(A,[(self.l['id'],self.d['id'])],uid())['items'][0]['reason'],'YICHE_CONFIG_REQUIRED')
 def test_mapping_conflict(self):
  self.p.edit_yiche_lead(A,self.l['id'],{'version':self.l['version'],'yiche':{'cityId':123}})
  self.assertEqual(self.p.dispatch(A,[(self.l['id'],self.d['id'])],uid())['items'][0]['reason'],'YICHE_MAPPING_MISMATCH')
 def test_preview_masks_sensitive_values(self):
  l=self.p.edit_yiche_lead(A,self.l['id'],{'version':self.l['version'],'yiche':{'requestIp':'192.0.2.5','dvid':'demo-sensitive','ptitle':'测试活动'}})
  self.assertNotIn('demo-sensitive',json.dumps(l))
  r=self.p.dispatch(A,[(self.l['id'],self.d['id'])],uid());id=r['items'][0]['delivery_id']
  self.p.worker(A);d=self.p.detail(A,'deliveries',id)
  req=d['outbound_payload']['yiche_request']['param']
  self.assertEqual(req['dvid'],'已加密 · 模拟');self.assertEqual(req['ptitle'],'测试活动')
  self.assertEqual(d['outbound_payload']['orderId'],id);self.assertEqual(d['send_status'],'ACKED')
 def test_xlsx_fields(self):
  book=Workbook();ws=book.active
  ws.append(['来源记录编号','客户姓名','手机号','车系','城市编码','授权接收方','授权证据','易车城市ID','易车厂商ID','设备ID','品牌','是否售卖'])
  ws.append(['YC-XLS','测试','13900000919','BMW_X5','310100','MEDIA_A','TEST://x','900002','900004','fake-device','宝马','否'])
  buf=io.BytesIO();book.save(buf)
  p=self.p.import_preview(A,'yiche.xlsx',buf.getvalue(),'ADS')
  self.assertEqual(p['valid'],1)
  r=self.p.import_commit(A,p['id'],'ALL_VALID')
  l=self.p.detail(A,'leads',r['items'][0]['lead_id'])
  self.assertEqual(l['yiche']['manufacturerId'],900004);self.assertIn('dvid',l['yiche_optional_present'])
 def test_migration_preserves_legacy_records(self):
  # Simulate v1.1 schema while keeping business rows; re-open twice.
  with self.p.tx() as c:
   c.execute('ALTER TABLE leads DROP COLUMN yiche_cipher')
   c.execute('ALTER TABLE demands DROP COLUMN yiche_config')
   c.execute('DELETE FROM schema_version WHERE version=2')
  for _ in range(2):
   p=Platform(self.tmp.name)
   self.assertEqual(p.detail(A,'demands',self.d['id'])['demand_no'],'000123')
   self.assertEqual(p.detail(A,'leads',self.l['id'])['lead_no'],'L000001')
   self.assertTrue(p.integrity(A)['passed'])

if __name__=='__main__':unittest.main(verbosity=2)
