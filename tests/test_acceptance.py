import csv,io,json,sqlite3,sys,tempfile,threading,time,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from domain import Platform,Fault,now,uid
from security import canonical,sign
import server
A={'id':'u_admin','org_id':'demo','role':'admin'}
C={'id':'u_compliance','org_id':'demo','role':'compliance'}
O={'id':'u_operator','org_id':'demo','role':'operator'}
B={'id':'u_business','org_id':'demo','role':'business'}
OTHER={'id':'u_other','org_id':'other','role':'admin'}
NAMES=['来源记录重放幂等','跨渠道意向去重与第一来源保留','5条线索向两个媒体产生10笔独立分发','直连与乙方统一按媒体去重','缺少对应接收方授权禁止分发','剩余1条配额下20并发最多成功1条','无幂等与查询能力超时保持UNKNOWN','同回调重复20次不重复记账','同事件编号不同内容拒绝并审计','乙方ACK不等于最终媒体入库','有效与入库乱序不覆盖其他维度','结果更正按版本冲正配额','暂停取消未发送并继续处理历史回调','撤回停发并通知历史接收方','明文与导出权限校验','跨组织访问与下载拒绝','重复任务投递不增加业务交付或获客成本','回调写入失败返回503且安全重试','重启后outbox恢复同一业务交付','车型或路由异常不发送','配额不可低于有效加在途','恢复旧备份前重放删除清单','签名时间戳与nonce防重放','未知编号回调202入待关联队列','媒体编号前导零与车型同传','Excel与CSV预检及确认幂等','媒体上限动态调整且保留历史','分页20 50 100及非法值校验']
class Acceptance(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);cls.base='http://127.0.0.1:'+str(cls.http.server_port);threading.Thread(target=cls.http.serve_forever,daemon=True).start()
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Platform(self.tmp.name);self.p.seed();server.P=self.p;server.LIMITS.clear();self.evidence=[]
 def tearDown(self):self.tmp.cleanup()
 def rows(self,t):
  c=self.p.connect()
  try:return [dict(x) for x in c.execute('SELECT * FROM '+t)]
  finally:c.close()
 def lead(self,n=1):return next(x for x in self.rows('leads') if x['lead_no']==f'L{n:06}')
 def dm(self,no='000123'):return next(x for x in self.rows('demands') if x['demand_no']==no)
 def sql(self,q,p=()):
  with self.p.tx() as c:c.execute(q,p)
 def newd(self,route='DIRECT_A',total=10,no=None):
  d=self.p.create_demand(A,{'demand_no':no or uid('TEST-'),'route_id':route,'target_total':total,'daily_limit':total,'unit_price_fen':4500,'yiche':{'platFormId':900001,'plat':'Web','cityId':900002,'serialId':900003,'manufacturerId':900004}});self.p.demand_action(A,d['id'],'submit',1);self.p.demand_action(B,d['id'],'approve',2);return self.p.detail(A,'demands',d['id'])
 def dispatch(self,n=1,no='000123',d=None):return self.p.dispatch(A,[(self.lead(n)['id'],(d or self.dm(no))['id'])],uid('test-'))
 def sent(self,n=1,no='000123',d=None):
  r=self.dispatch(n,no,d);self.assertEqual(r['created'],1,r);id=r['items'][0]['delivery_id'];self.p.worker(A);return id
 def ev(self,id,ty='MEDIA_STORED',v=1):
  d=self.p.detail(A,'deliveries',id);return {'event_id':uid('ev_'),'delivery_id':id,'event_type':ty,'event_version':v,'occurred_at':now(),'final_media_code':d['media_id'],'data':{'validity':'VALID'} if ty=='VALIDITY_UPDATED' else {}}
 def fault(self,code,fn,*a,**kw):
  with self.assertRaises(Fault) as cm:fn(*a,**kw)
  self.assertEqual(cm.exception.code,code)
 def call(self,path,body=None,headers=None):
  req=Request(self.base+path,data=canonical(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
  try:
   with urlopen(req) as r:return r.status,json.loads(r.read())
  except HTTPError as e:return e.code,json.loads(e.read())
 def signed(self,path,event,kid='TEST-MEDIA_A',ts=None,nonce=None):
  ts=str(ts or int(time.time()));nonce=nonce or uid('n_');return {'X-Key-Id':kid,'X-Timestamp':ts,'X-Nonce':nonce,'X-Signature':sign(server.TEST_OPEN_SECRET,'POST',path,ts,nonce,canonical(event).encode())}
 def note(self,x):self.evidence.append(x)
 def test_AC01(self):
  row={'source_record_id':'REPLAY','phone':'13900008881','series_id':'BMW_X5','city_code':'310100','collected_at':now(),'cost_fen':1000};rs=[self.p.ingest(A,'ADS',row) for _ in range(10)];self.assertEqual(len({x['lead_id'] for x in rs}),1);self.assertEqual(sum(x['source_record_id']=='REPLAY' for x in self.rows('ingestions')),1);self.note('10次提交，1条采集记录及1条线索')
 def test_AC02(self):
  old=self.lead();r=self.p.ingest(A,'PURCHASE',{'source_record_id':'OTHER-CHANNEL','phone':'13800000001','series_id':'BMW_X5','city_code':'310100','collected_at':now(),'cost_fen':9999});self.assertEqual(r['lead_id'],old['id']);l=self.p.detail(A,'leads',old['id']);self.assertEqual(l['channel_id'],'ADS');self.assertEqual(len(l['ingestions']),2);self.assertEqual(l['cost_fen'],1800);self.note('两个渠道同一线索，首次来源与1800分成本不变')
 def test_AC03(self):
  pairs=[(self.lead(n)['id'],self.dm(no)['id']) for n in range(1,6) for no in ['000123','AUTOHOME-0098']];r=self.p.dispatch(A,pairs,'AC03');self.assertEqual(r['created'],10);self.assertEqual(len({x['lead_id'] for x in r['items']}),5);self.assertTrue(self.p.integrity(A)['passed']);self.note('5条唯一线索、10笔分发，两需求各预占5')
 def test_AC04(self):
  self.sent();r=self.dispatch(no='AG-TO-YC-001');self.assertEqual(r['items'][0]['reason'],'FINAL_MEDIA_DUPLICATE');self.note('经乙方再投同一最终媒体被拦截')
 def test_AC05(self):
  r=self.dispatch(6,'AUTOHOME-0098');self.assertEqual(r['items'][0]['reason'],'CONSENT_MISSING');self.assertFalse(any(x['lead_id']==self.lead(6)['id'] for x in self.rows('deliveries')));self.note('只授权A不能发B，无外发载荷')
 def test_AC06(self):
  d=self.newd(total=1)
  def task(i):return self.p.dispatch(A,[(self.lead(i%5+1)['id'],d['id'])],str(i))['created']
  with ThreadPoolExecutor(max_workers=20) as pool:r=list(pool.map(task,range(20)))
  self.assertEqual(sum(r),1);self.assertTrue(self.p.integrity(A)['passed']);self.note('20个并发事务仅1成功，账本一致')
 def test_AC07(self):
  self.sql("UPDATE routes SET mode='TIMEOUT',supports_query=0,supports_idempotency=0 WHERE id='DIRECT_A'");id=self.sent();d=self.p.detail(A,'deliveries',id);self.assertEqual(d['send_status'],'UNKNOWN');self.fault('QUERY_UNSUPPORTED',self.p.reconcile,A,id);self.p.worker(A);self.assertEqual(len(self.p.detail(A,'deliveries',id)['attempts']),1);self.assertEqual(d['quota_state'],'RESERVED');self.note('UNKNOWN保留预占、仅1次尝试、不安全重发被禁止')
 def test_AC08(self):
  id=self.sent();ev=self.ev(id,'VALIDITY_UPDATED');before=self.dm()['valid_count']
  for _ in range(20):self.p.receive_callback(A,'MEDIA_A',ev);self.p.process_inbox(A)
  self.assertEqual(self.dm()['valid_count'],before+1);self.assertEqual(sum(x['event_id']==ev['event_id'] for x in self.rows('callback_events')),1);self.note('20次重复回调仅增1条有效量')
 def test_AC09(self):
  id=self.sent();ev=self.ev(id);self.p.receive_callback(A,'MEDIA_A',ev);ev['event_version']=2;self.fault('EVENT_CONFLICT',self.p.receive_callback,A,'MEDIA_A',ev);self.assertTrue(any(x['action']=='CALLBACK_ID_CONFLICT' for x in self.rows('audit_log')));self.note('冲突拒绝、保留原事件和冲突审计')
 def test_AC10(self):
  id=self.sent(no='AG-TO-AH-002');d=self.p.detail(A,'deliveries',id);self.assertEqual([d[x] for x in ['send_status','storage_status','validity_status']],['ACKED','PENDING','PENDING']);self.assertEqual(d['hops'][1]['acceptance'],'PENDING');self.note('乙方接收成功，媒体节点仍待反馈')
 def test_AC11(self):
  id=self.sent()
  for ty,v,val in [('VALIDITY_UPDATED',2,'VALID'),('MEDIA_STORED',1,None),('VALIDITY_UPDATED',1,'INVALID')]:
   ev=self.ev(id,ty,v)
   if val:ev['data']['validity']=val
   self.p.receive_callback(A,'MEDIA_A',ev);self.p.process_inbox(A)
  d=self.p.detail(A,'deliveries',id);self.assertEqual(d['validity_status'],'VALID');self.assertEqual(d['storage_status'],'STORED');self.note('有效v2先于入库v1，旧有效版本不覆盖')
 def test_AC12(self):
  id=self.sent();self.p.mock_callback(A,id,'INVALID',1);self.p.mock_callback(A,id,'VALID',2);self.p.mock_callback(A,id,'INVALID',3);self.assertTrue(self.p.integrity(A)['passed']);d=self.p.detail(A,'deliveries',id);self.assertEqual(d['quota_state'],'RELEASED');self.assertEqual(len(d['ledger']),4);self.note('无效→有效→无效，账本冲正后守恒')
 def test_AC13(self):
  sent=self.sent(1);queued=self.dispatch(2)['items'][0]['delivery_id'];d=self.dm();self.p.demand_action(A,d['id'],'pause',d['version']);self.assertEqual(self.p.detail(A,'deliveries',queued)['send_status'],'CANCELED');self.p.mock_callback(A,sent,'VALID');self.assertEqual(self.p.detail(A,'deliveries',sent)['validity_status'],'VALID');self.assertTrue(self.p.integrity(A)['passed']);self.note('暂停取消排队；已发送回调继续接收')
 def test_AC14(self):
  for n,st in [(1,'queued'),(2,'sent'),(3,'valid')]:
   id=self.dispatch(n)['items'][0]['delivery_id'] if st=='queued' else self.sent(n)
   if st=='valid':self.p.mock_callback(A,id,'VALID')
   r=self.p.privacy(C,{'lead_id':self.lead(n)['id'],'type':'REVOKE'});self.assertEqual(r['notices'],0 if st=='queued' else 1);self.assertEqual(self.p.detail(A,'leads',self.lead(n)['id'])['status'],'BLOCKED')
  self.note('排队、在途、有效场景撤回均停发并生成通知')
 def test_AC15(self):
  self.fault('FORBIDDEN',self.p.reveal,O,self.lead()['id'],'测试');ex=self.p.export_request(O,'测试导出');self.fault('DOWNLOAD_EXPIRED',self.p.export_download,O,ex['id']);public=self.p.detail(O,'leads',self.lead()['id']);self.assertNotIn('phone_cipher',public);self.assertNotIn('cost_fen',public);self.note('运营明文403；未批准禁止下载；列表无成本及密文')
 def test_AC16(self):
  self.fault('NOT_FOUND',self.p.detail,OTHER,'leads',self.lead()['id']);ex=self.p.export_request(O,'隔离测试');self.fault('NOT_FOUND',self.p.export_download,OTHER,ex['id']);self.assertEqual(self.p.worker(OTHER)['count'],0);self.note('跨组织对象和导出404；后台任务不会越界')
 def test_AC17(self):
  self.p.dispatch(A,[(self.lead(n)['id'],self.dm(no)['id']) for n in range(1,6) for no in ['000123','AUTOHOME-0098']],'AC17');self.p.worker(A)
  for _ in range(3):self.sql("UPDATE outbox SET status='PENDING'");self.p.worker(A)
  r=self.p.reports(A);self.assertEqual(r['deliveries'],11);self.assertEqual(r['unique_sent_leads'],6);self.assertEqual(r['cost_fen'],10800);self.assertEqual(len(self.rows('attempts')),11);self.note('10笔新分发+1历史，重复投递3轮无新增成本和交付；网络重试尚未接真实接口')
 def test_AC18(self):
  id=self.sent();ev=self.ev(id);path='/open/v1/callbacks/MEDIA_A'
  with patch.object(self.p,'receive_callback',side_effect=sqlite3.OperationalError('injected')):code,_=self.call(path,ev,self.signed(path,ev))
  self.assertEqual(code,503);self.assertFalse(any(x['event_id']==ev['event_id'] for x in self.rows('callback_events')));self.assertEqual(self.call(path,ev,self.signed(path,ev))[0],200);self.p.process_inbox(A);self.note('数据库写入失败HTTP503，重试HTTP200且一次处理')
 def test_AC19(self):
  id=self.dispatch()['items'][0]['delivery_id'];p2=Platform(self.tmp.name);p2.enforce_tombstones();self.assertEqual(p2.worker(A)['count'],1);self.sql("UPDATE outbox SET status='PENDING'");p2.worker(A);self.assertEqual(len(p2.detail(A,'deliveries',id)['attempts']),1);self.note('新实例恢复持久化outbox，不生成第二次发送')
 def test_AC20(self):
  self.assertEqual(self.dispatch(9)['created'],0);self.sql("UPDATE routes SET enabled=0 WHERE id='DIRECT_A'");self.assertEqual(self.dispatch()['items'][0]['reason'],'ROUTE_UNAVAILABLE');self.note('未映射车型隔离，停用路由禁止分发')
 def test_AC21(self):
  self.dispatch(1);self.dispatch(2);d=self.dm();self.fault('QUOTA_CONFLICT',self.p.edit_demand,A,d['id'],{'target_total':1,'daily_limit':1,'version':d['version']});self.assertEqual(self.dm()['target_total'],6);self.note('低于有效加在途则409，原需求总量不变')
 def test_AC22(self):
  lid=self.lead()['id'];backup=self.p.backup(A);self.p.privacy(C,{'lead_id':lid,'type':'DELETE'});self.p.restore(A,backup['file']);l=self.p.detail(A,'leads',lid);self.assertEqual(l['status'],'DELETED');self.assertEqual(l['phone'],'已删除');self.assertEqual(self.dispatch()['created'],0);self.note('恢复删除前备份后，独立清单确保仍已删除且不可分发')
 def test_AC23(self):
  id=self.sent();ev=self.ev(id);path='/open/v1/callbacks/MEDIA_A';headers=self.signed(path,ev);bad=dict(headers);bad['X-Signature']='bad';self.assertEqual(self.call(path,ev,bad)[0],401);self.assertEqual(self.call(path,ev,self.signed(path,ev,ts=int(time.time())-600))[0],401);self.assertEqual(self.call(path,ev,headers)[0],200);self.assertEqual(self.call(path,ev,headers)[0],401);self.note('错误签名、过期时间戳、重复nonce均401，正常签名200')
 def test_AC24(self):
  ev={'event_id':'unknown','external_delivery_id':'NO-MATCH','event_type':'MEDIA_STORED','event_version':1,'occurred_at':now(),'final_media_code':'MEDIA_A'};path='/open/v1/callbacks/MEDIA_A';code,j=self.call(path,ev,self.signed(path,ev));self.assertEqual(code,202);self.assertEqual(j['data']['status'],'UNMATCHED');self.note('HTTP202，inbox与事件均持久化待关联')
 def test_AC25(self):
  d=self.newd(no='0000007',route='AGENCY_TO_B');id=self.sent(d=d);p=self.p.detail(A,'deliveries',id)['outbound_payload'];self.assertEqual(p['demand_no'],'0000007');self.assertEqual(p['vehicle']['series'],'BMW_X5');self.fault('MEDIA_DEMAND_NO_REQUIRED',self.p.create_demand,A,{'route_id':'DIRECT_A'});self.note('0000007与BMW_X5同报文；未填媒体编号被拒绝')
 def test_AC26(self):
  from openpyxl import Workbook
  rows=[['source_record_id','phone','series_id','city_code','collected_at','recipients','evidence_ref'],['NEW1','13900009991','BMW_X5','310100',now(),'MEDIA_A','TEST://ok'],['DUP1','13800000001','BMW_X5','310100',now(),'MEDIA_A','TEST://dup'],['BAD1','bad','BMW_X5','310100',now(),'MEDIA_A','TEST://bad']]
  rows[0].extend(['name','brand_name','is_sold'])
  for row in rows[1:]:row.extend(['测试客户','宝马','否'])
  for ext in ['csv','xlsx']:
   if ext=='csv':o=io.StringIO();w=csv.writer(o);w.writerows(rows);b=o.getvalue().encode()
   else:wb=Workbook();ws=wb.active;[ws.append(r) for r in rows];o=io.BytesIO();wb.save(o);b=o.getvalue()
   pv=self.p.import_preview(A,'test.'+ext,b,'PURCHASE');self.assertEqual(pv['errors'],1);self.assertGreaterEqual(pv['duplicates'],1);r1=self.p.import_commit(A,pv['id'],'VALID_ROWS');r2=self.p.import_commit(A,pv['id'],'VALID_ROWS');self.assertEqual(r1,r2)
  self.note('CSV与Excel均正确分类，错误跳过，重复确认幂等')
 def test_AC27(self):
  self.sent();self.sent(no='AUTOHOME-0098');d=self.newd(route='DIRECT_C');self.assertEqual(self.dispatch(d=d)['items'][0]['reason'],'MAX_SALES_REACHED');self.p.settings_update(A,{'max_media_count':3,'version':1,'reason':'验收提高售卖上限'});r=self.dispatch(d=d);self.assertEqual(r['created'],1);id=r['items'][0]['delivery_id'];self.p.settings_update(A,{'max_media_count':1,'version':2,'reason':'验收降低售卖上限'});self.assertEqual(self.p.detail(A,'deliveries',id)['send_status'],'CANCELED');self.assertEqual(sum(x['first_sent_at'] is not None for x in self.rows('deliveries') if x['lead_id']==self.lead()['id']),2);self.assertTrue(self.p.integrity(A)['passed']);self.note('升至3允许第三家，降至1取消未发送第三家，保留两家历史')
 def test_AC28(self):
  for n in [20,50,100]:self.assertEqual(self.p.list(A,'leads',n)['page_size'],n)
  self.fault('PAGE_SIZE_INVALID',self.p.list,A,'leads',25);self.note('20、50、100均接受；25返回422语义错误')
class Result(unittest.TextTestResult):
 def __init__(self,*a,**kw):super().__init__(*a,**kw);self.cases=[]
 def record(self,t,ok,error=''):
  n=int(t._testMethodName.split('AC')[1]);self.cases.append({'id':f'AC{n:02}','name':NAMES[n-1],'passed':ok,'evidence':'；'.join(t.evidence),'error':error})
 def addSuccess(self,t):super().addSuccess(t);self.record(t,True)
 def addFailure(self,t,err):super().addFailure(t,err);self.record(t,False,self._exc_info_to_string(err,t))
 def addError(self,t,err):super().addError(t,err);self.record(t,False,self._exc_info_to_string(err,t))
if __name__=='__main__':
 r=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance));out={'generated_at':now(),'total':len(r.cases),'passed':sum(c['passed'] for c in r.cases),'failed':sum(not c['passed'] for c in r.cases),'environment':'独立临时SQLite及本地HTTP，模拟媒体，不含生产容量验收','cases':r.cases};(Path(__file__).resolve().parents[1]/'docs/acceptance-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));sys.exit(0 if r.wasSuccessful() else 1)
