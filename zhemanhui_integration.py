"""Isolated agency transport and durable per-submission callback correlation."""
import hmac,json,secrets,os
import zhemanhui as protocol
from security import digest,canonical

class ZhemanhuiMixin:
 def _zmh_request(self,c,a,d,l,payload):
  from domain import Fault,uid,now
  # 已确认的软米saveClue协议只绑定会员；worker也需拦截旧队列中的KA交付。
  if d['sale_project']!='MEMBER':raise Fault('ZMH_PROJECT_MISMATCH','软米会员接口不能接收KA或未指定项目的线索')
  config=c.execute('SELECT * FROM zmh_routes WHERE org_id=? AND route_id=?',(a['org_id'],d['route_id'])).fetchone()
  if not config or not config['enabled'] or os.environ.get('ZMH_ENABLE_TEST_PUSH')!='1':raise Fault('ZMH_DISABLED','软米接口尚未开启，禁止模拟为成功')
  from media_catalog_matching import delivery_media_codes
  codes=delivery_media_codes(c,a['org_id'],d,l,payload)
  from route_sending import control
  environment=control(c,a['org_id'],d['route_id'])['environment']
  snapshot_environment=json.loads(d['route_snapshot']).get('send_environment','TEST')
  if environment!=snapshot_environment:raise Fault('ROUTE_SEND_CHANGED','交付环境已变化，请重新匹配')
  token=secrets.token_urlsafe(32)
  req=protocol.prepare(protocol.Config(config['callback_url'],environment=environment),demand_no=payload['demand_no'],city_id=codes['city_id'],series_id=codes['series_id'],name=payload['customer']['name'],mobile=payload['customer']['phone'],delivery_id=d['id'],callback_token=token)
  c.execute('INSERT INTO zmh_submissions(id,org_id,delivery_id,token_hash,created_at) VALUES(?,?,?,?,?)',(d['id'],a['org_id'],d['id'],digest(token),now()))
  return req
 def _send_zmh(self,a,id,request):
  from domain import now
  from urllib.parse import urlsplit
  if urlsplit(request.full_url).path=='/api-test/v1/saveClue':
   outcome=protocol.send_test(request,enabled=True)
  else:
   outcome=protocol.send_request(request,environment='PRODUCTION',enabled=True)
  with self.tx() as c:
   d=self.one(c,'deliveries',id,a);sub=c.execute('SELECT * FROM zmh_submissions WHERE id=?',(id,)).fetchone()
   c.execute('UPDATE zmh_submissions SET send_status=? WHERE id=?',(outcome['status'],id))
   c.execute('UPDATE attempts SET outcome=? WHERE delivery_id=? AND attempt_no=1',(outcome['status'],id))
   if sub['callback_at']:return outcome
   if outcome['status']=='ACKED':
    # Supplier receipt alone says nothing about final-media storage/validity.
    c.execute("UPDATE deliveries SET send_status='ACKED',acked_at=?,error='' WHERE id=?",(now(),id))
    c.execute("UPDATE hops SET acceptance='ACCEPTED',accepted_at=? WHERE delivery_id=? AND hop_no=1",(now(),id))
   elif outcome['status']=='FAILED':
    if d['quota_state']=='RESERVED':self._ledger(c,id,'zmh-reject:'+id,-1,0,'ZMH_REJECTED')
    c.execute("UPDATE deliveries SET send_status='FAILED',quota_state='RELEASED',error=? WHERE id=?",(outcome['error'],id))
   else:c.execute('UPDATE deliveries SET error=? WHERE id=?',(outcome['error'],id))
   self.audit(c,a,'ZMH_SEND_RESULT',id,outcome)
  return outcome
 def receive_zmh_callback(self,params):
  from domain import Fault,now,uid
  id=params['delivery_id'];result=params['resultCode'];code=params['code']
  if result not in ('0','1'):raise Fault('ZMH_CALLBACK_INVALID','resultCode无效',400)
  valid=result=='0' and code=='2001';validity='VALID' if valid else 'INVALID'
  with self.tx() as c:
   sub=c.execute('SELECT * FROM zmh_submissions WHERE id=?',(id,)).fetchone()
   if not sub or not hmac.compare_digest(sub['token_hash'],digest(params['callback_token'])):raise Fault('ZMH_CALLBACK_FORBIDDEN','回调校验失败',403)
   actor={'id':'zmh-callback','org_id':sub['org_id'],'role':'admin'}
   description='有效线索' if valid else self._yiche_code_map(c,sub['org_id']).get(code,('INVALID','未配置失败原因（code='+code+'）'))[1]
   if not valid and code=='2001':description='上报失败（resultCode=1，code=2001）'
   event_id='zmh:'+id+':'+result+':'+code
   if sub['callback_at']:
    if sub['result_code']==result and sub['code']==code:return {'duplicate':True}
    self.audit(c,actor,'ZMH_CALLBACK_CONFLICT',id,{'resultCode':result,'code':code})
    if sub['delivery_id']:c.execute("UPDATE deliveries SET callback_status='CONFLICT',version=version+1 WHERE id=?",(id,))
    return {'conflict':True}
   when=now()
   c.execute('UPDATE zmh_submissions SET result_code=?,code=?,validity=?,description=?,callback_at=? WHERE id=?',(result,code,validity,description,when,id))
   if sub['delivery_id']:
    d=self.one(c,'deliveries',id,actor)
    if not d['first_sent_at']:raise Fault('ZMH_NOT_SENT','分发尚未发送',409)
    self._ledger(c,id,event_id,-int(d['quota_state']=='RESERVED'),int(valid)-int(d['quota_state']=='VALID'),'ZMH_'+validity)
    c.execute("UPDATE deliveries SET send_status='ACKED',acked_at=COALESCE(acked_at,?),validity_status=?,validity_version=1,validated_at=?,storage_status=?,stored_at=?,callback_status='COMPLETE',callback_last_at=?,evidence_level='AGENCY_REPORTED',quota_state=?,settlement_status=?,error=?,version=version+1 WHERE id=?",(when,validity,when,'STORED' if valid else 'REJECTED',when if valid else None,when,'VALID' if valid else 'RELEASED','ELIGIBLE' if valid else 'UNCONFIRMED','' if valid else description,id))
    event={'resultCode':result,'code':code,'reason':description}
    c.execute('INSERT INTO callback_events(id,org_id,partner_id,event_id,delivery_id,event_type,event_version,occurred_at,received_at,body,payload_hash,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(uid('cb_'),sub['org_id'],d['receiver_id'],event_id,id,'VALIDITY_UPDATED',1,when,when,canonical(event),digest(canonical(event)),'PROCESSED'))
   self.audit(c,actor,'ZMH_CALLBACK',id,{'resultCode':result,'code':code,'validity':validity,'description':description})
  return {'processed':True,'validity':validity}
