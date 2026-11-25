#!/usr/bin/env python3
import argparse,base64,csv,hashlib,hmac,io,json,mimetypes,os,secrets,sqlite3,sys,threading,time,traceback
import re
from decimal import Decimal
from collections import defaultdict,deque
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from domain import Platform,Fault,PERMS,now,uid,later
from security import canonical,digest,verify_password,sign
from media_vehicles import MediaVehicles
from media_catalogs import MediaCatalogs
from demand_sync import DemandSync
from demand_excel import DemandExcel,template_bytes
from bootstrap import prepare_startup
from partner_edit import update_partner
import yiche_transport
import zhemanhui
ROOT=Path(__file__).resolve().parent
P=None
VEHICLES=None
DEMAND_SYNC=None
LIMITS=defaultdict(deque)
TEST_OPEN_SECRET='TEST-ONLY-CALLBACK-SECRET-2026'
class Handler(BaseHTTPRequestHandler):
 server_version='LeadPlatformLocal/1.1'
 def log_message(self,fmt,*args):
  # Never log request bodies, tokens, PII, or download queries.
  if args:print(time.strftime('%H:%M:%S'),self.command,urlparse(self.path).path,str(args[1] if len(args)>1 else ''))
 def send(self,status,data,ctype='application/json; charset=utf-8',extra=None):
  b=canonical(data).encode() if isinstance(data,(dict,list)) else (data.encode() if isinstance(data,str) else data)
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','same-origin');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
  for k,v in (extra or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(b)
 def ok(self,data,status=200):self.send(status,{'code':'OK','message':'成功','data':data,'request_id':self.req})
 def auth(self):
  cookie=SimpleCookie(self.headers.get('Cookie',''));token=cookie.get('lead_session');token=token.value if token else '';c=P.connect()
  try:
   row=c.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires_at>? AND u.enabled=1',(digest(token),time.time())).fetchone()
   if not row:raise Fault('UNAUTHENTICATED','请先登录',401)
   return dict(row)
  finally:c.close()
 def rate(self,key,limit=60,seconds=60):
  q=LIMITS[key];t=time.time()
  while q and q[0]<t-seconds:q.popleft()
  if len(q)>=limit:raise Fault('RATE_LIMIT','操作频繁，请稍后再试',429)
  q.append(t)
 def do_GET(self):self.handle_request('GET')
 def do_POST(self):self.handle_request('POST')
 def do_PATCH(self):self.handle_request('PATCH')
 def do_DELETE(self):self.handle_request('DELETE')
 def handle_request(self,method):
  self.req=uid('req_')
  try:
   parsed=urlparse(self.path);path=parsed.path;qs={k:v[-1] for k,v in parse_qs(parsed.query).items()};body=b'';data={}
   host=self.headers.get('Host','')
   public_test=os.environ.get('LEAD_PUBLIC_TEST')=='1'
   public_origin=os.environ.get('LEAD_PUBLIC_ORIGIN','').rstrip('/')
   extra_hosts={v.strip().lower() for v in os.environ.get('LEAD_ALLOWED_HOSTS','').split(',') if v.strip()}
   if '*' not in extra_hosts and urlparse('//'+host).hostname not in ({'127.0.0.1','localhost','::1'}|extra_hosts):raise Fault('HOST_FORBIDDEN','访问域名未授权',403)
   if method!='GET':
    origin=self.headers.get('Origin')
    if origin and origin not in ([public_origin] if public_test and public_origin else ['http://'+host,'https://'+host]):raise Fault('ORIGIN_FORBIDDEN','禁止跨站提交',403)
    n=int(self.headers.get('Content-Length','0'))
    if n>15*1024*1024:raise Fault('BODY_TOO_LARGE','请求过大',413)
    body=self.rfile.read(n)
    try:data=json.loads(body) if body else {}
    except:raise Fault('JSON_INVALID','JSON格式错误',400)
   if path==zhemanhui.CALLBACK_PATH:
    if method!='GET':return self.send(405,{'resultCode':1})
    try:
     P.receive_zmh_callback(zhemanhui.callback_fields(parsed.query))
     return self.send(200,{'resultCode':0,'message':'success'})
    except Fault as error:return self.send(error.status,{'resultCode':1,'message':'callback rejected'})
    except ValueError:return self.send(400,{'resultCode':1,'message':'invalid parameters'})
   if path=='/health':return self.ok({'ready':True,'mode':'PUBLIC_TEST' if public_test else 'LOCAL_TEST','mock_enabled':P.mock_enabled,'external_sending':P.yiche_live is not None,**({'seen_host':host} if os.environ.get('LEAD_DEBUG_HOST')=='1' else {})})
   if path==yiche_transport.CALLBACK_PATH and public_test:raise Fault('LIVE_DISABLED','外部测试环境不接收真实媒体回调',403)
   if path==yiche_transport.CALLBACK_PATH:
    return self.yiche_callback(method,parsed.query)
   if path.startswith('/open/v1/'):
    return self.open_api(method,path,qs,body,data)
   if path=='/api/v1/login' and method=='POST':
    self.rate('login:'+self.client_address[0],30,60);c=P.connect()
    try:r=c.execute('SELECT * FROM users WHERE username=? AND enabled=1',(data.get('username'),)).fetchone()
    finally:c.close()
    if not r or not verify_password(data.get('password',''),r['password_hash']):raise Fault('LOGIN_FAILED','账号或密码错误',401)
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
    with P.tx() as c:c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),r['id'],time.time()+28800,csrf));P.audit(c,dict(r),'LOGIN',r['id'])
    return self.send(200,{'code':'OK','data':{'username':r['username'],'role':r['role'],'csrf':csrf},'request_id':self.req},extra={'Set-Cookie':f'lead_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'+('; Secure' if public_test else '')})
   if not path.startswith('/api/'):
    if path in ['/','/index.html']:file=ROOT/'static/index.html'
    elif path.startswith('/static/'):
     file=(ROOT/path.lstrip('/')).resolve()
     if not file.is_relative_to(ROOT/'static'):raise Fault('NOT_FOUND','文件不存在',404)
    else:raise Fault('NOT_FOUND','页面不存在',404)
    if not file.exists():raise Fault('NOT_FOUND','文件不存在',404)
    content=file.read_bytes()
    if file.name=='index.html':content=content.replace(b'<head>',b'<head><meta name="demo-mode" content="'+(b'true' if getattr(P,'demo_mode',False) else b'false')+b'">',1)
    if public_test and file.name=='index.html':content=content.replace(b'<head>',b'<head><meta name="public-test" content="true">',1)
    return self.send(200,content,mimetypes.guess_type(file)[0] or 'application/octet-stream')
   a=self.auth();P.require(a,'read')
   if method!='GET' and self.headers.get('X-CSRF-Token')!=a['csrf']:raise Fault('CSRF_FAILED','会话校验失败，请刷新',403)
   self.rate(a['id']+':'+path,600)
   if path=='/api/v1/me':return self.ok({k:a[k] for k in ['id','username','role','org_id','csrf']}|{'permissions':sorted(PERMS[a['role']])})
   if path=='/api/v1/logout' and method=='POST':
    with P.tx() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(digest(SimpleCookie(self.headers.get('Cookie',''))['lead_session'].value),))
    return self.send(200,{'code':'OK'},extra={'Set-Cookie':'lead_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'+('; Secure' if public_test else '')})
   if path.startswith('/api/v1/channels/') and path.endswith('/api-key') and method=='POST':
    channel_id=path.split('/')[-2]
    return self.ok(P.issue_source_key(a,channel_id),201)
   if path=='/api/v1/yiche-codes':
    if method=='GET':return self.ok(P.yiche_codes(a))
    if method in ('POST','PATCH','DELETE'):return self.ok(P.edit_yiche_codes(a,method,data))
   if path.startswith('/api/v1/media-catalogs/'):
    parts=path.strip('/').split('/')
    catalogs=MediaCatalogs(P)
    if len(parts)==4 and method=='GET':return self.ok(catalogs.overview(a,parts[3]))
    if len(parts)==5 and method=='GET':
     return self.ok(catalogs.list(a,parts[4],parts[3],qs.get('q',''),int(qs.get('page','1')),int(qs.get('page_size','20'))))
    if len(parts)==6 and parts[5]=='options' and method=='GET':return self.ok(catalogs.entry_options(a,parts[4]))
    if len(parts)==6 and parts[5]=='config' and method=='PATCH':return self.ok(catalogs.configure(a,parts[4],parts[3],data))
    if len(parts)==6 and parts[5]=='sync' and method=='POST':return self.ok(catalogs.start(a,parts[4],parts[3]),202)
   if path=='/api/v1/media-vehicles' and method=='GET':
    return self.ok(VEHICLES.overview(a))
   if path.startswith('/api/v1/media-vehicles/'):
    parts=path.strip('/').split('/')
    if len(parts)==4 and method=='GET':
     page=max(1,min(10000,int(qs.get('page','1'))))
     return self.ok(VEHICLES.list(a,parts[3],qs.get('q',''),page))
    if len(parts)==5 and parts[4]=='sync' and method=='POST':
     return self.ok(VEHICLES.start(a,parts[3]),202)
    if len(parts)==5 and parts[4]=='config' and method=='PATCH':
     enabled=data.get('enabled',True)
     if not isinstance(enabled,bool):raise Fault('VEHICLE_CONFIG_INVALID','同步状态格式错误')
     VEHICLES.configure(a,parts[3],data.get('cid'),data.get('key',''),enabled)
     return self.ok({'saved':True})
   if path=='/api/v1/lead-entry-options' and method=='GET':return self.ok(P.lead_entry_options(a))
   if path=='/api/v1/demands/summary' and method=='GET':return self.ok(P.demand_summary(a,int(qs.get('page_size',20)),int(qs.get('page',1)),qs.get('q',''),qs.get('status',''),qs.get('media','')))
   if path=='/api/v1/partner-projects' and method=='POST':return self.ok(P.project_add(a,data.get('partner_id'),data.get('project')),201)
   if path=='/api/v1/partner-projects/status' and method=='PATCH':return self.ok(P.project_status_update(a,data.get('partner_id'),data.get('project'),data))
   if path=='/api/v1/partner-projects/mode' and method=='PATCH':return self.ok(P.project_mode_update(a,data.get('partner_id'),data.get('project'),data))
   if path=='/api/v1/catalogs':return self.ok(P.catalogs(a))
   if path=='/api/v1/reports/funnel':return self.ok(P.reports(a))
   if path=='/api/v1/integrity':return self.ok(P.integrity(a))
   if path=='/api/v1/maintenance' and method=='POST':return self.ok(P.maintenance(a))
   if path=='/api/v1/backup' and method=='POST':return self.ok(P.backup(a))
   if path=='/api/v1/acceptance':
    p=ROOT/'docs/acceptance-results.json';result=json.loads(p.read_text()) if p.exists() else {'cases':[],'note':'测试报告尚未生成'}
    for name,filename in [('security','security-results.json'),('interaction','dom-results.json'),('browser','browser-results.json')]:
     f=ROOT/'docs'/filename
     if f.exists():result[name]=json.loads(f.read_text())
    return self.ok(result)
   if path=='/api/v1/demand-excel/template' and method=='GET':
    P.require(a,'demand');return self.send(200,template_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',{'Content-Disposition':'attachment; filename="demand-template.xlsx"'})
   if path=='/api/v1/demand-excel/preview' and method=='POST':
    P.require(a,'demand')
    try:content=base64.b64decode(data.get('content',''),validate=True)
    except (ValueError,TypeError):raise Fault('FILE_INVALID','文件内容编码无效')
    return self.ok(DemandExcel(P,DEMAND_SYNC).preview(a,data.get('filename',''),content,data.get('config',{})))
   if path=='/api/v1/demand-excel/commit' and method=='POST':
    return self.ok(DemandExcel(P,DEMAND_SYNC).commit(a,data.get('id','')))
   if path=='/api/v1/demand-sync/sources':
    if method=='GET':return self.ok(DEMAND_SYNC.sources(a))
    if method=='POST':
     if data.get('source_kind')=='API_KA':
      from ka_demand_sources import KaDemandSources
      return self.ok(KaDemandSources(DEMAND_SYNC).save(a,data),201)
     return self.ok(DEMAND_SYNC.save(a,data),201)
   if path.startswith('/api/v1/demand-sync/sources/'):
    parts=path.strip('/').split('/');source_id=parts[4]
    if len(parts)==5 and method=='DELETE':return self.ok(DEMAND_SYNC.delete(a,source_id,data.get('version')))
    if len(parts)==5 and method=='PATCH':
     conn=P.connect()
     try:kind=DEMAND_SYNC._source(conn,a,source_id)['source_kind']
     finally:conn.close()
     if kind=='API_KA':
      from ka_demand_sources import KaDemandSources
      return self.ok(KaDemandSources(DEMAND_SYNC).save(a,data,source_id))
     return self.ok(DEMAND_SYNC.save(a,data,source_id))
    if len(parts)==6:
     if parts[5]=='sync' and method=='POST':return self.ok(DEMAND_SYNC.run(a,source_id))
     if parts[5]=='runs' and method=='GET':return self.ok(DEMAND_SYNC.runs(a,source_id))
     if parts[5]=='projects' and method=='GET':return self.ok(DEMAND_SYNC.projects(a,source_id,int(qs.get('page',1)),int(qs.get('page_size',20)),qs.get('q',''),qs.get('present','1')))
   if path=='/api/v1/demand-sync/projects' and method=='GET':return self.ok(DEMAND_SYNC.all_projects(a,int(qs.get('page',1)),int(qs.get('page_size',20)),{k:qs[k] for k in ('partner_id','project','brand','model','city','match_status') if k in qs}))
   if path.startswith('/api/v1/demand-sync/projects/') and method=='GET':return self.ok(DEMAND_SYNC.detail(a,path.rsplit('/',1)[-1]))
   if path=='/api/v1/settings/lead-fields':
    if method=='GET':return self.ok(P.lead_fields_settings(a))
    if method=='PATCH':return self.ok(P.update_lead_fields(a,data['account_id'],data['fields']))
   if path=='/api/v1/settings/distribution':
    if method=='GET':return self.ok(P.catalogs(a)['settings'])
    if method=='PATCH':return self.ok(P.settings_update(a,data))
   if path=='/api/v1/matches/leads' and method=='GET':
    return self.ok(P.matching_leads(a,int(qs.get('page_size',20)),int(qs.get('page',1)),
       {k:qs[k] for k in ('lead_no','city','brand','model','sales_count','matched_media','project') if k in qs},qs.get('selected_ids','').split(','),qs.get('buyer_ids','').split(',') if 'buyer_ids' in parse_qs(parsed.query,keep_blank_values=True) else None))
   if path=='/api/v1/matches/preview' and method=='POST':return self.ok(P.preview(a,data.get('lead_ids'),data.get('demand_ids'),data.get('projects'),data.get('dispatch_mode','MANUAL')))
   if path=='/api/v1/dispatches' and method=='POST':
    projects=data.get('projects')
    if not isinstance(projects,dict):raise Fault('SALE_PROJECT_REQUIRED','请选择分发项目（KA或会员）')
    return self.ok(P.dispatch(a,[(x['lead_id'],x['demand_id']) for x in data.get('pairs',[])],
        self.headers.get('Idempotency-Key'),data.get('preview_version'),projects),202)
   if path=='/api/v1/distribution/auto' and method=='GET':return self.ok(P.auto_status(a))
   if path=='/api/v1/distribution/auto/run' and method=='POST':return self.ok(P.auto_run(a))
   if path=='/api/v1/distribution/auto/bind' and method=='POST':return self.ok(P.auto_bind(a,[(x['lead_id'],x['demand_id']) for x in data.get('pairs',[])]))
   if path=='/api/v1/worker/run' and method=='POST':return self.ok(P.worker(a))
   if path=='/api/v1/inbox/run' and method=='POST':return self.ok(P.process_inbox(a))
   if path=='/api/v1/template' and method=='GET':
    headers=['姓名','电话','品牌','车型','城市','获客渠道','是否售卖','售卖媒体','项目','售卖时间','性别']
    rows=[['测试未售客户','13900009991','宝马','宝马 X5','上海市','信息流投放','否','','','','未登记'],
          ['测试已售客户','13900009992','宝马','宝马 X5','上海市','TMK','是','MEDIA_A','会员',now(),'未登记']]
    if qs.get('format')=='csv':
     f=io.StringIO();w=csv.writer(f);w.writerow(headers);w.writerows(rows);return self.send(200,'\ufeff'+f.getvalue(),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="leads-template.csv"'})
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    wb=Workbook();ws=wb.active;ws.title='线索导入';ws.append(headers)
    for r in rows:ws.append(r)
    for col in ws.columns:
     ws.column_dimensions[col[0].column_letter].width=23
     for cell in col:cell.number_format='@'
    for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='123F32')
    f=io.BytesIO();wb.save(f);return self.send(200,f.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',{'Content-Disposition':'attachment; filename="leads-template.xlsx"'})
   rest=path.removeprefix('/api/v1/').split('/');table=rest[0]
   if table in ['leads','demands','deliveries','imports','callback_events','privacy_requests','audit_log','exports']:
    if len(rest)==1:
     if method=='GET':
      if table=='audit_log':P.require(a,'settings')
      return self.ok(P.list(a,table,int(qs.get('page_size',20)),int(qs.get('page',1)),qs.get('q',''),qs.get('status',''),qs.get('channel',''),qs.get('media',''),
                            lead_filters={k:qs[k] for k in ('city','brand','model','supplier','acquisition_channel','from','to','sold_media','project') if k in qs}))
     if table=='leads':return self.ok(P.create_lead(a,data),201)
     if table=='demands':return self.ok(P.create_demand(a,data),201)
     if table=='imports':
      if not data.get('supplier_id'):raise Fault('SUPPLIER_REQUIRED','请选择线索供应商')
      return self.ok(P.import_preview(a,data['filename'],base64.b64decode(data['content']),data.get('channel_id'),data.get('sheet'),data.get('column_mapping'),data['supplier_id']),202)
     if table=='privacy_requests' and method=='POST':return self.ok(P.privacy(a,data),202)
     if table=='exports':return self.ok(P.export_request(a,data.get('purpose','')),202)
    id=rest[1]
    if len(rest)==2:
     if method=='GET' and table in ['leads','demands','deliveries']:return self.ok(P.detail(a,table,id))
     if method=='PATCH' and table=='demands':return self.ok(P.edit_demand(a,id,data))
     if method=='PATCH' and table=='leads':return self.ok(P.edit_yiche_lead(a,id,data))
    if len(rest)>=3:
     action=rest[2]
     read_actions={'timeline','errors','download'}
     if (action in read_actions and method!='GET') or (action not in read_actions and method!='POST'):raise Fault('METHOD_NOT_ALLOWED','请使用正确的请求方法',405)
     if table=='leads' and action=='gender':return self.ok(P.edit_lead_gender(a,id,data.get('gender'),data['version']))
     if table=='leads' and action=='title':return self.ok(P.edit_lead_title(a,id,data['customer_title'],data['version']))
     if table=='leads' and action=='review':return self.ok(P.review_consent(a,id))
     if table=='leads' and action=='reveal':return self.ok(P.reveal(a,id,data.get('purpose','')))
     if table=='demands' and action in ['submit','approve','pause','resume','close']:return self.ok(P.demand_action(a,id,action,data.get('version',0)))
     if table=='deliveries':
      if action=='project':return self.ok(P.fill_delivery_project(a,id,data.get('project')))
      if action=='timeline':return self.ok(P.detail(a,table,id))
      if action=='mock-callback':return self.ok(P.mock_callback(a,id,data['action'],data.get('version'),data.get('event_id')))
      if action=='reconcile':return self.ok(P.reconcile(a,id))
     if table=='imports' and action=='commit':return self.ok(P.import_commit(a,id,data.get('mode','VALID_ROWS')))
     if table=='imports' and action=='errors':
      c=P.connect();im=P.one(c,'imports',id,a);c.close();pv=json.loads(im['preview']);f=io.StringIO();w=csv.writer(f);w.writerow(['行号','脱敏手机号','错误原因'])
      for row in pv['rows']:
       if row['errors']:w.writerow([row['row'],row['phone'],'；'.join(row['errors'])])
      return self.send(200,'\ufeff'+f.getvalue(),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="import-errors.csv"'})
     if table=='exports':
      if action=='approve':return self.ok(P.export_approve(a,id))
      if action=='download':return self.send(200,P.export_download(a,id),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="leads-approved.csv"'})
   if table in ['partners','channels','routes','vehicles'] and len(rest)==1 and method=='POST':
    P.require(a,'settings')
    with P.tx() as c:
     id=uid('cfg_');name=str(data.get('name','')).strip()
     if table in ('partners','channels'):
      price_text=str(data.get('cooperation_price_yuan','')).strip()
      if price_text and not re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,2})?',price_text):
       raise Fault('COOPERATION_PRICE_INVALID','合作价格须为非负金额，最多两位小数')
      price_fen=int(Decimal(price_text)*100) if price_text else None
      note=data.get('cooperation_note','')
      if not isinstance(note,str) or len(note.strip())>1000:
       raise Fault('COOPERATION_NOTE_INVALID','备注说明最多 1000 字')
      note=note.strip()
     if table=='partners':
      role=data.get('role');end=data.get('contract_end',later(days=365))
      if not name or role not in ['MEDIA','AGENCY','SOURCE']:raise Fault('PARTNER_INVALID','请填写名称并选择主体角色')
      project=data.get('cooperation_project')
      if role in ('MEDIA','AGENCY') and project not in ('MEMBER','KA'):raise Fault('COOPERATION_PROJECT_REQUIRED','请选择合作项目（会员或KA）')
      from domain import parse
      parse(end)
      c.execute('''INSERT INTO partners(id,org_id,name,role,enabled,contract_end,cooperation_price_fen,cooperation_note,cooperation_project)
                   VALUES(?,?,?,?,1,?,?,?,?)''',(id,a['org_id'],name,role,end,price_fen,note,project))
      if role in ('MEDIA','AGENCY'):P._ensure_partner_projects(c,a['org_id'],id,(project,))
      if role=='AGENCY':P.attach_agency_media(c,a,id,data)
     elif table=='channels':
      source_id=data.get('source_partner_id')
      raw_type=data.get('type')
      ty=' '.join(raw_type.split()) if isinstance(raw_type,str) else ''
      if not name or not ty or len(ty)>50:
       raise Fault('CHANNEL_INVALID','请填写供应商名称和获客方式（最多 50 字）')
      known_types={'投放':'ADS','采买':'PURCHASE','ads':'ADS','purchase':'PURCHASE','tmk':'TMK'}
      known_types.update((row['type'].casefold(),row['type']) for row in
                         c.execute('SELECT DISTINCT type FROM channels WHERE org_id=?',(a['org_id'],)))
      ty=known_types.get(ty.casefold(),ty)
      if source_id:
       partner=P.one(c,'partners',source_id,a)
       if partner['role']!='SOURCE':
        raise Fault('CHANNEL_INVALID','来源主体必须是线索来源方')
      else:
       source_id=uid('src_')
       c.execute("""INSERT INTO partners(id,org_id,name,role,contract_end)
                    VALUES(?,?,?,'SOURCE',?)""",(source_id,a['org_id'],name,later(days=365)))
      c.execute("""INSERT INTO channels(id,org_id,name,type,source_partner_id,cooperation_price_fen,cooperation_note)
                   VALUES(?,?,?,?,?,?,?)""",(id,a['org_id'],name,ty,source_id,price_fen,note))
     elif table=='routes':
      receiver=P.one(c,'partners',data.get('receiver_id'),a);media=P.one(c,'partners',data.get('media_id'),a)
      if media['role']!='MEDIA' or receiver['role'] not in ['MEDIA','AGENCY'] or (receiver['role']=='MEDIA' and receiver['id']!=media['id']):raise Fault('ROUTE_INVALID','直连时接收方与媒体一致；转发接收方应为乙方')
      c.execute('INSERT INTO routes(id,org_id,buyer_id,receiver_id,media_id) VALUES(?,?,?,?,?)',(id,a['org_id'],receiver['id'],receiver['id'],media['id']))
      if receiver['role']=='AGENCY':c.execute('INSERT OR IGNORE INTO agency_final_media(org_id,agency_id,media_id) VALUES(?,?,?)',(a['org_id'],receiver['id'],media['id']))
     else:
      id=str(data.get('series_id','')).strip()
      if not all(str(data.get(k,'')).strip() for k in ['series_id','brand_id','brand_name','series_name']):raise Fault('VEHICLE_INVALID','品牌和车系编码名称均为必填')
      if c.execute('SELECT 1 FROM catalog WHERE series_id=?',(id,)).fetchone():raise Fault('VEHICLE_DUPLICATE','车系编码已存在',409)
      c.execute('INSERT INTO catalog VALUES(?,?,?,?)',(id,data['brand_id'],data['brand_name'],data['series_name']))
     P.audit(c,a,table.upper()+'_CREATE',id)
    return self.ok({'id':id},201)
   if table=='partners' and len(rest)==2 and method=='PATCH':return self.ok(update_partner(P,a,rest[1],data))
   if table in ['routes','channels','partners'] and len(rest)==2 and method=='PATCH':
    P.require(a,'engineering' if table=='routes' else 'settings')
    with P.tx() as c:
     r=P.one(c,table,rest[1],a)
     if 'enabled' in data:c.execute('UPDATE '+table+' SET enabled=? WHERE id=?',(int(bool(data['enabled'])),r['id']))
     if table=='partners' and 'cooperation_project' in data:
      if r['role'] not in ('MEDIA','AGENCY') or data['cooperation_project'] not in ('MEMBER','KA'):raise Fault('COOPERATION_PROJECT_REQUIRED','请选择合作项目（会员或KA）')
      c.execute('UPDATE partners SET cooperation_project=? WHERE id=?',(data['cooperation_project'],r['id']))
     if table=='routes':
      mode=data.get('mode',r['mode'])
      if mode not in ['ACK','TIMEOUT']:raise Fault('MODE_INVALID','仅允许模拟接收或模拟超时')
      c.execute('UPDATE routes SET mode=?,supports_query=?,supports_idempotency=? WHERE id=?',(mode,int(data.get('supports_query',r['supports_query'])),int(data.get('supports_idempotency',r['supports_idempotency'])),r['id']))
     P.audit(c,a,table.upper()+'_CONFIG',r['id'],data)
    return self.ok({'ok':True})
   if path.startswith('/api/v1/privacy-notices/') and method=='POST':
    P.require(a,'privacy');id=path.split('/')[-1]
    if not P.mock_enabled:raise Fault('MOCK_DISABLED','当前环境已禁用模拟通知确认',403)
    with P.tx() as c:
     n=c.execute('SELECT n.*,p.org_id FROM privacy_notices n JOIN privacy_requests p ON p.id=n.request_id WHERE n.id=? AND p.org_id=?',(id,a['org_id'])).fetchone()
     if not n:raise Fault('NOT_FOUND','通知不存在',404)
     c.execute("UPDATE privacy_notices SET status='CONFIRMED',confirmed_at=? WHERE id=?",(now(),id))
     if not c.execute("SELECT 1 FROM privacy_notices WHERE request_id=? AND status!='CONFIRMED'",(n['request_id'],)).fetchone():c.execute("UPDATE privacy_requests SET status='COMPLETED' WHERE id=?",(n['request_id'],))
     P.audit(c,a,'MOCK_PRIVACY_CONFIRM',id)
    return self.ok({'mock':True})
   if path=='/api/v1/privacy-notices' and method=='GET':
    P.require(a,'privacy');c=P.connect()
    try:return self.ok([dict(x) for x in c.execute('SELECT n.*,p.type,p.lead_id FROM privacy_notices n JOIN privacy_requests p ON p.id=n.request_id WHERE p.org_id=?',(a['org_id'],))])
    finally:c.close()
   raise Fault('NOT_FOUND','接口不存在',404)
  except Fault as e:self.send(e.status,{'code':e.code,'message':e.message,'data':None,'request_id':self.req})
  except (ValueError,KeyError,TypeError) as e:self.send(422,{'code':'VALIDATION_ERROR','message':'请检查必填字段及字段格式','data':None,'request_id':self.req})
  except sqlite3.Error:
   self.send(503,{'code':'DATABASE_UNAVAILABLE','message':'数据未提交，请稍后重试','request_id':self.req})
  except Exception as e:
   print('Unhandled',type(e).__name__,self.req);traceback.print_exc(limit=3);self.send(500,{'code':'INTERNAL_ERROR','message':'处理失败，请保留请求编号','request_id':self.req})
 def yiche_callback(self,method,query):
  if method!='GET':
   return self.send(405,{'resultCode':1,'resultMessage':'method not allowed'},extra={'Allow':'GET'})
  try:
   parsed=parse_qs(query,keep_blank_values=True,max_num_fields=16)
   required={'platFormId','orderId','callbackToken','resultCode'}
   if not required.issubset(parsed) or any(len(values)!=1 for values in parsed.values()):
    raise ValueError('invalid callback parameters')
   P.receive_yiche_callback({name:values[0] for name,values in parsed.items()})
  except Fault as e:
   return self.send(e.status,{'resultCode':1,'resultMessage':'callback rejected'})
  except (ValueError,KeyError):
   return self.send(400,{'resultCode':1,'resultMessage':'invalid callback parameters'})
  except sqlite3.Error:
   return self.send(503,{'resultCode':1,'resultMessage':'please retry'})
  return self.send(200,{'resultCode':0,'resultMessage':'success'})
 def open_api(self,method,path,qs,body,data):
  if method!='POST' or qs:raise Fault('SCHEMA_INVALID','开放写接口不接受查询参数',400)
  kid=self.headers.get('X-Key-Id','');ts=self.headers.get('X-Timestamp','');nonce=self.headers.get('X-Nonce','');signature=self.headers.get('X-Signature','')
  binding=P.source_api_key(kid) if kid.startswith('source_') else None
  a={'id':'open:'+kid,'role':'admin','org_id':binding['org_id'] if binding else 'demo'}
  def fail():
   with P.tx() as c:P.audit(c,a,'OPEN_SIGNATURE_REJECTED',kid)
   raise Fault('SIGNATURE_INVALID','签名、时间戳或nonce校验失败',401)
  if os.environ.get('LEAD_PUBLIC_TEST')=='1' and not binding:fail()
  if not binding and kid not in ['TEST-MEDIA_A','TEST-MEDIA_B','TEST-AGENCY_A','TEST-SOURCE_ADS']:fail()
  try:valid=abs(time.time()-int(ts))<=300
  except:valid=False
  if not valid or not nonce or not hmac.compare_digest(sign(binding['secret'] if binding else TEST_OPEN_SECRET,method,path,ts,nonce,body),signature):fail()
  with P.tx() as c:
   c.execute('DELETE FROM nonces WHERE expires_at<?',(time.time(),))
   if c.execute('SELECT 1 FROM nonces WHERE key_id=? AND nonce=?',(kid,nonce)).fetchone():c.commit();fail()
   c.execute('INSERT INTO nonces VALUES(?,?,?)',(kid,nonce,time.time()+600))
  self.rate('open:'+kid,100,1)
  if path=='/open/v1/leads' and (binding or kid=='TEST-SOURCE_ADS'):
   a['role']='operator'
   if not isinstance(data,dict):raise Fault('SCHEMA_INVALID','请求须为对象',400)
   items=data.get('items',[data])
   if not isinstance(items,list) or not 1<=len(items)<=100:raise Fault('BATCH_TOO_LARGE','每批1至100条')
   res=[]
   for row in items:
    try:
     if not isinstance(row,dict):raise Fault('SCHEMA_INVALID','线索须为对象')
     channel_id=row.get('channel_id',data.get('channel_id'))
     expected_channel=binding['channel_id'] if binding else 'ADS'
     if binding and not channel_id:raise Fault('CHANNEL_REQUIRED','请传平台渠道号 channel_id')
     if channel_id and channel_id!=expected_channel:raise Fault('CHANNEL_FORBIDDEN','渠道号与API凭证不匹配',403)
     res.append(P.source_ingest(a,expected_channel,row))
    except Fault as error:res.append({'status':'REJECTED','code':error.code,'message':error.message})
   return self.ok({'items':res},202)
  if path.startswith('/open/v1/callbacks/'):
   partner=path.split('/')[-1]
   if binding or kid!='TEST-'+partner:raise Fault('FORBIDDEN','凭证与回调主体不匹配',403)
   r=P.receive_callback(a,partner,data);return self.ok(r,202 if r['status']=='UNMATCHED' else 200)
  raise Fault('FORBIDDEN','凭证无权访问该接口',403)
def run():
 global P,VEHICLES,DEMAND_SYNC
 ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=int(os.environ.get('PORT') or 8765));ap.add_argument('--host',default=os.environ.get('HOST') or '127.0.0.1');ap.add_argument('--data',default=str(ROOT/'data'));ap.add_argument('--no-vehicle-sync',action='store_true',help='仅恢复本地预览，不启动媒体车型库定时同步');ap.add_argument('--seed-demo',action='store_true',help='显式初始化本机演示数据，禁止外部推送环境使用');args=ap.parse_args()
 try:live=None if os.environ.get('LEAD_PUBLIC_TEST')=='1' else yiche_transport.from_environment()
 except ValueError as error:ap.error(str(error))
 if os.environ.get('LEAD_PUBLIC_TEST')=='1':
  origin=urlparse(os.environ.get('LEAD_PUBLIC_ORIGIN',''))
  allowed=os.environ.get('LEAD_ALLOWED_HOSTS','').split(',')
  if origin.scheme!='https' or ('*' not in allowed and origin.hostname not in allowed):ap.error('外部测试须配置HTTPS地址与匹配域名')
 P=Platform(args.data)
 try:prepare_startup(P,args.seed_demo,args.host,os.environ.get('LEAD_PUBLIC_TEST')=='1',bool(live) or os.environ.get('ZMH_ENABLE_TEST_PUSH')=='1')
 except ValueError as error:ap.error(str(error))
 P.mock_enabled=os.environ.get('LEAD_DISABLE_MOCK')!='1'
 P.demo_mode=args.seed_demo
 P.enforce_tombstones()
 P.yiche_live=live
 server=ThreadingHTTPServer((args.host,args.port),Handler)
 VEHICLES=MediaVehicles(P)
 catalogs=MediaCatalogs(P)
 if not args.no_vehicle_sync:threading.Thread(target=catalogs.schedule,daemon=True).start()
 if not args.no_vehicle_sync:threading.Thread(target=VEHICLES.schedule, daemon=True).start()
 stop_distribution=threading.Event()
 DEMAND_SYNC=DemandSync(P)
 P.reconcile_external_sources()
 threading.Thread(target=DEMAND_SYNC.schedule,args=(stop_distribution,),daemon=True).start()
 def distribution_schedule():
  while not stop_distribution.wait(30):
   try:
    c=P.connect()
    try:orgs=[r[0] for r in c.execute('SELECT org_id FROM settings WHERE auto_dispatch_enabled=1 AND dispatch_enabled=1')]
    finally:c.close()
    for org in orgs:P.auto_run({'id':'system:auto','role':'admin','org_id':org})
   except Exception as error:print('自动分发扫描失败',type(error).__name__,flush=True)
 threading.Thread(target=distribution_schedule,daemon=True).start()
 print(f'汽车线索平台测试系统 http://127.0.0.1:{args.port}',flush=True);print('易车正式推送已启用' if live else '线索分发为本地模拟',flush=True)
 print('本次运行已暂停车型库定时同步' if args.no_vehicle_sync else '车型库定时同步已启动',flush=True)
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:
  stop_distribution.set()
  VEHICLES.stop.set()
  catalogs.stop.set()
  server.server_close()
if __name__=='__main__':run()
