import io,json,sys,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_acceptance import Acceptance,A,C,O,B,canonical
class Security(Acceptance):
 def login(self,role='admin'):
  r=urlopen(Request(self.base+'/api/v1/login',data=canonical({'username':role,'password':'Demo2026!'}).encode(),headers={'Content-Type':'application/json'}));body=json.loads(r.read());return {'Cookie':r.headers['Set-Cookie'].split(';')[0],'X-CSRF-Token':body['data']['csrf']}
 def request(self,path,method='GET',data=None,headers=None):
  try:
   r=urlopen(Request(self.base+path,method=method,data=canonical(data).encode() if data is not None else None,headers={'Content-Type':'application/json',**(headers or {})}));return r.status,r.read()
  except HTTPError as e:return e.code,e.read()
 def test_SEC01(self):
  h=self.login();self.assertEqual(self.request('/api/v1/leads/'+self.lead()['id']+'/review',headers=h)[0],405);self.note('读取请求不能触发授权审核')
 def test_SEC02(self):
  h=self.login();del h['X-CSRF-Token'];self.assertEqual(self.request('/api/v1/worker/run','POST',{},h)[0],403);self.note('缺少CSRF禁止写入')
 def test_SEC03(self):
  h=self.login('operator');self.assertEqual(self.request('/api/v1/settings/distribution','PATCH',{'max_media_count':9,'version':1},h)[0],403);self.note('运营无法越权修改系统上限')
 def test_SEC04(self):
  ex=self.p.export_request(O,'核对渠道');self.p.export_approve(C,ex['id']);text=self.p.export_download(O,ex['id']);self.assertIn('138****0001',text.replace('+86',''));self.assertNotIn('13800000001',text);self.fault('DOWNLOAD_EXPIRED',self.p.export_download,O,ex['id']);self.note('独立审批后仅脱敏单次下载')
 def test_SEC05(self):
  f={'id':'u_finance','org_id':'demo','role':'finance'};self.fault('SELF_APPROVAL',self.p.export_approve,f,self.p.export_request(f,'本人申请')['id']);d=self.p.create_demand(A,{'demand_no':'SELF','route_id':'DIRECT_A','target_total':1,'daily_limit':1,'yiche':{'platFormId':900001,'plat':'Web','cityId':900002,'serialId':900003,'manufacturerId':900004}});self.p.demand_action(A,d['id'],'submit',1);self.fault('SELF_APPROVAL',self.p.demand_action,A,d['id'],'approve',2);self.note('导出和需求均禁止本人自审')
 def test_SEC06(self):
  id=self.sent();ev=self.ev(id);ev['customer']={'phone':'13800000001'};self.fault('SCHEMA_INVALID',self.p.receive_callback,A,'MEDIA_A',ev);self.note('回调拒绝夹带客户个人信息字段')
 def test_SEC07(self):
  raw=(self.p.root/'platform.sqlite').read_bytes();self.assertNotIn(b'13800000001',raw);self.assertNotIn(b'Demo2026!',raw);self.note('数据库无测试手机号及口令明文')
 def test_SEC08(self):
  row='source_record_id,phone,series_id,city_code,collected_at,recipients,evidence_ref\nSCRUB,13800000001,BMW_X5,310100,,MEDIA_A,TEST://scrub';pv=self.p.import_preview(A,'scrub.csv',row.encode(),'PURCHASE');self.p.privacy(C,{'lead_id':self.lead()['id'],'type':'DELETE'});im=next(x for x in self.rows('imports') if x['id']==pv['id']);self.assertNotIn('13800000001',self.p.vault.decrypt(im['rows_cipher']));self.assertEqual(json.loads(im['preview'])['errors'],1);self.note('删除同步清理待确认导入，防止旧预览再次入库')
 def test_SEC09(self):
  h=self.login();code,body=self.request('/api/v1/partners','POST',{'name':'新增模拟采购方','role':'SOURCE'},h);self.assertEqual(code,201);id=json.loads(body)['data']['id'];self.assertEqual(self.request('/api/v1/channels','POST',{'name':'新增测试渠道','type':'PURCHASE','source_partner_id':id},h)[0],201);self.note('合作主体和来源渠道可通过受保护接口新增')
 def test_SEC10(self):
  d=self.dm();lid=self.lead()['id'];key='ONE';self.p.dispatch(A,[(lid,d['id'])],key);self.fault('IDEMPOTENCY_CONFLICT',self.p.dispatch,A,[(self.lead(2)['id'],d['id'])],key);self.note('同业务幂等键内容变化拒绝')
if __name__=='__main__':
 suite=unittest.TestSuite(Security(n) for n in Security.__dict__ if n.startswith('test_SEC'));r=unittest.TextTestRunner(verbosity=2).run(suite);out={'total':r.testsRun,'passed':r.testsRun-len(r.errors)-len(r.failures),'errors':[str(t)+': '+str(e) for t,e in r.errors+r.failures]};(Path(__file__).resolve().parents[1]/'docs/security-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));sys.exit(0 if r.wasSuccessful() else 1)
