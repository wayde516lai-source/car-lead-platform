import csv,hmac,io,json,os,re,secrets,sqlite3,time,unicodedata,uuid,zipfile
from contextlib import contextmanager,nullcontext
from datetime import date, datetime,timedelta,timezone
from pathlib import Path
from security import Vault,canonical,digest,password_hash,sign
from demand_statistics import summarize_order_quantities
from project_availability import demand_project_condition
import yiche
import yiche_transport
from zhemanhui_integration import ZhemanhuiMixin
UTC=timezone.utc
SH=timezone(timedelta(hours=8))
def now():return datetime.now(UTC).isoformat(timespec='seconds')
def later(days=0,hours=0):return (datetime.now(UTC)+timedelta(days=days,hours=hours)).isoformat(timespec='seconds')
def uid(prefix=''):return prefix+uuid.uuid4().hex[:20]
def parse(t):return datetime.fromisoformat(t.replace('Z','+00:00')).astimezone(UTC)
def day():return datetime.now(SH).date().isoformat()
def external_demand_no(demand):
    """Return the supplied media number, preserving explicit empty Excel values.

    demand_no remains the legacy unique storage key. A nullable override keeps
    existing numbered demands compatible without inventing a media number.
    """
    value = demand.get('external_demand_no')
    return demand['demand_no'] if value is None else value

class Fault(Exception):
 def __init__(self,code,message,status=422):self.code=code;self.message=message;self.status=status
LEAD_COLUMNS = (
 ('lead_no','编号'), ('customer','姓名'), ('phone','电话'), ('brand','品牌'), ('model','车型'), ('city','城市'),
 ('supplier','线索供应商'), ('acquisition_channel','获客渠道'), ('is_sold','是否售卖'), ('sold_media','售卖媒体'), ('project','项目'),
 ('sales_count','已匹配媒体'), ('sold_at','售卖时间'), ('collected_at','采集时间'),
)

COMPOUND_SURNAMES = {'欧阳','司马','上官','诸葛','司徒','司空','东方','皇甫','尉迟','公孙','慕容',
                     '令狐','长孙','宇文','夏侯','独孤','南宫','百里','东郭','西门','闻人','端木',
                     '轩辕','钟离','澹台','公羊','濮阳','公冶','太史','呼延','赫连','拓跋','单于'}

def customer_label(name, title):
 """展示姓氏加称谓；原始姓名只有先生/女士时保留原值，不构造姓氏。"""
 name = name.strip()
 if not name:
  return ''
 # 称谓本身是合法原始姓名，不能剥离后变成空值。
 if name in ('先生','女士'):
  return name
 for suffix in ('先生','女士'):
  if name.endswith(suffix):
   title = title or suffix
   name = name[:-len(suffix)].strip()
   break
 surname = name[:2] if name[:2] in COMPOUND_SURNAMES else name[:1]
 return surname + title if surname else ''

PERMS={
 'admin':{'distribution_rules','read','write','demand','approve','dispatch','settings','engineering','finance','export','consent','privacy'},
 'operator':{'read','write','dispatch','export'},
 'business':{'read','demand','approve','finance'},
 'compliance':{'read','consent','privacy','export_approve','reveal'},
 'engineer':{'read','engineering'},
 'finance':{'read','finance','export','export_approve'},
 'analyst':{'read'}}
from distribution import DistributionMixin
from synced_demands import SyncedDemandsMixin
class Platform(DistributionMixin,SyncedDemandsMixin,ZhemanhuiMixin):
 def __init__(self,root):
  self.yiche_live=None
  self.mock_enabled=True
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.dbpath=self.root/'platform.sqlite';self.vault=Vault(self.root)
  c=self.connect()
  try:c.executescript((Path(__file__).parent/'migrations/001_initial.sql').read_text())
  finally:c.close()
  self._migrate()
 def _migrate(self):
  with self.tx() as c:
   migrations = ((2, '002_yiche.sql'), (3, '003_media_vehicles.sql'),
                 (4, '004_media_vehicle_schedule.sql'), (5, '005_yiche_submissions.sql'),
                 (6, '006_lead_fields.sql'), (7, '007_lead_number_permission.sql'),
                 (8, '008_yiche_code_management.sql'), (9, '009_cooperation_terms.sql'), (10, '010_source_api_details.sql'), (11, '011_lead_sales.sql'), (12, '012_manual_models_gender.sql'), (13, '013_import_acquisition_channel.sql'), (14, '014_delivery_project.sql'), (15, '015_distribution_rules.sql'), (16, '016_demand_sync.sql'), (17, '017_synced_demands.sql'), (18, '018_partner_project.sql'), (19, '019_source_partner.sql'), (20, '020_partner_projects.sql'), (21, '021_agency_final_media.sql'), (22, '022_explicit_projects.sql'), (23, '023_sync_history.sql'), (24, '024_zhemanhui.sql'), (25, '025_demand_excel.sql'), (26, '026_optional_excel_demand_no.sql'), (27, '027_replace_demand_source.sql'), (28, '028_partner_project_enabled.sql'), (29, '029_ka_demand_api.sql'), (30, '030_demand_source_deletion.sql'), (31, '031_route_send_controls.sql'), (32, '032_media_city_catalogs.sql'))
   for version, filename in migrations:
    if c.execute('SELECT 1 FROM schema_version WHERE version=?', (version,)).fetchone():
     continue
    for sql in (Path(__file__).parent / 'migrations' / filename).read_text().split(';'):
     if sql.strip():
      c.execute(sql)
 def connect(self):
  c=sqlite3.connect(self.dbpath,timeout=30,isolation_level=None);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL');return c
 @contextmanager
 def tx(self):
  c=self.connect()
  try:c.execute('BEGIN IMMEDIATE');yield c;c.commit()
  except: c.rollback();raise
  finally:c.close()
 def one(self,c,table,id,a):
  r=c.execute('SELECT * FROM '+table+' WHERE id=? AND org_id=?',(id,a['org_id'])).fetchone()
  if not r:raise Fault('NOT_FOUND','记录不存在或无权访问',404)
  return dict(r)
 def audit(self,c,a,action,target,detail=''):
  c.execute('INSERT INTO audit_log VALUES(?,?,?,?,?,?,?)',(uid('au_'),a['org_id'],a['id'],action,str(target),canonical(detail),now()))
 def require(self,a,perm):
  if perm not in PERMS.get(a['role'],set()):
   with self.tx() as c:self.audit(c,a,'ACCESS_DENIED',perm)
   raise Fault('FORBIDDEN','当前账号没有此操作权限',403)
 def seed(self):
  with self.tx() as c:
   if c.execute('SELECT 1 FROM users').fetchone():return
   for org in ['demo','other']:c.execute('INSERT INTO settings(org_id) VALUES(?)',(org,))
   for role in PERMS:
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,1)',('u_'+role,'demo',role,password_hash('Demo2026!'),role))
   c.execute('INSERT INTO users VALUES(?,?,?,?,?,1)',('u_other','other','other',password_hash('Demo2026!'),'admin'))
   for id,name,role in [('MEDIA_A','易车 · 模拟媒体','MEDIA'),('MEDIA_B','汽车之家 · 模拟媒体','MEDIA'),('MEDIA_C','懂车帝 · 模拟媒体','MEDIA'),('AGENCY_A','星桥代运营 · 模拟乙方','AGENCY'),('SOURCE_A','自营投放中心','SOURCE'),('SOURCE_B','合规采购供应商','SOURCE'),('SOURCE_C','TMK意向采集组','SOURCE')]:
    c.execute('INSERT INTO partners(id,org_id,name,role,enabled,contract_end,cooperation_project) VALUES(?,?,?,?,1,?,?)',(id,'demo',name,role,later(days=365),'MEMBER' if role in ('MEDIA','AGENCY') else None))
   for partner in c.execute("SELECT id,org_id FROM partners WHERE role IN ('MEDIA','AGENCY')").fetchall():self._ensure_partner_projects(c,partner['org_id'],partner['id'])
   for id,name,ty,src in [('ADS','信息流投放','ADS','SOURCE_A'),('PURCHASE','采购导入','PURCHASE','SOURCE_B'),('TMK','TMK人工录入','TMK','SOURCE_C')]:c.execute('INSERT INTO channels(id,org_id,name,type,source_partner_id,enabled) VALUES(?,?,?,?,?,1)',(id,'demo',name,ty,src))
   for row in [('BMW_X5','BMW','宝马','宝马 X5'),('BMW_3','BMW','宝马','宝马 3系'),('AUDI_A6','AUDI','奥迪','奥迪 A6L')]:c.execute('INSERT INTO catalog VALUES(?,?,?,?)',row)
   for id,b,r,m in [('DIRECT_A','MEDIA_A','MEDIA_A','MEDIA_A'),('DIRECT_B','MEDIA_B','MEDIA_B','MEDIA_B'),('DIRECT_C','MEDIA_C','MEDIA_C','MEDIA_C'),('AGENCY_TO_A','AGENCY_A','AGENCY_A','MEDIA_A'),('AGENCY_TO_B','AGENCY_A','AGENCY_A','MEDIA_B')]:c.execute('INSERT INTO routes(id,org_id,buyer_id,receiver_id,media_id) VALUES(?,?,?,?,?)',(id,'demo',b,r,m))
   c.execute("INSERT OR IGNORE INTO agency_final_media(org_id,agency_id,media_id) SELECT r.org_id,r.buyer_id,r.media_id FROM routes r JOIN partners p ON p.id=r.buyer_id AND p.org_id=r.org_id WHERE p.role='AGENCY'")
  admin={'id':'u_admin','org_id':'demo','role':'admin'};compliance={'id':'u_compliance','org_id':'demo','role':'compliance'}
  for i in range(1,14):
   row={'source_record_id':f'TEST-{i:03}','phone':f'1380000{i:04}','name':f'测试客户{i:02}','series_id':'BMW_X5','city_code':'310100','collected_at':later(hours=-i),'recipients':['MEDIA_A','MEDIA_B','MEDIA_C','AGENCY_A'],'evidence_ref':f'TEST://notice/{i}','cost_fen':1800}
   if i==6:row['recipients']=['MEDIA_A']
   if i==8:row['collected_at']=later(days=-10)
   if i==9:row['series_id']='UNMAPPED'
   if i==10:row['city_code']='110100';row['series_id']='AUDI_A6'
   if i==11:row['recipients']=[]
   res=self.ingest(admin,'ADS' if i<=9 else 'TMK',row)
   if row['recipients']:self.review_consent(compliance,res['lead_id'])
   if i==7:self.privacy(compliance,{'lead_id':res['lead_id'],'type':'REVOKE','reason':'测试撤回'})
  for n,route,total in [('000123','DIRECT_A',5),('AUTOHOME-0098','DIRECT_B',5),('AG-TO-YC-001','AGENCY_TO_A',3),('AG-TO-AH-002','AGENCY_TO_B',3)]:
   d=self.create_demand(admin,{'demand_no':n,'route_id':route,'series_id':'BMW_X5','city_codes':['310100'],'target_total':total,'daily_limit':total,'priority':80 if route.startswith('DIRECT') else 50,'unit_price_fen':4500,'status':'DRAFT','yiche':{'platFormId':900001,'plat':'Web','cityId':900002,'serialId':900003,'manufacturerId':900004,'clueTypeId':1} if route in ('DIRECT_A','AGENCY_TO_A') else {}})
   self.demand_action(admin,d['id'],'submit',1)
   self.demand_action({'id':'u_business','org_id':'demo','role':'business'},d['id'],'approve',2)
  # A separate historical record, leaving L001-L005 available for the 5x2 acceptance case.
  c=self.connect();l=dict(c.execute("SELECT * FROM leads WHERE lead_no='L000012'").fetchone());d=dict(c.execute("SELECT * FROM demands WHERE demand_no='000123'").fetchone());c.close()
  dr=self.dispatch(admin,[(l['id'],d['id'])],'seed-history');self.worker(admin)
  if dr['items'] and dr['items'][0].get('delivery_id'):
   did=dr['items'][0]['delivery_id'];self.mock_callback(admin,did,'STORED');self.mock_callback(admin,did,'VALID')
   # Increase target so the example demand still has five available positions.
   self.edit_demand(admin,d['id'],{'target_total':6,'daily_limit':6,'version':3})
 def catalogs(self,a):
  c=self.connect()
  try:
   out={t:[dict(x) for x in c.execute('SELECT * FROM '+t+" WHERE org_id=?"+(" AND id NOT LIKE 'INTAKE:%'" if t=='channels' else ''),(a['org_id'],))] for t in ['partners','channels','routes']}
   from route_sending import status as route_send_status
   for route in out['routes']:route['sending']=route_send_status(self,c,a,route)
   from api_connection_status import connection_statuses,public_status
   inbound,outbound=connection_statuses(c,a['org_id'])
   for channel in out['channels']:channel.update(public_status(inbound.get(channel['id'])))
   for p in out['partners']:
    p['cooperation_projects']=[{**dict(r),**public_status(outbound.get((p['id'],r['project'])))} for r in c.execute('SELECT project,matching_mode,version,enabled FROM partner_projects WHERE org_id=? AND partner_id=? ORDER BY project DESC',(a['org_id'],p['id']))]
   for p in out['partners']:
    p['final_media_ids']=[r['media_id'] for r in c.execute('SELECT media_id FROM agency_final_media WHERE org_id=? AND agency_id=? ORDER BY media_id',(a['org_id'],p['id']))] if p['role']=='AGENCY' else ([p['id']] if p['role']=='MEDIA' else [])
   out['vehicles']=[dict(x) for x in c.execute('SELECT * FROM catalog WHERE series_id NOT IN (SELECT series_id FROM manual_vehicle_entries WHERE org_id!=?)',(a['org_id'],))];out['cities']=[{'code':'310100','name':'上海市'},{'code':'110100','name':'北京市'}];out['settings']=dict(c.execute('SELECT * FROM settings WHERE org_id=?',(a['org_id'],)).fetchone());return out
  finally:c.close()
 def attach_agency_media(self,c,a,agency_id,data):
  agency=self.one(c,'partners',agency_id,a)
  media_id=data.get('final_media_id');raw=data.get('final_media','')
  if not isinstance(raw,str) or len(raw.strip())>100:raise Fault('FINAL_MEDIA_INVALID','最终媒体名称最多100字')
  name=raw.strip()
  if media_id:
   media=self.one(c,'partners',media_id,a)
   if media['role']!='MEDIA' or not media['enabled']:raise Fault('FINAL_MEDIA_INVALID','最终媒体请选择可用的媒体主体')
  else:
   if not name:raise Fault('FINAL_MEDIA_REQUIRED','合作角色为乙方时，必须填写最终媒体')
   if name=='自有媒体':name=agency['name']+' · 自有媒体'
   aliases={'易车':'MEDIA_A','汽车之家':'MEDIA_B','懂车帝':'MEDIA_C'}
   existing=c.execute("SELECT * FROM partners WHERE org_id=? AND role='MEDIA' AND (name=? COLLATE NOCASE OR id=?) ORDER BY CASE WHEN name=? COLLATE NOCASE THEN 0 ELSE 1 END LIMIT 1",(a['org_id'],name,aliases.get(name,''),name)).fetchone()
   if existing:
    media=dict(existing)
    if not media['enabled']:raise Fault('FINAL_MEDIA_INVALID','该最终媒体已停用，请先启用')
   else:
    media={'id':uid('media_')}
    c.execute("INSERT INTO partners(id,org_id,name,role,enabled,contract_end,cooperation_project) VALUES(?,?,?,'MEDIA',1,?,?)",(media['id'],a['org_id'],name,agency['contract_end'],agency['cooperation_project']))
    self._ensure_partner_projects(c,a['org_id'],media['id'],(agency['cooperation_project'],));self.audit(c,a,'MEDIA_CREATED_FROM_AGENCY',media['id'],{'name':name,'agency_id':agency_id})
  c.execute('INSERT OR IGNORE INTO agency_final_media(org_id,agency_id,media_id) VALUES(?,?,?)',(a['org_id'],agency_id,media['id']))
  if not c.execute('SELECT 1 FROM routes WHERE org_id=? AND buyer_id=? AND receiver_id=? AND media_id=?',(a['org_id'],agency_id,agency_id,media['id'])).fetchone():
   c.execute('INSERT INTO routes(id,org_id,buyer_id,receiver_id,media_id) VALUES(?,?,?,?,?)',(uid('route_'),a['org_id'],agency_id,agency_id,media['id']))
  self.audit(c,a,'AGENCY_FINAL_MEDIA',agency_id,{'media_id':media['id']})
  return media['id']

 def issue_source_key(self,a,channel_id):
  self.require(a,'settings')
  with self.tx() as c:
   channel=self.one(c,'channels',channel_id,a)
   if not channel['enabled']:raise Fault('SOURCE_BLOCKED','供应商已停用')
   key_id=uid('source_')
   secret=secrets.token_urlsafe(32)
   c.execute('UPDATE source_api_keys SET enabled=0 WHERE org_id=? AND channel_id=?',(a['org_id'],channel_id))
   c.execute('INSERT INTO source_api_keys VALUES(?,?,?,?,1,?)',
             (key_id,a['org_id'],channel_id,self.vault.encrypt(secret),now()))
   self.audit(c,a,'SOURCE_API_KEY_ISSUED',channel_id,{'key_id':key_id})
  return {'key_id':key_id,'secret':secret,'channel_id':channel_id,
          'supplier_id':channel['source_partner_id'],'endpoint':'/open/v1/leads'}

 def source_api_key(self,key_id):
  c=self.connect()
  try:
   row=c.execute('SELECT * FROM source_api_keys WHERE id=? AND enabled=1',(key_id,)).fetchone()
   if not row:return None
   return {'org_id':row['org_id'],'channel_id':row['channel_id'],
           'secret':self.vault.decrypt(row['secret_cipher'])}
  finally:c.close()

 def source_ingest(self,a,channel_id,row):
  self.require(a,'write')
  if not isinstance(row,dict):raise Fault('SCHEMA_INVALID','每条线索须为对象')
  aliases={'编号':'source_record_id','需求编号':'demand_no','渠道名称':'channel_name',
           '车系名称':'series_name','品牌名称':'brand_name','城市':'city','手机号':'phone',
           '客户姓名':'name','上报状态':'report_status','回调状态':'callback_status',
           '线索状态':'lead_code','上报时间':'reported_at','回调时间':'callback_at'}
  row=row.copy()
  for label,key in aliases.items():
   if label in row:
    if key in row and str(row[key])!=str(row[label]):
     raise Fault('SCHEMA_INVALID','同一字段内容不一致')
    row[key]=row.pop(label)
  if not str(row.get('source_record_id','')).strip():
   raise Fault('REQUIRED','来源编号不能为空')
  details={}
  for key in ('source_record_id','demand_no','channel_name','series_name','brand_name','city',
              'phone','name','report_status','callback_status','lead_code','reported_at','callback_at'):
   value=row.get(key)
   if value is not None and not isinstance(value,(str,int)):
    raise Fault('SCHEMA_INVALID',key+'须为文本或整数')
   details[key]='' if value is None else str(value).strip()
   if len(details[key])>200:raise Fault('SCHEMA_INVALID',key+'最多200字')
  for key,allowed,default in (
      ('callback_status',{'成功':'SUCCESS','失败':'FAILED','未回调':'NONE',
                          'SUCCESS':'SUCCESS','FAILED':'FAILED','NONE':'NONE'},'NONE'),
      ('report_status',{'上报成功':'SUCCESS','上报失败':'FAILED','未上报':'PENDING',
                        'SUCCESS':'SUCCESS','FAILED':'FAILED','PENDING':'PENDING','UNKNOWN':'UNKNOWN'},'UNKNOWN')):
   raw=details[key]
   if raw and raw not in allowed:raise Fault('SCHEMA_INVALID',key+'不在允许范围内')
   details[key]=allowed.get(raw,default)
  if details['lead_code'] and not re.fullmatch(r'-?[0-9]{1,12}',details['lead_code']):
   raise Fault('SCHEMA_INVALID','线索状态请传数字 Code 码')
  for key in ('reported_at','callback_at'):
   raw=details[key]
   if raw in ('','N/A','null'):
    details[key]=''
    continue
   try:
    stamp=datetime.fromisoformat(raw.replace('Z','+00:00'))
    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=SH)
    details[key]=stamp.astimezone(UTC).isoformat(timespec='seconds')
   except ValueError:raise Fault('TIME_INVALID',key+'时间格式错误')
  payload_hash=digest(canonical(row))
  with self.tx() as c:
   self.one(c,'channels',channel_id,a)
   codes=self._yiche_code_map(c,a['org_id'])
   code=details['lead_code']
   details['lead_status']=codes.get(code,('', '未收录码'))[1] if code else '未判定'
   if not row.get('series_id') and details['series_name']:
    matches=c.execute('SELECT series_id FROM catalog WHERE series_name=? AND brand_name=?',
                      (details['series_name'],details['brand_name'])).fetchall()
    if len(matches)==1:row['series_id']=matches[0]['series_id']
   if not row.get('city_code'):
    row['city_code']={'上海':'310100','上海市':'310100','北京':'110100','北京市':'110100'}.get(details['city'],'')
   if 'is_sold' in row:row=self._entry_fields(c,a,row)
   result=self._ingest(c,a,channel_id,row,payload_hash,direct=True)
   # 仅由有效签名密钥身份记录成功接收，手动调用及测试公共密钥不算接通。
   if a['id'].startswith('open:source_') and c.execute('SELECT 1 FROM source_api_keys WHERE org_id=? AND channel_id=? AND enabled=1 AND id=?',(a['org_id'],channel_id,a['id'][5:])).fetchone():
    self.audit(c,a,'SOURCE_API_RECEIVED',channel_id,{'accepted':True})
   ingestion=c.execute('SELECT id FROM ingestions WHERE org_id=? AND channel_id=? AND source_record_id=?',
                       (a['org_id'],channel_id,str(row['source_record_id']).strip())).fetchone()
   lead=self.one(c,'leads',result['lead_id'],a)
   if lead['status']!='DELETED':
    c.execute('INSERT OR IGNORE INTO source_lead_details VALUES(?,?,?,?)',
              (ingestion['id'],result['lead_id'],self.vault.encrypt(canonical(details)),now()))
   return result

 def _source_details(self,c,a,lead_id,fields):
  output=[]
  for record in c.execute("""SELECT s.*,i.channel_id FROM source_lead_details s
                             JOIN ingestions i ON i.id=s.ingestion_id
                             WHERE s.lead_id=? ORDER BY s.created_at,s.rowid""",(lead_id,)):
   details=json.loads(self.vault.decrypt(record['details_cipher']))
   permissions={
    'lead_no':('source_record_id','demand_no'),
    'channel':('channel_name',),
    'model':('series_name',),'brand':('brand_name',),'city':('city',),
    'source_status':('report_status','callback_status','lead_code','lead_status'),
    'collected_at':('reported_at','callback_at'),
   }
   for permission,keys in permissions.items():
    if permission not in fields:
     for key in keys:details.pop(key,None)
   phone=details.pop('phone','')
   name=details.pop('name','')
   if 'customer' in fields:
    details['name']=customer_label(name,'')
   if 'phone' in fields:
    details['phone']=phone[:6]+'****'+phone[-4:] if phone else '已删除'
   if 'channel' in fields:details['channel_id']=record['channel_id']
   output.append(details)
  return output

 def _intake_channel(self,c,a):
  channel_id='INTAKE:'+a['org_id']
  source_id='INTAKE_SOURCE:'+a['org_id']
  c.execute("""INSERT OR IGNORE INTO partners(id,org_id,name,role,contract_end)
               VALUES(?,?,?,'SOURCE',?)""",(source_id,a['org_id'],'平台录入',later(days=36500)))
  c.execute("""INSERT OR IGNORE INTO channels(id,org_id,name,type,source_partner_id)
               VALUES(?,?,?,'MANUAL',?)""",(channel_id,a['org_id'],'平台录入与导入',source_id))
  return channel_id

 def _supplier_intake_channel(self,c,a,supplier_id):
  """必选本组织有效供应商，优先复用其已启用采购渠道，不改历史线索归属。"""
  if not isinstance(supplier_id,str) or not supplier_id.strip():raise Fault('SUPPLIER_REQUIRED','请选择线索供应商')
  supplier=self.one(c,'partners',supplier_id,a)
  if supplier['role']!='SOURCE' or supplier_id.startswith('INTAKE_SOURCE:'):raise Fault('SUPPLIER_INVALID','请选择合作与渠道中线索采购的供应商')
  if not supplier['enabled'] or supplier['contract_end']<=now():raise Fault('SUPPLIER_UNAVAILABLE','该供应商已停用或合同已到期')
  channels=c.execute("SELECT id,enabled FROM channels WHERE org_id=? AND source_partner_id=? ORDER BY CASE type WHEN 'PURCHASE' THEN 0 ELSE 1 END,id",(a['org_id'],supplier_id)).fetchall()
  if channels:
   available=next((row for row in channels if row['enabled']),None)
   if not available:raise Fault('SOURCE_BLOCKED','该供应商的来源渠道已停用')
   return available['id']
  channel_id='SUPPLIER_INTAKE:'+digest(canonical([a['org_id'],supplier_id]))[:24]
  c.execute("INSERT OR IGNORE INTO channels(id,org_id,name,type,source_partner_id) VALUES(?,?,?,'MANUAL',?)",(channel_id,a['org_id'],'手动/表格录入 · '+supplier['name'],supplier_id))
  channel=self.one(c,'channels',channel_id,a)
  if not channel['enabled']:raise Fault('SOURCE_BLOCKED','该供应商的录入渠道已停用')
  return channel_id

 def lead_entry_options(self,a):
  self.require(a,'read')
  c=self.connect()
  try:
   models={row['series_id']:dict(row) for row in c.execute(
       'SELECT series_id,brand_name,series_name FROM catalog WHERE series_id NOT IN (SELECT series_id FROM manual_vehicle_entries WHERE org_id!=?) ORDER BY brand_name,series_name',(a['org_id'],))}
   existing={(row['brand_name'],row['series_name']) for row in models.values()}
   for row in c.execute("""SELECT DISTINCT media_id,series_id,brand_id,brand_name,series_name
                           FROM media_vehicle_items WHERE org_id=?
                           ORDER BY brand_name,series_name,media_id""",(a['org_id'],)):
    pair=(row['brand_name'],row['series_name'])
    if pair in existing:continue
    key=f"media:{row['media_id']}:{row['brand_id']}:{row['series_id']}"
    models[key]={'series_id':key,'brand_name':row['brand_name'],'series_name':row['series_name']}
    existing.add(pair)
   suppliers=[dict(row) for row in c.execute("SELECT id,name FROM partners WHERE org_id=? AND role='SOURCE' AND enabled=1 AND contract_end>? AND id NOT LIKE 'INTAKE_SOURCE:%' ORDER BY name,id",(a['org_id'],now()))]
   return {'models':list(models.values()),'suppliers':suppliers}
  finally:c.close()

 def _entry_fields(self,c,a,data,register=True):
  row=data.copy()
  phone=str(row.get('phone') or '').strip()
  if not re.fullmatch(r'1[3-9][0-9]{9}',phone):raise Fault('PHONE_INVALID','电话须为有效的11位手机号')
  row['phone']=phone
  if 'acquisition_channel' in row:
   channel=row['acquisition_channel']
   if not isinstance(channel,str) or len(channel.strip())>100:
    raise Fault('ACQUISITION_CHANNEL_INVALID','获客渠道最多100字')
   row['acquisition_channel']=channel.strip()
  name=str(row.get('name') or '').strip()
  brand=str(row.get('brand_name') or row.get('brand') or '').strip()
  model=str(row.get('model') or row.get('series_name') or row.get('series_id') or '').strip()
  city=str(row.get('city') or row.get('city_code') or '').strip()
  if not name or len(name)>50:raise Fault('NAME_REQUIRED','姓名必填且最多50字')
  if not brand or len(brand)>100:raise Fault('BRAND_REQUIRED','品牌必填且最多100字')
  if not model or len(model)>150:raise Fault('MODEL_REQUIRED','车型必填且最多150字')
  if not city or len(city)>100:raise Fault('CITY_REQUIRED','城市必填且最多100字')
  vehicle=c.execute('SELECT * FROM catalog WHERE (series_id=? OR series_name=?) AND brand_name=? AND series_id NOT IN (SELECT series_id FROM manual_vehicle_entries WHERE org_id!=?)',
                    (model,model,brand,a['org_id'])).fetchall()
  if len(vehicle)==1:
   row['series_id']=vehicle[0]['series_id']
   row['model']=vehicle[0]['series_name']
  else:
   matches=c.execute("""SELECT DISTINCT media_id,brand_id,brand_name,series_id,series_name
                        FROM media_vehicle_items WHERE org_id=? AND brand_name=?
                        AND (series_name=? OR 'media:'||media_id||':'||brand_id||':'||series_id=?)""",
                     (a['org_id'],brand,model,model)).fetchall()
   identities={(r['brand_name'],r['series_name']) for r in matches}
   if matches and len(identities)==1:
    vehicle=matches[0]
    series_id=f"media:{vehicle['media_id']}:{vehicle['brand_id']}:{vehicle['series_id']}"
    row['model']=vehicle['series_name']
    if register:
     c.execute('INSERT OR IGNORE INTO catalog VALUES(?,?,?,?)',
               (series_id,str(vehicle['brand_id']),brand,vehicle['series_name']))
   else:
    if c.execute('SELECT 1 FROM catalog WHERE series_id=?',(model,)).fetchone():
     raise Fault('MODEL_INVALID','车型编号与所填品牌不匹配')
    series_id='manual:'+digest(canonical([a['org_id'],brand,model]))[:24]
    existing_brand=c.execute('SELECT brand_id FROM catalog WHERE brand_name=? AND series_id NOT IN (SELECT series_id FROM manual_vehicle_entries WHERE org_id!=?) LIMIT 1',(brand,a['org_id'])).fetchone()
    if not existing_brand:
     existing_brand=c.execute('SELECT brand_id FROM media_vehicle_items WHERE org_id=? AND brand_name=? LIMIT 1',(a['org_id'],brand)).fetchone()
    brand_id=existing_brand['brand_id'] if existing_brand else 'manual_brand:'+digest(canonical([a['org_id'],brand]))[:20]
    row['model']=model
    if register:
     c.execute('INSERT OR IGNORE INTO catalog VALUES(?,?,?,?)',(series_id,brand_id,brand,model))
     c.execute('INSERT OR IGNORE INTO manual_vehicle_entries VALUES(?,?,?)',
               (a['org_id'],series_id,int(model=='全系')))
   row['series_id']=series_id
  row['name']=name
  row['brand_name']=brand
  row['city_code']={'上海':'310100','上海市':'310100','北京':'110100','北京市':'110100'}.get(city,city)
  sold=row.get('is_sold')
  if sold in (True,1,'1','是','yes','YES','true','TRUE'):
   row['is_sold']=True
  elif sold in (False,0,'0','否','no','NO','false','FALSE'):
   row['is_sold']=False
  else:
   raise Fault('SALE_REQUIRED','是否售卖必填，请选择是或否')
  if not row['is_sold']:
   row.update(sale_media_id='',sale_project='',sold_at='')
   return row
  media=str(row.get('sale_media_id') or row.get('sale_media') or '').strip()
  matches=c.execute("SELECT id FROM partners WHERE org_id=? AND role='MEDIA' AND (id=? OR name=?)",
                    (a['org_id'],media,media)).fetchall()
  if len(matches)!=1:raise Fault('SALE_MEDIA_REQUIRED','已售卖时必须选择已登记的售卖媒体')
  project=str(row.get('sale_project') or row.get('project') or '').strip().upper()
  if project not in ('KA','会员','MEMBER'):
   raise Fault('SALE_PROJECT_REQUIRED','已售卖时项目必填，只能是KA或会员')
  try:
   sold_at=datetime.fromisoformat(str(row.get('sold_at') or '').replace('Z','+00:00'))
   if sold_at.tzinfo is None:sold_at=sold_at.replace(tzinfo=SH)
   sold_at=sold_at.astimezone(UTC)
   if sold_at>parse(now())+timedelta(minutes=5):raise ValueError()
  except (ValueError,TypeError):
   raise Fault('SALE_TIME_REQUIRED','已售卖时售卖时间必填，且不能晚于当前时间')
  row.update(sale_media_id=matches[0]['id'],sale_project='KA' if project=='KA' else 'MEMBER',
             sold_at=sold_at.isoformat(timespec='seconds'))
  return row

 def create_lead(self,a,data):
  self.require(a,'write')
  if not isinstance(data,dict):raise Fault('SCHEMA_INVALID','线索须为对象')
  with self.tx() as c:
   channel=self._supplier_intake_channel(c,a,data.get('supplier_id'))
   row=self._entry_fields(c,a,data)
   row.setdefault('source_record_id',uid('manual_'))
   return self._ingest(c,a,channel,row,direct=True)

 def _sale_history(self,c,lead_id):
  return [dict(row) for row in c.execute("""SELECT s.media_id,p.name AS media_name,s.project,s.sold_at
                FROM lead_sale_history s JOIN partners p ON p.id=s.media_id
                WHERE s.lead_id=? ORDER BY s.sold_at,s.id""",(lead_id,))]

 def _sale_records(self,c,lead_id):
  records=self._sale_history(c,lead_id)
  records.extend(dict(row) for row in c.execute('''SELECT d.media_id,p.name AS media_name,
          d.sale_project AS project,d.first_sent_at AS sold_at
          FROM deliveries d JOIN partners p ON p.id=d.media_id
          WHERE d.lead_id=? AND d.first_sent_at IS NOT NULL
          AND d.send_status NOT IN ('CANCELED','FAILED')''',(lead_id,)))
  return records

 def _matched_media(self,c,lead_id,skip_delivery=None):
  media={row['media_id'] for row in c.execute("""SELECT media_id FROM deliveries
        WHERE lead_id=? AND send_status NOT IN ('CANCELED','FAILED') AND id!=?""",
        (lead_id,skip_delivery or ''))}
  media.update(row['media_id'] for row in c.execute('SELECT media_id FROM lead_sale_history WHERE lead_id=?',(lead_id,)))
  return media

 def ingest(self,a,channel,row):
  self.require(a,'write')
  with self.tx() as c:return self._ingest(c,a,channel,row)
 def _ingest(self,c,a,channel,row,payload_hash=None,direct=False):
  try:extra=yiche.lead_fields(row.get('yiche',{}))
  except ValueError as e:raise Fault('YICHE_FIELDS_INVALID',str(e)) from None
  acquisition_channel=row.get('acquisition_channel','')
  if not isinstance(acquisition_channel,str) or len(acquisition_channel.strip())>100:
   raise Fault('ACQUISITION_CHANNEL_INVALID','获客渠道最多100字')
  ch=self.one(c,'channels',channel,a)
  if not ch['enabled']:raise Fault('SOURCE_BLOCKED','来源渠道已停用')
  sp=self.one(c,'partners',ch['source_partner_id'],a)
  if not sp['enabled'] or sp['contract_end']<now():raise Fault('SOURCE_BLOCKED','来源合同已失效')
  title=row.get('customer_title') or ''
  gender=row.get('gender')
  if gender is not None:
   values={'男':'MALE','女':'FEMALE','未登记':'UNKNOWN','':'UNKNOWN','MALE':'MALE','FEMALE':'FEMALE','UNKNOWN':'UNKNOWN'}
   if not isinstance(gender,str) or gender not in values:
    raise Fault('GENDER_INVALID','性别只能是男、女或未登记')
   gender=values[gender]
   title={'MALE':'先生','FEMALE':'女士','UNKNOWN':''}[gender]
  else:
   gender={'先生':'MALE','女士':'FEMALE'}.get(title,'UNKNOWN')
  if title not in ('','先生','女士'):raise Fault('CUSTOMER_TITLE_INVALID','称谓只能是先生、女士或留空')
  sr=str(row.get('source_record_id','')).strip()
  if not sr:raise Fault('REQUIRED','来源记录编号不能为空')
  h=payload_hash or digest(canonical(row));old=c.execute('SELECT * FROM ingestions WHERE org_id=? AND channel_id=? AND source_record_id=?',(a['org_id'],channel,sr)).fetchone()
  if old:
   accepted_hashes={h}
   if direct and payload_hash is None and 'model' in row:
    legacy=row.copy()
    legacy.pop('model')
    accepted_hashes.add(digest(canonical(legacy)))
   if old['payload_hash'] not in accepted_hashes:raise Fault('IDEMPOTENCY_CONFLICT','同来源记录编号内容不一致',409)
   return {'lead_id':old['lead_id'],'status':old['status'],'duplicate':True}
  phone=str(row.get('phone','')).replace(' ','').replace('-','');phone=phone[3:] if phone.startswith('+86') else phone
  if not re.fullmatch(r'1[3-9][0-9]{9}',phone):raise Fault('PHONE_INVALID','手机号格式错误')
  phone='+86'+phone;fp=self.vault.fingerprint(a['org_id'],phone)
  try:col=parse(row.get('collected_at') or now())
  except:raise Fault('TIME_INVALID','采集时间格式错误')
  if col>parse(now())+timedelta(minutes=5):raise Fault('TIME_INVALID','采集时间晚于当前时间')
  series=row.get('series_id','');city=str(row.get('city_code',''));reason=''
  if c.execute('SELECT 1 FROM manual_vehicle_entries WHERE series_id=? AND org_id!=?',(series,a['org_id'])).fetchone():
   raise Fault('MODEL_UNMAPPED','车型不存在或不可使用')
  if not c.execute('SELECT 1 FROM catalog WHERE series_id=?',(series,)).fetchone():reason='MODEL_UNMAPPED'
  if city not in ('310100','110100'):reason='CITY_UNMAPPED'
  recipients=row.get('recipients',[])
  if not isinstance(recipients,list):raise Fault('CONSENT_INVALID','接收方必须为列表')
  for r in recipients:self.one(c,'partners',r,a)
  old=c.execute('SELECT * FROM leads WHERE org_id=? AND phone_hmac=? AND series_id=? AND collected_at>=? AND status!=? ORDER BY collected_at LIMIT 1',(a['org_id'],fp,series,(col-timedelta(days=c.execute('SELECT dedup_window_days FROM settings WHERE org_id=?',(a['org_id'],)).fetchone()[0])).isoformat(timespec='seconds'),'DELETED')).fetchone()
  if old:lid=old['id'];status='DUPLICATE'
  else:
   lid=uid('ld_');n=c.execute('SELECT count(*) FROM leads WHERE org_id=?',(a['org_id'],)).fetchone()[0]+1
   st='READY' if direct else 'QUARANTINED';exp=(col+timedelta(days=7)).isoformat(timespec='seconds')
   if not direct and exp<now():st='EXPIRED'
   cost=row.get('cost_fen');cost=int(cost) if cost is not None and cost!='' else None
   if cost is not None and cost<0:raise Fault('COST_INVALID','成本不可为负数')
   c.execute('INSERT INTO leads(id,org_id,lead_no,phone_cipher,phone_hmac,name_cipher,series_id,city_code,channel_id,collected_at,expires_at,status,reason,cost_fen,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(lid,a['org_id'],f'L{n:06}',self.vault.encrypt(phone),fp,self.vault.encrypt(str(row.get('name') or '').strip()[:50]),series,city,channel,col.isoformat(timespec='seconds'),exp,st,reason or ('CONSENT_REVIEW_REQUIRED' if recipients else 'CONSENT_MISSING'),cost,now()))
   if direct:c.execute('UPDATE leads SET direct_admission=1,reason=? WHERE id=?',('',lid))
   c.execute('UPDATE leads SET customer_title=?,gender=? WHERE id=?',(title,gender,lid))
   c.execute('UPDATE leads SET acquisition_channel=? WHERE id=?',(acquisition_channel.strip(),lid))
   if extra:c.execute('UPDATE leads SET yiche_cipher=? WHERE id=?',(self.vault.encrypt(canonical(extra)),lid))
   status='CREATED'
  if direct:
   current=self.one(c,'leads',lid,a)
   if current['status'] not in ('BLOCKED','DELETED'):
    c.execute("UPDATE leads SET direct_admission=1,status='READY',reason='' WHERE id=?",(lid,))
  for r in recipients:
   if not row.get('evidence_ref'):continue
   c.execute('INSERT OR IGNORE INTO consents VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid('co_'),a['org_id'],lid,r,str(row['evidence_ref']),'TEST-v1',now(),later(days=7),None,'PENDING',None))
  c.execute('INSERT INTO ingestions VALUES(?,?,?,?,?,?,?,?,?)',(uid('in_'),a['org_id'],channel,sr,lid,status,'',h,now()));self.audit(c,a,'LEAD_INGEST',lid,{'channel_id':channel,'status':status})
  if row.get('is_sold') is True:
   ingestion=c.execute('SELECT id FROM ingestions WHERE org_id=? AND channel_id=? AND source_record_id=?',
                       (a['org_id'],channel,sr)).fetchone()
   c.execute('INSERT INTO lead_sale_history VALUES(?,?,?,?,?,?,?)',
             (uid('sale_'),lid,ingestion['id'],row['sale_media_id'],row['sale_project'],row['sold_at'],now()))
  return {'lead_id':lid,'status':status,'duplicate':status=='DUPLICATE'}
 def review_consent(self,a,lid):
  self.require(a,'consent')
  with self.tx() as c:
   l=self.one(c,'leads',lid,a)
   if l['status'] in ['BLOCKED','DELETED']:raise Fault('LEAD_BLOCKED','线索已停用，不得恢复')
   c.execute("UPDATE consents SET review_status='APPROVED',reviewed_by=? WHERE lead_id=? AND revoked_at IS NULL",(a['id'],lid))
   ok=c.execute("SELECT 1 FROM consents WHERE lead_id=? AND review_status='APPROVED' AND expires_at>? AND revoked_at IS NULL",(lid,now())).fetchone()
   if ok and l['reason'] not in ['MODEL_UNMAPPED','CITY_UNMAPPED'] and l['expires_at']>now():c.execute("UPDATE leads SET status='READY',reason='',version=version+1 WHERE id=?",(lid,))
   self.audit(c,a,'CONSENT_REVIEW',lid)
  return {'ok':True}
 def _yiche_lead(self,l):
  return json.loads(self.vault.decrypt(l['yiche_cipher'])) if l.get('yiche_cipher') else {}
 def _yiche_demand(self,raw,complete=False):
  try:return yiche.demand_fields(raw,complete)
  except ValueError as e:raise Fault('YICHE_FIELDS_INVALID',str(e)) from None
 def edit_lead_gender(self,a,id,gender,version):
  titles={'MALE':'先生','FEMALE':'女士','UNKNOWN':''}
  if not isinstance(gender,str) or gender not in titles:
   raise Fault('GENDER_INVALID','性别只能是男、女或未登记')
  return self.edit_lead_title(a,id,titles[gender],version)

 def edit_lead_title(self,a,id,title,version):
  self.require(a,'write')
  if title not in ('','先生','女士'):raise Fault('CUSTOMER_TITLE_INVALID','称谓只能是先生、女士或留空')
  with self.tx() as c:
   lead=self.one(c,'leads',id,a)
   fields=self._lead_field_override(c,a)
   if fields is not None and 'customer' not in fields:raise Fault('FORBIDDEN','当前账号无权查看客户字段',403)
   if lead['status']=='DELETED':raise Fault('LEAD_BLOCKED','已删除线索不能修改')
   if version!=lead['version']:raise Fault('VERSION_CONFLICT','线索已变更，请刷新',409)
   gender={'先生':'MALE','女士':'FEMALE'}.get(title,'UNKNOWN')
   c.execute('UPDATE leads SET customer_title=?,gender=?,version=version+1 WHERE id=?',(title,gender,id))
   self.audit(c,a,'LEAD_TITLE_UPDATED',id)
  return {'saved':True}
 def edit_yiche_lead(self,a,lid,data):
  self.require(a,'write')
  with self.tx() as c:
   l=self.one(c,'leads',lid,a)
   if l['status'] in ('BLOCKED','DELETED'):raise Fault('LEAD_BLOCKED','已停用的线索不能补充字段')
   if data.get('version')!=l['version']:raise Fault('VERSION_CONFLICT','线索已修改，请刷新',409)
   try:extra=yiche.lead_fields(self._yiche_lead(l)|data.get('yiche',{}))
   except (ValueError,TypeError) as e:raise Fault('YICHE_FIELDS_INVALID',str(e)) from None
   c.execute('UPDATE leads SET yiche_cipher=?,version=version+1 WHERE id=?',(self.vault.encrypt(canonical(extra)),lid))
   self.audit(c,a,'LEAD_YICHE_EDIT',lid,{'fields':sorted(extra)})
   return self._public(c,a,'leads',self.one(c,'leads',lid,a))
 def create_demand(self,a,data):
  self.require(a,'demand');no=data.get('demand_no')
  if not isinstance(no,str) or not no.strip():raise Fault('MEDIA_DEMAND_NO_REQUIRED','必须填写媒体提供的需求编号，按文本保留前导零')
  with self.tx() as c:
   r=self.one(c,'routes',data.get('route_id'),a);s=data.get('series_id','BMW_X5')
   from media_catalog_matching import requires_media_catalogs,register_demand_vehicle,resolve_media_codes,yiche_fields_from_catalog
   managed=requires_media_catalogs(c,a['org_id'],r,strict=not self.mock_enabled)
   if managed:s=register_demand_vehicle(c,a['org_id'],r['media_id'],s)
   yc=self._yiche_demand(data.get('yiche',{})) if r['media_id']=='MEDIA_A' else {}
   if not c.execute('SELECT 1 FROM catalog WHERE series_id=?',(s,)).fetchone():raise Fault('MODEL_UNMAPPED','请选择标准车型')
   if c.execute('SELECT 1 FROM manual_vehicle_entries WHERE series_id=? AND org_id!=?',(s,a['org_id'])).fetchone():
    raise Fault('MODEL_UNMAPPED','车型不存在或不可使用')
   total=int(data.get('target_total',5));daily=int(data.get('daily_limit',total));price=int(data.get('unit_price_fen',0));priority=int(data.get('priority',50));cities=data.get('city_codes',['310100'])
   if total<1 or daily<1 or daily>total or price<0 or not 1<=priority<=100 or not isinstance(cities,list) or not 1<=len(cities)<=100 or any(not isinstance(city,str) or not city.strip() or len(city)>100 for city in cities) or (not managed and any(x not in ['310100','110100'] for x in cities)):raise Fault('DEMAND_INVALID','数量、优先级、价格或城市不符合要求')
   if managed:
    codes=[resolve_media_codes(c,a['org_id'],r['media_id'],{'series_id':s,'city_code':city},adapter_version=r['adapter_version']) for city in cities]
    if len(codes)==1 and r['media_id']=='MEDIA_A' and r['adapter_version']!='zhemanhui-v1':
     yc,_=yiche_fields_from_catalog(yc,{},codes[0])
   if r['media_id']=='MEDIA_A' and len(cities)!=1:raise Fault('YICHE_FIELDS_INVALID','易车需求只可绑定一个易车城市 ID，请按城市分别建需求')
   start=data.get('start_at',later(days=-1));end=data.get('end_at',later(days=30))
   if parse(start)>=parse(end):raise Fault('TIME_INVALID','需求结束必须晚于开始')
   id=uid('dm_')
   try:c.execute('INSERT INTO demands(id,org_id,demand_no,agency_demand_no,buyer_id,receiver_id,media_id,route_id,series_id,city_codes,target_total,daily_limit,priority,start_at,end_at,unit_price_fen,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,a['org_id'],no,data.get('agency_demand_no'),r['buyer_id'],r['receiver_id'],r['media_id'],r['id'],s,canonical(cities),total,daily,priority,start,end,price,a['id'],now()))
   except sqlite3.IntegrityError:raise Fault('DEMAND_DUPLICATE','该媒体下需求编号已存在',409)
   if yc:c.execute('UPDATE demands SET yiche_config=? WHERE id=?',(canonical(yc),id))
   project=data.get('sale_project','UNKNOWN')
   if project not in ('MEMBER','KA','UNKNOWN'):raise Fault('SALE_PROJECT_REQUIRED','请选择会员或KA')
   c.execute('UPDATE demands SET sale_project=?,auto_enabled=? WHERE id=?',(project,int(self._project_mode(c,a,r['buyer_id'],project)=='AUTO'),id))
   self._snapshot_demand(c,a,id);self.audit(c,a,'DEMAND_CREATE',id,{'demand_no':no});return self.one(c,'demands',id,a)
 def _snapshot_demand(self,c,a,id):
  d=self.one(c,'demands',id,a);c.execute('INSERT INTO demand_versions VALUES(?,?,?,?,?,?)',(uid('dv_'),id,d['version'],canonical(d),a['id'],now()))
 def demand_action(self,a,id,action,version):
  self.require(a,'approve' if action=='approve' else 'demand')
  with self.tx() as c:
   d=self.one(c,'demands',id,a)
   if d['version']!=int(version):raise Fault('VERSION_CONFLICT','需求已修改，请刷新',409)
   states={'submit':(['DRAFT'],'PENDING_REVIEW'),'approve':(['PENDING_REVIEW'],'ACTIVE'),'pause':(['ACTIVE'],'PAUSED'),'resume':(['PAUSED'],'ACTIVE'),'close':(['DRAFT','PENDING_REVIEW','ACTIVE','PAUSED'],'CLOSED')}
   if action not in states or d['status'] not in states[action][0]:raise Fault('STATE_CONFLICT','当前状态不允许此操作',409)
   if action=='approve' and d['created_by']==a['id']:raise Fault('SELF_APPROVAL','创建人不能审核自己的需求',403)
   if d['media_id']=='MEDIA_A' and not d.get('external_project_id') and action in ['submit','approve','resume']:self._yiche_demand(json.loads(d['yiche_config']),True)
   if action in ['approve','resume'] and d['end_at']<=now():raise Fault('DEMAND_EXPIRED','需求已过期')
   c.execute('UPDATE demands SET status=?,version=version+1,approved_by=CASE WHEN ?=? THEN ? ELSE approved_by END WHERE id=?',(states[action][1],action,'approve',a['id'],id))
   if action in ['pause','close']:
    for x in c.execute("SELECT id FROM deliveries WHERE demand_id=? AND first_sent_at IS NULL AND send_status IN ('QUEUED','RESERVED','RETRY_WAIT')",(id,)).fetchall():self._cancel(c,a,x['id'],'DEMAND_'+action.upper())
   self._snapshot_demand(c,a,id);self.audit(c,a,'DEMAND_'+action.upper(),id)
   return self.one(c,'demands',id,a)
 def edit_demand(self,a,id,data):
  self.require(a,'demand')
  with self.tx() as c:
   d=self.one(c,'demands',id,a)
   if d['version']!=int(data.get('version',0)):raise Fault('VERSION_CONFLICT','需求版本变化',409)
   if d.get('external_project_id') and any(k in data for k in ('target_total','daily_limit','unit_price_fen','series_id','city_codes','route_id','sale_project')):raise Fault('SOURCE_MANAGED','该需求字段由原表和同步设置维护，请修改对应来源')
   if any(k in data for k in ['demand_no','media_id','route_id','series_id']):raise Fault('IMMUTABLE','激活后的主体、车型及媒体编号不可直接修改')
   total=int(data.get('target_total',d['target_total']));daily=int(data.get('daily_limit',d['daily_limit']));price=int(data.get('unit_price_fen',d['unit_price_fen']))
   if d['quantity_basis']!='DAILY' and total<d['reserved_count']+d['valid_count']:raise Fault('QUOTA_CONFLICT','总量不能小于有效加在途',409)
   if total<1 or not 1<=daily<=total or price<0:raise Fault('DEMAND_INVALID','数量或价格无效')
   if 'yiche' in data:
    if d['media_id']!='MEDIA_A':raise Fault('YICHE_FIELDS_INVALID','只有易车需求可配置易车字段')
    yc=self._yiche_demand(data['yiche'],d['status'] not in ('DRAFT','CLOSED'))
    if len(json.loads(d['city_codes']))!=1:raise Fault('YICHE_FIELDS_INVALID','请按城市分别建立易车需求')
    c.execute('UPDATE demands SET yiche_config=? WHERE id=?',(canonical(yc),id))
   if 'auto_enabled' in data or 'sale_project' in data:
    project=data.get('sale_project',d['sale_project']);enabled=data.get('auto_enabled',int(self._project_mode(c,a,d['buyer_id'],project)=='AUTO'))
    if type(enabled) is not int or enabled not in (0,1) or project not in ('KA','MEMBER','UNKNOWN') or (enabled and project=='UNKNOWN'):
     raise Fault('AUTO_CONFIG_INVALID','自动接收必须选择项目（KA或会员）')
    c.execute('UPDATE demands SET auto_enabled=?,sale_project=? WHERE id=?',(enabled,project,id))
    if project in ('MEMBER','KA') and 'auto_enabled' in data:self._set_project_mode(c,a,d['buyer_id'],project,'AUTO' if enabled else 'MANUAL')
   c.execute('UPDATE demands SET target_total=?,daily_limit=?,unit_price_fen=?,version=version+1 WHERE id=?',(total,daily,price,id));self._snapshot_demand(c,a,id);self.audit(c,a,'DEMAND_EDIT',id,{'target':total,'price_fen':price});return self.one(c,'demands',id,a)
 def _effective_lead(self,lead,at=None):
  # Read-time status prevents expiry from depending on a maintenance job.
  lead=dict(lead)
  if lead['status']=='READY' and parse(lead['expires_at'])<=parse(at or now()):
   lead.update(status='EXPIRED',reason='LEAD_EXPIRED')
  return lead
 def _basic_match_block(self,c,a,l,d,project=None,match_context=None):
  """基础业务条件：有效线索、需求时间、车型名称、城市及项目，不检查执行开关。"""
  l=self._effective_lead(l)
  if l['status']!='READY':return 'LEAD_'+('INVALID' if l['status']=='QUARANTINED' else l['status'])
  if parse(l['collected_at'])<parse(now())-timedelta(hours=d['max_age_hours']):return 'DEMAND_LEAD_TOO_OLD'
  if d.get('sync_state')=='REMOVED' or d['status']!='ACTIVE' or d['start_at']>now() or d['end_at']<=now():return 'DEMAND_INACTIVE'
  if d.get('external_project_id'):
   reason=self._synced_vehicle_region_block(c,a,l,d,match_context)
   if reason:return reason
   if project and project not in ('UNKNOWN',d['sale_project']):return 'PROJECT_MISMATCH'
  else:
   from vehicle_matching import demand_model_matches
   if not demand_model_matches(c,a['org_id'],l,d,match_context):return 'MODEL_MISMATCH'
   from synced_demands import city_name
   if city_name(l['city_code']) not in {city_name(value) for value in json.loads(d['city_codes'])}:return 'CITY_MISMATCH'
  return None
 def _block(self,c,a,l,d,skip_delivery=None,project=None,match_context=None):
  reason=self._basic_match_block(c,a,l,d,project,match_context)
  if reason:return reason
  r=self.one(c,'routes',d['route_id'],a)
  # 既有软米适配器仅确认会员协议，KA拉需求不等于已确认KA提交接口。
  if r['adapter_version']=='zhemanhui-v1' and (d.get('api_submit_limit') is not None or d['sale_project']!='MEMBER' or project not in (None,'UNKNOWN','MEMBER')):return 'KA_DELIVERY_NOT_CONFIGURED'
  if d.get('external_project_id'):
   buyer=self.one(c,'partners',d['receiver_id'],a)
   if not c.execute('SELECT 1 FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],d['buyer_id'],d['sale_project'])).fetchone():return 'COOPERATION_PROJECT_REQUIRED'
  if not self.mock_enabled and r['adapter_version']=='mock-v1' and not (self.yiche_live and r['receiver_id']=='MEDIA_A' and r['media_id']=='MEDIA_A'):return 'MOCK_DISABLED'
  if not self._demand_project_enabled(c,a,d,project):return 'PROJECT_PAUSED'
  if not r['enabled']:return 'ROUTE_UNAVAILABLE'
  from media_catalog_matching import requires_media_catalogs,resolve_media_codes
  if requires_media_catalogs(c,a['org_id'],r,strict=not self.mock_enabled):
   try:resolve_media_codes(c,a['org_id'],r['media_id'],l,match_context,adapter_version=r['adapter_version'])
   except Fault as error:return error.code
  if r['adapter_version']=='zhemanhui-v1':
   from route_sending import send_block
   blocked=send_block(c,a['org_id'],r)
   if blocked:return blocked
  ch=self.one(c,'channels',l['channel_id'],a)
  if not ch['enabled']:return 'SOURCE_BLOCKED'
  for p in set([r['receiver_id'],r['media_id'],ch['source_partner_id']]):
   partner=self.one(c,'partners',p,a)
   if not partner['enabled'] or partner['contract_end']<=now():return 'CONTRACT_EXPIRED'
  for recipient in ([] if l['direct_admission'] else set([d['receiver_id'],d['media_id']])):
   if not c.execute("SELECT 1 FROM consents WHERE lead_id=? AND recipient_id=? AND review_status='APPROVED' AND revoked_at IS NULL AND expires_at>?",(l['id'],recipient,now())).fetchone():return 'CONSENT_MISSING'
  if l['exclusive_media_id'] and (not l['exclusive_until'] or l['exclusive_until']>now()) and l['exclusive_media_id']!=d['media_id']:return 'EXCLUSIVE_CONFLICT'
  if c.execute('SELECT 1 FROM risk_blocklist WHERE org_id=? AND phone_hmac=?',(a['org_id'],l['phone_hmac'])).fetchone():return 'SOURCE_BLOCKED'
  setting=dict(c.execute('SELECT * FROM settings WHERE org_id=?',(a['org_id'],)).fetchone())
  if not setting['dispatch_enabled']:return 'DISPATCH_PAUSED'
  reason=self._sale_block(c,a,l,d,setting,skip_delivery,project)
  if reason:return reason
  if d.get('external_project_id') and self.yiche_live:return 'YICHE_CONFIG_REQUIRED'
  if d['media_id']=='MEDIA_A' and r['adapter_version']!='zhemanhui-v1' and not skip_delivery and not d.get('external_project_id'):
   raw_cfg=json.loads(d['yiche_config']);extra=self._yiche_lead(l)
   if requires_media_catalogs(c,a['org_id'],r,strict=not self.mock_enabled):
    from media_catalog_matching import yiche_fields_from_catalog
    raw_cfg,extra=yiche_fields_from_catalog(raw_cfg,extra,resolve_media_codes(c,a['org_id'],r['media_id'],l,match_context,adapter_version=r['adapter_version']))
   try:cfg=yiche.demand_fields(raw_cfg,True)
   except ValueError:return 'YICHE_CONFIG_REQUIRED'
   if any(k in cfg and k in extra and cfg[k]!=extra[k] for k in yiche.LEAD_IDS):return 'YICHE_MAPPING_MISMATCH'
  if d.get('api_submit_limit') is not None:
   from ka_demand_sources import submission_usage
   if d['api_quota_day']!=day().replace('-',''):return 'API_QUOTA_STALE'
   reserved,submitted=submission_usage(c,d['id'],skip_delivery)
   if reserved+submitted>=d['api_submit_limit']:return 'DAILY_QUOTA_FULL'
  elif d.get('quantity_basis')=='DAILY':
   reserved,valid=self._daily_counts(c,d['id'],skip_delivery)
   if reserved+valid>=d['target_total']:return 'DAILY_QUOTA_FULL'
  elif not skip_delivery:
   if d['reserved_count']+d['valid_count']>=d['target_total']:return 'QUOTA_FULL'
   dc=c.execute('SELECT * FROM daily_counter WHERE demand_id=? AND day=?',(d['id'],day())).fetchone()
   if dc and dc['reserved']+dc['sent']>=d['daily_limit']:return 'DAILY_QUOTA_FULL'
  return None
 def preview(self,a,lead_ids=None,demand_ids=None,projects=None,mode='MANUAL'):
  if mode not in ('MANUAL','AUTO'):raise Fault('PROJECT_MODE_INVALID','匹配方式无效')
  self.require(a,'dispatch');c=self.connect()
  try:
   leads=[dict(x) for x in c.execute('SELECT * FROM leads WHERE org_id=? ORDER BY collected_at,id',(a['org_id'],)) if not lead_ids or x['id'] in lead_ids]
   demands=[dict(x) for x in c.execute('SELECT * FROM demands WHERE org_id=? ORDER BY created_at,id',(a['org_id'],)) if not demand_ids or x['id'] in demand_ids]
   items=[]
   lead_fields=self._lead_field_override(c,a)
   for l in leads:
    for d in demands:
     reason=self._project_mode_block(c,a,d,mode,(projects or {}).get(d['id'],d['sale_project'])) or self._block(c,a,l,d,project=(projects or {}).get(d['id'],d['sale_project']));items.append({'lead_id':l['id'],**({'lead_no':l['lead_no']} if lead_fields is None or 'lead_no' in lead_fields else {}),'demand_id':d['id'],'demand_no':external_demand_no(d),'media_id':d['media_id'],'route_id':d['route_id'],'eligible':reason is None,'reason':reason,'project':d['sale_project'],'project_locked':bool(d.get('external_project_id'))})
   return {'items':items,'eligible':sum(x['eligible'] for x in items),'preview_version':self.preview_version(c,a)}
  finally:c.close()
 def preview_version(self,c,a):
  return digest(canonical([tuple(x) for x in c.execute('SELECT id,version,status FROM demands WHERE org_id=? ORDER BY id',(a['org_id'],))])+canonical([tuple(x) for x in c.execute('SELECT * FROM settings WHERE org_id=?',(a['org_id'],))])+canonical([tuple(x) for x in c.execute('SELECT * FROM partner_projects WHERE org_id=? ORDER BY partner_id,project',(a['org_id'],))])+canonical([tuple(x) for x in c.execute('SELECT * FROM route_send_controls WHERE org_id=? ORDER BY route_id',(a['org_id'],))])+canonical([tuple(x) for x in c.execute('SELECT id,enabled FROM routes WHERE org_id=? ORDER BY id',(a['org_id'],))]))
 def dispatch(self,a,pairs,key,preview_version=None,projects=None,_connection=None,mode="MANUAL"):
  self.require(a,'dispatch')
  if not key:raise Fault('IDEMPOTENCY_REQUIRED','缺少业务幂等键')
  if projects is not None and (not isinstance(projects,dict) or any(projects.get(did) not in ('KA','MEMBER') for _,did in pairs)):
   raise Fault('SALE_PROJECT_REQUIRED','每项需求必须选择项目（KA或会员）')
  body=canonical({'pairs':pairs,'projects':projects,'mode':mode})
  with (nullcontext(_connection) if _connection else self.tx()) as c:
   old=c.execute('SELECT * FROM idempotency WHERE org_id=? AND actor_id=? AND path=? AND key=?',(a['org_id'],a['id'],'dispatch',key)).fetchone()
   if old:
    if old['payload_hash']!=digest(body):raise Fault('IDEMPOTENCY_CONFLICT','相同幂等键请求内容不同',409)
    return json.loads(old['result'])
   if preview_version and self.preview_version(c,a)!=preview_version:raise Fault('VERSION_CONFLICT','需求或设置已变更，请重新预览',409)
   items=[]
   for lid,did in pairs:
    l=self.one(c,'leads',lid,a);d=self.one(c,'demands',did,a);project=(projects or {}).get(did,d['sale_project']);reason=self._project_mode_block(c,a,d,mode,project) or self._block(c,a,l,d,project=project)
    if reason:items.append({'lead_id':lid,'demand_id':did,'reason':reason});continue
    id=uid('dl_');r=self.one(c,'routes',d['route_id'],a)
    if r['adapter_version']=='zhemanhui-v1':
     from route_sending import control
     r['send_environment']=control(c,a['org_id'],r['id'])['environment']
    payload={'delivery_id':id,'lead_ref':l['lead_no'],'demand_no':external_demand_no(d),'agency_demand_no':d['agency_demand_no'],'customer':{'name':self.vault.decrypt(l['name_cipher']),'phone':self.vault.decrypt(l['phone_cipher'])},'vehicle':{'series':l['series_id'],'city':l['city_code']},'route':{'direct_receiver':r['receiver_id'],'final_media':r['media_id']},'collected_at':l['collected_at'],'schema_version':'1'}
    from media_catalog_matching import requires_media_catalogs,resolve_media_codes
    if requires_media_catalogs(c,a['org_id'],r,strict=not self.mock_enabled):payload['media_dictionary']=resolve_media_codes(c,a['org_id'],r['media_id'],l,adapter_version=r['adapter_version'])
    if d.get('external_project_id'):
     payload['simulation_only']=True
     payload['source_project']={'id':d['external_project_id'],'brand':d['match_brand'],'models':json.loads(d['match_models']),'regions':json.loads(d['match_regions']),'project':d['sale_project'],'demand_no':external_demand_no(d)}
    elif d['media_id']=='MEDIA_A' and r['adapter_version']!='zhemanhui-v1':
     extra=self._yiche_lead(l);cfg=json.loads(d['yiche_config'])
     if payload.get('media_dictionary'):
      from media_catalog_matching import yiche_fields_from_catalog
      cfg,extra=yiche_fields_from_catalog(cfg,extra,payload['media_dictionary'])
      payload['yiche_config']=cfg
     payload['yiche_request']=yiche.build_request(cfg,extra,payload['customer']['phone'],payload['customer']['name'],id)
     payload['yiche_mock']=not (self.yiche_live and r['receiver_id']=='MEDIA_A')
     if not payload['yiche_mock']:
      payload['yiche_lead']=extra
    c.execute('INSERT INTO deliveries(id,org_id,lead_id,demand_id,receiver_id,media_id,route_id,route_snapshot,demand_snapshot,payload_cipher,price_fen,pricing_basis,day,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,a['org_id'],lid,did,d['receiver_id'],d['media_id'],r['id'],canonical(r),canonical(d),self.vault.encrypt(canonical(payload)),d['unit_price_fen'],d['pricing_basis'],day(),now()))
    setting=dict(c.execute('SELECT * FROM settings WHERE org_id=?',(a['org_id'],)).fetchone())
    c.execute('UPDATE deliveries SET sale_project=?,dispatch_mode=?,rule_version=?,rule_snapshot=? WHERE id=?',(project,mode,setting['version'],canonical(setting),id))
    to=[r['receiver_id']] if r['receiver_id']==r['media_id'] else [r['receiver_id'],r['media_id']]
    prev='PLATFORM'
    for idx,p in enumerate(to,1):c.execute('INSERT INTO hops(id,delivery_id,hop_no,from_id,to_id) VALUES(?,?,?,?,?)',(uid('hp_'),id,idx,prev,p));prev=p
    c.execute('INSERT INTO dedup_registry VALUES(?,?,?,?,?,?) ON CONFLICT(org_id,phone_hmac,series_id,media_id) DO UPDATE SET owner_delivery_id=excluded.owner_delivery_id,blocked_until=excluded.blocked_until',(a['org_id'],l['phone_hmac'],l['series_id'],d['media_id'],id,later(days=setting['dedup_window_days'])))
    c.execute('INSERT INTO daily_counter VALUES(?,?,1,0) ON CONFLICT(demand_id,day) DO UPDATE SET reserved=reserved+1',(did,day()))
    self._ledger(c,id,'create:'+id,1,0,'RESERVE')
    c.execute('INSERT INTO outbox(id,delivery_id,created_at) VALUES(?,?,?)',(uid('ob_'),id,now()));self.audit(c,a,'DISPATCH_CREATE',id,{'demand_no':external_demand_no(d)});items.append({'lead_id':lid,'demand_id':did,'delivery_id':id})
   result={'items':items,'created':sum('delivery_id' in x for x in items),'rejected':sum('reason' in x for x in items)}
   c.execute('INSERT INTO idempotency VALUES(?,?,?,?,?,?,?)',(a['org_id'],a['id'],'dispatch',key,digest(body),canonical(result),now()));return result
 def fill_delivery_project(self,a,delivery_id,project):
  self.require(a,'write')
  if project not in ('KA','MEMBER'):raise Fault('SALE_PROJECT_REQUIRED','请选择项目（KA或会员）')
  with self.tx() as c:
   fields=self._lead_field_override(c,a)
   if fields is not None and 'project' not in fields:raise Fault('FORBIDDEN','当前账号无项目字段权限',403)
   delivery=self.one(c,'deliveries',delivery_id,a)
   if delivery['sale_project']!='UNKNOWN':raise Fault('VERSION_CONFLICT','项目已登记，请刷新',409)
   c.execute('UPDATE deliveries SET sale_project=?,version=version+1 WHERE id=?',(project,delivery_id))
   self.audit(c,a,'DELIVERY_PROJECT_FILLED',delivery_id,{'project':project})
  return {'saved':True}

 def _ledger(self,c,did,event,rd,vd,reason):
  d=dict(c.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone())
  ins=c.execute('INSERT OR IGNORE INTO quota_ledger VALUES(?,?,?,?,?,?,?,?)',(uid('ql_'),did,d['demand_id'],event,rd,vd,reason,now()))
  if ins.rowcount:
   c.execute('UPDATE demands SET reserved_count=reserved_count+?,valid_count=valid_count+? WHERE id=?',(rd,vd,d['demand_id']))
   dm=dict(c.execute('SELECT * FROM demands WHERE id=?',(d['demand_id'],)).fetchone())
   if dm['quantity_basis']!='DAILY' and dm['valid_count']>=dm['target_total'] and dm['status']=='ACTIVE':c.execute("UPDATE demands SET status='COMPLETED' WHERE id=?",(dm['id'],))
   elif dm['quantity_basis']!='DAILY' and dm['status']=='COMPLETED' and dm['valid_count']<dm['target_total'] and dm['end_at']>now():c.execute("UPDATE demands SET status='ACTIVE' WHERE id=?",(dm['id'],))
 def _cancel(self,c,a,id,reason):
  d=self.one(c,'deliveries',id,a)
  if d['first_sent_at'] or d['send_status'] in ['CANCELED','FAILED']:return
  if d['quota_state']=='RESERVED':self._ledger(c,id,'cancel:'+id,-1,0,reason)
  c.execute("UPDATE deliveries SET send_status='CANCELED',quota_state='RELEASED',error=? WHERE id=?",(reason,id));c.execute("UPDATE outbox SET status='CANCELED' WHERE delivery_id=?",(id,));c.execute('DELETE FROM dedup_registry WHERE owner_delivery_id=?',(id,));c.execute('UPDATE daily_counter SET reserved=reserved-1 WHERE demand_id=? AND day=?',(d['demand_id'],d['day']));self.audit(c,a,'DISPATCH_CANCEL',id,reason)
 def worker(self,a,auto_only=False):
  self.require(a,'dispatch');processed=[];live_jobs=[];zmh_jobs=[]
  with self.tx() as c:
   for ob in c.execute("SELECT o.* FROM outbox o JOIN deliveries d ON d.id=o.delivery_id WHERE d.org_id=? AND o.status='PENDING' ORDER BY o.created_at,o.id",(a['org_id'],)).fetchall():
    d=self.one(c,'deliveries',ob['delivery_id'],a)
    if auto_only and d['dispatch_mode']!='AUTO':continue
    if d['first_sent_at'] or d['send_status']!='QUEUED':c.execute("UPDATE outbox SET status='DONE' WHERE id=?",(ob['id'],));continue
    l=self.one(c,'leads',d['lead_id'],a);dm=self.one(c,'demands',d['demand_id'],a);reason=self._block(c,a,l,dm,d['id'])
    if reason:self._cancel(c,a,d['id'],reason);continue
    live=False;zmh=False
    if json.loads(d['route_snapshot']).get('adapter_version')=='zhemanhui-v1':
     try:request=self._zmh_request(c,a,d,l,json.loads(self.vault.decrypt(d['payload_cipher'])))
     except Fault as error:self._cancel(c,a,d['id'],error.code);continue
     except ValueError:self._cancel(c,a,d['id'],'ZMH_CONFIG_REQUIRED');continue
     zmh=True
    if not zmh and d['media_id']=='MEDIA_A':
     payload=json.loads(self.vault.decrypt(d['payload_cipher']))
     if payload.get('simulation_only') and dm.get('external_project_id') and self.yiche_live is None:
      pass
     elif 'yiche_request' not in payload:
      self._cancel(c,a,d['id'],'YICHE_CONFIG_REQUIRED');continue
     live=payload.get('yiche_mock') is False and d['receiver_id']=='MEDIA_A'
     if live:
      if self.yiche_live is None:continue
      cfg=payload.get('yiche_config') or json.loads(json.loads(d['demand_snapshot'])['yiche_config'])
      extra=payload['yiche_lead']
      from media_catalog_matching import requires_media_catalogs,delivery_media_codes,yiche_fields_from_catalog
      if requires_media_catalogs(c,a['org_id'],json.loads(d['route_snapshot']),strict=not self.mock_enabled):
       try:
        codes=delivery_media_codes(c,a['org_id'],d,l,payload)
        cfg,extra=yiche_fields_from_catalog(cfg,extra,codes)
       except Fault as error:self._cancel(c,a,d['id'],error.code);continue
      if not self._yiche_vehicle_exists(c,a['org_id'],cfg,extra,d['media_id']):
       self._cancel(c,a,d['id'],'YICHE_CATALOG_MISMATCH');continue
      request,token_hash=yiche_transport.prepare(self.yiche_live,cfg,extra,payload['customer'],d['id'])
    # 正式环境不允许未知适配器或旧交付快照回退成模拟成功。
    if not self.mock_enabled and not live and not zmh:self._cancel(c,a,d['id'],'MOCK_DISABLED');continue
    r=json.loads(d['route_snapshot']);mode=r['mode']
    if d['day']!=day():
     dc=c.execute('SELECT * FROM daily_counter WHERE demand_id=? AND day=?',(dm['id'],day())).fetchone()
     if dm['quantity_basis']!='DAILY' and dc and dc['sent']+dc['reserved']>=dm['daily_limit']:self._cancel(c,a,d['id'],'DAILY_QUOTA_FULL');continue
     c.execute('UPDATE daily_counter SET reserved=reserved-1 WHERE demand_id=? AND day=?',(dm['id'],d['day']));c.execute('INSERT INTO daily_counter VALUES(?,?,1,0) ON CONFLICT(demand_id,day) DO UPDATE SET reserved=reserved+1',(dm['id'],day()));c.execute('UPDATE deliveries SET day=? WHERE id=?',(day(),d['id']))
    if live:
     c.execute('INSERT INTO yiche_submissions(delivery_id,platform_id,token_hash) VALUES(?,?,?)',
               (d['id'],cfg['platFormId'],token_hash))
     live_jobs.append((d['id'],request))
    if zmh:zmh_jobs.append((d['id'],request))
    status='UNKNOWN' if live or zmh or mode=='TIMEOUT' else 'ACKED';c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',(uid('at_'),d['id'],1,d['id'],status,None if status=='UNKNOWN' else 200,now()))
    c.execute('UPDATE deliveries SET send_status=?,first_sent_at=?,acked_at=?,error=? WHERE id=?',(status,now(),now() if status=='ACKED' else None,'RESULT_UNKNOWN' if status=='UNKNOWN' else '',d['id']))
    c.execute('UPDATE daily_counter SET reserved=reserved-1,sent=sent+1 WHERE demand_id=? AND day=?',(dm['id'],day()))
    if status=='ACKED':c.execute("UPDATE hops SET acceptance='ACCEPTED',external_id=?,accepted_at=? WHERE delivery_id=? AND hop_no=1",('MOCK-'+d['id'],now(),d['id']))
    c.execute("UPDATE outbox SET status='DONE',attempts=attempts+1 WHERE id=?",(ob['id'],))
    self.audit(c,a,'ZMH_SEND_CLAIMED' if zmh else 'YICHE_SEND_CLAIMED' if live else 'MOCK_SEND',d['id'],{} if live or zmh else {'mode':mode})
    processed.append(d['id'])
  for delivery_id,request in zmh_jobs:self._send_zmh(a,delivery_id,request)
  for delivery_id,request in live_jobs:
   self._send_yiche(a,delivery_id,request)
  return {'processed':processed,'count':len(processed)}
 def _yiche_vehicle_exists(self,c,org,cfg,extra,media_id='MEDIA_A'):
  car=extra.get('carId',cfg.get('carId'))
  exists=c.execute('''SELECT 1 FROM media_vehicle_items
      WHERE org_id=? AND media_id=? AND series_id=? AND factory_id=?
      AND (? IS NULL OR car_id=?) AND (? IS NULL OR brand_id=?) LIMIT 1''',
      (org,media_id,cfg['serialId'],cfg['manufacturerId'],car,car,cfg.get('brandId'),cfg.get('brandId'))).fetchone()
  if not exists:return False
  return 'csId2' not in cfg or bool(c.execute(
      "SELECT 1 FROM media_vehicle_items WHERE org_id=? AND media_id=? AND series_id=?",
      (org,media_id,cfg['csId2'])).fetchone())
 def _send_yiche(self,a,id,request):
  # Claim first, then perform network I/O outside the SQLite transaction.
  with self.tx() as c:
   d=self.one(c,'deliveries',id,a)
   lead=self.one(c,'leads',d['lead_id'],a)
   demand=self.one(c,'demands',d['demand_id'],a)
   blocked=self._block(c,a,lead,demand,id)
  outcome,code,error=('FAILED',None,blocked) if blocked else yiche_transport.send(self.yiche_live,request)
  with self.tx() as c:
   d=self.one(c,'deliveries',id,a)
   submission=c.execute('SELECT callback_result FROM yiche_submissions WHERE delivery_id=?',(id,)).fetchone()
   c.execute('UPDATE attempts SET outcome=?,http_status=? WHERE delivery_id=? AND attempt_no=1',(outcome,code,id))
   if submission['callback_result'] is not None:
    self.audit(c,a,'YICHE_SEND_RESULT',id,{'outcome':outcome,'http_status':code,'error':error})
    if outcome=='ACKED':
     c.execute("""UPDATE deliveries SET storage_status='STORED',stored_at=COALESCE(stored_at,?),
                 settlement_status=CASE WHEN pricing_basis='STORED' THEN 'ELIGIBLE' ELSE settlement_status END
                 WHERE id=?""",(now(),id))
    return
   if outcome=='ACKED':
    moment=now()
    c.execute("""UPDATE deliveries SET send_status='ACKED',acked_at=?,storage_status='STORED',
                stored_at=?,error='',settlement_status=CASE WHEN pricing_basis='STORED'
                THEN 'ELIGIBLE' ELSE settlement_status END WHERE id=?""",(moment,moment,id))
    c.execute("UPDATE hops SET acceptance='ACCEPTED',external_id=?,accepted_at=? WHERE delivery_id=? AND hop_no=1",(id,moment,id))
   elif outcome=='FAILED':
    if d['quota_state']=='RESERVED':self._ledger(c,id,'yiche-reject:'+id,-1,0,'YICHE_REJECTED')
    c.execute("UPDATE deliveries SET send_status='FAILED',storage_status='REJECTED',quota_state='RELEASED',error=? WHERE id=?",(error,id))
    c.execute("UPDATE hops SET acceptance='REJECTED' WHERE delivery_id=? AND hop_no=1",(id,))
   else:
    c.execute('UPDATE deliveries SET error=? WHERE id=?',(error,id))
   self.audit(c,a,'YICHE_SEND_RESULT',id,{'outcome':outcome,'http_status':code,'error':error})
 def _yiche_code_map(self,c,org_id):
  codes=yiche_transport.CALLBACK_CODES.copy()
  for row in c.execute('SELECT code,status,description FROM yiche_code_overrides WHERE org_id=? ORDER BY rowid',(org_id,)):
   if row['status'] is None:
    codes.pop(row['code'],None)
   else:
    codes[row['code']]=(row['status'],row['description'])
  return codes

 def yiche_codes(self,a):
  self.require(a,'read')
  c=self.connect()
  try:return yiche_transport.callback_codes(self._yiche_code_map(c,a['org_id']))
  finally:c.close()

 def edit_yiche_codes(self,a,method,data):
  self.require(a,'settings')
  def parse_group(group):
   if isinstance(group,dict):
    codes,status,description=(group.get(k) for k in ('codes','status','description'))
    if (isinstance(codes,list) and codes
        and all(isinstance(code,str) and re.fullmatch(r'-?[0-9]{1,12}',code) for code in codes)
        and len(set(codes))==len(codes) and status in ('VALID','INVALID')
        and isinstance(description,str) and 1<=len(description.strip())<=200):
     return codes,(status,description.strip())
   raise Fault('YICHE_CODE_INVALID','请输入不重复的整数 Code 码，并填写有效或无效及原因')
  if not isinstance(data,dict):
   raise Fault('YICHE_CODE_INVALID','请求格式错误')
  old_codes,old_value=parse_group(data.get('previous')) if method!='POST' else ([],None)
  new_codes,new_value=parse_group(data.get('group')) if method!='DELETE' else ([],None)
  with self.tx() as c:
   current=self._yiche_code_map(c,a['org_id'])
   if old_value and set(old_codes)!={code for code,value in current.items() if value==old_value}:
    raise Fault('VERSION_CONFLICT','这组 Code 码已变更，请刷新后重试',409)
   duplicates=set(new_codes)&(current.keys()-set(old_codes))
   if duplicates:
    raise Fault('YICHE_CODE_DUPLICATE','Code 码已存在：'+ ' / '.join(sorted(duplicates)),409)
   for code in dict.fromkeys(old_codes+new_codes):
    status,description=new_value if code in new_codes else (None,'')
    c.execute("""INSERT INTO yiche_code_overrides VALUES(?,?,?,?)
                 ON CONFLICT(org_id,code) DO UPDATE SET status=excluded.status,description=excluded.description""",
              (a['org_id'],code,status,description))
   self.audit(c,a,'YICHE_CODE_'+method,'MEDIA_A',{'before':data.get('previous'),'after':data.get('group')})
  return {'saved':True}

 def receive_yiche_callback(self,params):
  order_id=params['orderId']
  platform_id=yiche.integer(params['platFormId'],'platFormId')
  token=params['callbackToken']
  result,code=params['resultCode'],params.get('code','')
  if not re.fullmatch(r'dl_[0-9a-f]{20}',order_id) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
   raise Fault('YICHE_CALLBACK_INVALID','回调关联参数无效',403)
  with self.tx() as c:
   row=c.execute('''SELECT d.*,s.platform_id,s.token_hash,s.callback_result,s.callback_code
                    FROM yiche_submissions s JOIN deliveries d ON d.id=s.delivery_id
                    WHERE s.delivery_id=? AND d.media_id='MEDIA_A' AND d.receiver_id='MEDIA_A' ''',(order_id,)).fetchone()
   if not row or row['platform_id']!=platform_id or not hmac.compare_digest(row['token_hash'],digest(token)):
    raise Fault('YICHE_CALLBACK_INVALID','回调关联参数无效',403)
   codes=self._yiche_code_map(c,row['org_id'])
   validity=yiche_transport.callback_result(result,code,codes)
   description=codes.get(code,('',''))[1]
   actor={'id':'yiche-callback','org_id':row['org_id']}
   event_id=f'yiche:{order_id}:{result}:{code}'
   if c.execute("SELECT 1 FROM callback_events WHERE org_id=? AND partner_id='MEDIA_A' AND event_id=?",
                (row['org_id'],event_id)).fetchone():return
   when=now()
   event={'event_id':event_id,'delivery_id':order_id,'event_type':'VALIDITY_UPDATED',
          'occurred_at':when,'data':{'validity':validity,'reason_code':code,'resultCode':int(result)}}
   conflict=(validity=='CONFLICT' or row['send_status']=='FAILED' or row['callback_result'] is not None)
   status='CONFLICT' if conflict else 'PROCESSED'
   c.execute('''INSERT INTO callback_events(id,org_id,partner_id,event_id,delivery_id,event_type,event_version,
                occurred_at,received_at,body,payload_hash,status,error)
                VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?)''',
             (uid('cb_'),row['org_id'],'MEDIA_A',event_id,order_id,'VALIDITY_UPDATED',when,when,
              canonical(event),digest(canonical(event)),status,'YICHE_RESULT_CONFLICT' if conflict else ''))
   if conflict:
    c.execute("UPDATE deliveries SET callback_status='CONFLICT',callback_last_at=? WHERE id=?",(when,order_id))
    self.audit(c,actor,'YICHE_CALLBACK_CONFLICT',order_id,{'resultCode':result,'code':code})
    return
   c.execute('UPDATE yiche_submissions SET callback_result=?,callback_code=?,callback_description=? WHERE delivery_id=?',(int(result),code,description,order_id))
   reserved_delta=-1 if row['quota_state']=='RESERVED' else 0
   valid_delta=int(validity=='VALID')-int(row['quota_state']=='VALID')
   self._ledger(c,order_id,event_id,reserved_delta,valid_delta,'RESULT_'+validity)
   stored=validity=='VALID' or row['storage_status']=='STORED'
   eligible=validity=='VALID' if row['pricing_basis']=='VALID' else stored
   c.execute('''UPDATE deliveries SET send_status='ACKED',acked_at=COALESCE(acked_at,?),
       storage_status=?,stored_at=CASE WHEN ? THEN COALESCE(stored_at,?) ELSE stored_at END,
       validity_status=?,validity_version=1,validated_at=?,callback_status='COMPLETE',callback_last_at=?,
       evidence_level='MEDIA_RAW',quota_state=?,settlement_status=?,version=version+1,error='' WHERE id=?''',
       (when,'STORED' if stored else row['storage_status'],stored,when,validity,when,when,
        'VALID' if validity=='VALID' else 'RELEASED','ELIGIBLE' if eligible else 'UNCONFIRMED',order_id))
   c.execute("UPDATE hops SET acceptance='ACCEPTED',external_id=?,accepted_at=COALESCE(accepted_at,?) WHERE delivery_id=? AND hop_no=1",
             (order_id,when,order_id))
   self.audit(c,actor,'YICHE_CALLBACK',order_id,{'resultCode':result,'code':code})
 def receive_callback(self,a,partner,event):
  required=['event_id','event_type','occurred_at']
  allowed={'event_id','event_type','occurred_at','delivery_id','external_delivery_id','event_version','final_media_code','hop_no','reported_by','media_record_id','data'}
  if set(event)-allowed or set(event.get('data',{}))-{'validity','reason_code'}:raise Fault('SCHEMA_INVALID','回调包含不允许的字段，不能接收客户个人信息')
  if any(not event.get(k) for k in required):raise Fault('SCHEMA_INVALID','缺少事件编号、类型或发生时间')
  if event['event_type'] not in ['RECEIVER_ACCEPTED','FORWARDED','MEDIA_STORED','MEDIA_REJECTED','VALIDITY_UPDATED','RESULT_CORRECTED']:raise Fault('SCHEMA_INVALID','不支持的事件类型')
  parse(event['occurred_at']);h=digest(canonical(event))
  with self.tx() as c:
   self.one(c,'partners',partner,a)
   old=c.execute('SELECT * FROM callback_events WHERE org_id=? AND partner_id=? AND event_id=?',(a['org_id'],partner,event['event_id'])).fetchone()
   if old:
    if old['payload_hash']!=h:
     # Commit immutable conflict evidence even though the caller receives an error.
     self.audit(c,a,'CALLBACK_ID_CONFLICT',old['id']);c.commit();raise Fault('EVENT_CONFLICT','同事件编号内容不一致',409)
    return {'duplicate':True,'id':old['id'],'status':old['status']}
   d=None
   if event.get('delivery_id'):
    x=c.execute('SELECT * FROM deliveries WHERE id=? AND org_id=?',(event['delivery_id'],a['org_id'])).fetchone();d=dict(x) if x else None
   elif event.get('external_delivery_id'):
    x=c.execute('SELECT d.* FROM deliveries d JOIN hops h ON h.delivery_id=d.id WHERE d.org_id=? AND h.to_id=? AND h.external_id=?',(a['org_id'],partner,event['external_delivery_id'])).fetchall()
    if len(x)==1:d=dict(x[0])
   if d and c.execute('SELECT 1 FROM zmh_submissions WHERE delivery_id=?',(d['id'],)).fetchone():raise Fault('ZMH_CALLBACK_REQUIRED','此交付需通过软米专用回调处理',403)
   if d and c.execute('SELECT 1 FROM yiche_submissions WHERE delivery_id=?',(d['id'],)).fetchone():
    raise Fault('YICHE_CALLBACK_REQUIRED','此交付需通过易车回调地址处理',403)
   if d and partner not in [d['receiver_id'],d['media_id']]:raise Fault('FORBIDDEN','回调方不属于该交付链路',403)
   if d and event.get('final_media_code')!=d['media_id']:raise Fault('MEDIA_MISMATCH','最终媒体与交付不一致')
   id=uid('cb_');status='PENDING' if d else 'UNMATCHED'
   c.execute('INSERT INTO callback_events(id,org_id,partner_id,event_id,delivery_id,external_id,event_type,event_version,occurred_at,received_at,body,payload_hash,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,a['org_id'],partner,event['event_id'],d['id'] if d else None,event.get('external_delivery_id'),event['event_type'],event.get('event_version'),event['occurred_at'],now(),canonical(event),h,status))
   c.execute('INSERT INTO inbox(id,event_ref,status) VALUES(?,?,?)',(uid('ib_'),id,status));self.audit(c,a,'CALLBACK_PERSIST',id)
  return {'duplicate':False,'id':id,'status':status}
 def process_inbox(self,a):
  self.require(a,'engineering');count=0
  with self.tx() as c:
   for row in c.execute("SELECT * FROM callback_events WHERE org_id=? AND status='PENDING' ORDER BY received_at,id",(a['org_id'],)).fetchall():
    ev=dict(row);e=json.loads(ev['body']);d=self.one(c,'deliveries',ev['delivery_id'],a);ty=ev['event_type'];dim='validity' if ty in ['VALIDITY_UPDATED','RESULT_CORRECTED'] else 'storage';v=ev['event_version']
    status='PROCESSED';error=''
    if not d['first_sent_at']:status='CONFLICT';error='NOT_SENT'
    elif ty in ['RECEIVER_ACCEPTED','FORWARDED']:
     hn=1 if ty=='RECEIVER_ACCEPTED' else 2
     c.execute("UPDATE hops SET acceptance='ACCEPTED',external_id=COALESCE(?,external_id),accepted_at=? WHERE delivery_id=? AND hop_no=?",(e.get('external_delivery_id'),ev['occurred_at'],d['id'],hn))
    elif v is None:status='CONFLICT';error='EVENT_VERSION_REQUIRED'
    elif not isinstance(v,int) or v<1:status='CONFLICT';error='VERSION_INVALID'
    elif v<d[dim+'_version']:status='STALE'
    elif v==d[dim+'_version']:
     desired=e.get('data',{}).get('validity') if dim=='validity' else ('STORED' if ty=='MEDIA_STORED' else 'REJECTED')
     if desired!=d[dim+'_status']:status='CONFLICT';error='SAME_VERSION_DIFFERENT_RESULT'
     else:status='STALE'
    else:
     evidence='MEDIA_RAW' if ev['partner_id']==d['media_id'] else 'AGENCY_REPORTED'
     if dim=='storage':
      value='STORED' if ty=='MEDIA_STORED' else 'REJECTED';c.execute('UPDATE deliveries SET storage_status=?,storage_version=?,stored_at=? WHERE id=?',(value,v,ev['occurred_at'] if value=='STORED' else None,d['id']))
      c.execute('UPDATE hops SET acceptance=?,media_record_id=?,accepted_at=? WHERE delivery_id=? AND hop_no=(SELECT max(hop_no) FROM hops WHERE delivery_id=?)',('ACCEPTED' if value=='STORED' else 'REJECTED',e.get('media_record_id','M-'+d['id']),ev['occurred_at'],d['id'],d['id']))
      if value=='REJECTED' and d['quota_state']=='RESERVED':self._ledger(c,d['id'],'event:'+ev['id'],-1,0,'MEDIA_REJECTED');c.execute("UPDATE deliveries SET quota_state='RELEASED' WHERE id=?",(d['id'],))
     else:
      value=e.get('data',{}).get('validity')
      if value not in ['VALID','INVALID']:status='CONFLICT';error='VALIDITY_INVALID'
      else:
       oldstate=d['quota_state'];newstate='VALID' if value=='VALID' else 'RELEASED'
       rd=(1 if newstate=='RESERVED' else 0)-(1 if oldstate=='RESERVED' else 0);vd=(1 if newstate=='VALID' else 0)-(1 if oldstate=='VALID' else 0)
       if rd or vd:self._ledger(c,d['id'],'event:'+ev['id'],rd,vd,'RESULT_'+value)
       c.execute('UPDATE deliveries SET validity_status=?,validity_version=?,validated_at=?,quota_state=? WHERE id=?',(value,v,ev['occurred_at'],newstate,d['id']))
     if status=='PROCESSED':c.execute('UPDATE deliveries SET evidence_level=?,version=version+1 WHERE id=?',(evidence,d['id']))
    c.execute('UPDATE callback_events SET status=?,error=? WHERE id=?',(status,error,ev['id']));c.execute('UPDATE inbox SET status=?,attempts=attempts+1 WHERE event_ref=?',(status,ev['id']))
    d2=self.one(c,'deliveries',d['id'],a);cb='CONFLICT' if status=='CONFLICT' else ('COMPLETE' if d2['storage_status']!='PENDING' and d2['validity_status']!='PENDING' else 'PARTIAL')
    eligible=(d2['validity_status']=='VALID' if d2['pricing_basis']=='VALID' else d2['storage_status']=='STORED')
    settlement='ELIGIBLE' if eligible else ('REVERSED' if d['settlement_status'] in ['ELIGIBLE','CONFIRMED'] else 'UNCONFIRMED')
    c.execute('UPDATE deliveries SET callback_status=?,callback_last_at=?,settlement_status=? WHERE id=?',(cb,now(),settlement,d['id']));count+=1
  return {'processed':count}
 def mock_callback(self,a,id,action,version=None,event_id=None):
  self.require(a,'engineering')
  if not self.mock_enabled:raise Fault('MOCK_DISABLED','当前环境已禁用模拟反馈',403)
  c=self.connect();d=self.one(c,'deliveries',id,a);c.close()
  dim='storage' if action in ['STORED','REJECTED'] else 'validity';ty={'STORED':'MEDIA_STORED','REJECTED':'MEDIA_REJECTED','VALID':'VALIDITY_UPDATED','INVALID':'VALIDITY_UPDATED'}.get(action)
  if not ty:raise Fault('ACTION_INVALID','未知模拟动作')
  e={'event_id':event_id or uid('TEST-'),'delivery_id':id,'event_type':ty,'event_version':version or d[dim+'_version']+1,'occurred_at':now(),'final_media_code':d['media_id'],'data':{'validity':action} if dim=='validity' else {}}
  res=self.receive_callback(a,d['receiver_id'],e);self.process_inbox(a);return res
 def reconcile(self,a,id):
  self.require(a,'engineering')
  if not self.mock_enabled:raise Fault('MOCK_DISABLED','当前查询仅支持模拟，正式环境禁止模拟查询结果',403)
  with self.tx() as c:
   d=self.one(c,'deliveries',id,a);r=json.loads(d['route_snapshot'])
   if c.execute('SELECT 1 FROM yiche_submissions WHERE delivery_id=?',(id,)).fetchone():raise Fault('QUERY_UNSUPPORTED','易车接口未定义结果查询，请等待回调',409)
   if not r['supports_query']:raise Fault('QUERY_UNSUPPORTED','该适配器无结果查询能力；禁止盲目重发',409)
   if d['send_status']=='UNKNOWN':
    c.execute("UPDATE deliveries SET send_status='ACKED',acked_at=?,error='' WHERE id=?",(now(),id));c.execute("UPDATE hops SET acceptance='ACCEPTED',external_id=?,accepted_at=? WHERE delivery_id=? AND hop_no=1",('MOCK-'+id,now(),id))
   self.audit(c,a,'MOCK_QUERY',id);return {'result':'ACKED','mock':True,'resent':False}
 def settings_update(self,a,data):
  return self.update_distribution_rules(a,data)

 def privacy(self,a,data):
  self.require(a,'privacy');ty=data.get('type','REVOKE')
  if ty not in ['REVOKE','DELETE']:raise Fault('TYPE_INVALID','仅支持撤回或删除')
  with self.tx() as c:
   l=self.one(c,'leads',data['lead_id'],a);id=uid('pr_')
   c.execute('INSERT INTO privacy_requests VALUES(?,?,?,?,?,?,?,?)',(id,a['org_id'],l['id'],ty,data.get('reason','本人核验后申请'),'PENDING_NOTICES',a['id'],now()))
   c.execute("UPDATE leads SET status=?,reason=?,version=version+1 WHERE id=?",('DELETED' if ty=='DELETE' else 'BLOCKED',ty,l['id']));c.execute('UPDATE consents SET revoked_at=? WHERE lead_id=?',(now(),l['id']))
   for d in c.execute("SELECT * FROM deliveries WHERE lead_id=? AND first_sent_at IS NULL AND send_status='QUEUED'",(l['id'],)).fetchall():self._cancel(c,a,d['id'],ty)
   recipients=set()
   for d in c.execute('SELECT * FROM deliveries WHERE lead_id=? AND first_sent_at IS NOT NULL',(l['id'],)):recipients.update([d['receiver_id'],d['media_id']])
   for r in recipients:c.execute('INSERT INTO privacy_notices(id,request_id,recipient_id) VALUES(?,?,?)',(uid('pn_'),id,r))
   if not recipients:c.execute("UPDATE privacy_requests SET status='COMPLETED' WHERE id=?",(id,))
   if ty=='DELETE':
    self._scrub_imports(c,l)
    c.execute('DELETE FROM source_lead_details WHERE lead_id=?',(l['id'],))
    c.execute("UPDATE leads SET phone_cipher='',name_cipher='',yiche_cipher='',phone_hmac=? WHERE id=?",('deleted:'+l['id'],l['id']));c.execute("UPDATE deliveries SET payload_cipher='' WHERE lead_id=?",(l['id'],))
   self.audit(c,a,'PRIVACY_'+ty,l['id'],{'request_id':id,'recipients':list(recipients)})
   # Separate append-only tombstones survive restoring an older database backup.
   tomb={'lead_id':l['id'],'org_id':a['org_id'],'type':ty,'at':now()}
   fd=os.open(str(self.root/'privacy-tombstones.jsonl'),os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
   with os.fdopen(fd,'a') as f:f.write(canonical(tomb)+'\n');f.flush();os.fsync(f.fileno())
  return {'id':id,'notices':len(recipients)}
 def enforce_tombstones(self):
  p=self.root/'privacy-tombstones.jsonl'
  if not p.exists():return
  with self.tx() as c:
   for ln in p.read_text().splitlines():
    t=json.loads(ln);c.execute('UPDATE leads SET status=? WHERE id=? AND org_id=?',('DELETED' if t['type']=='DELETE' else 'BLOCKED',t['lead_id'],t['org_id']));c.execute('UPDATE consents SET revoked_at=COALESCE(revoked_at,?) WHERE lead_id=?',(t['at'],t['lead_id']))
    a={'id':'system','org_id':t['org_id'],'role':'admin'}
    for d in c.execute("SELECT id FROM deliveries WHERE lead_id=? AND first_sent_at IS NULL AND send_status='QUEUED'",(t['lead_id'],)).fetchall():self._cancel(c,a,d['id'],'PRIVACY_RESTORE')
    if t['type']=='DELETE':
     original=c.execute('SELECT * FROM leads WHERE id=?',(t['lead_id'],)).fetchone()
     if original:self._scrub_imports(c,dict(original))
     c.execute('DELETE FROM source_lead_details WHERE lead_id=?',(t['lead_id'],))
     c.execute("UPDATE leads SET phone_cipher='',name_cipher='',yiche_cipher='',phone_hmac=? WHERE id=?",('deleted:'+t['lead_id'],t['lead_id']));c.execute("UPDATE deliveries SET payload_cipher='' WHERE lead_id=?",(t['lead_id'],))
 def _scrub_imports(self,c,l):
  for im in c.execute('SELECT * FROM imports WHERE org_id=?',(l['org_id'],)).fetchall():
   rows=json.loads(self.vault.decrypt(im['rows_cipher']));pv=json.loads(im['preview']);changed=False
   for idx,row in enumerate(rows):
    phone=str(row.get('phone','')).replace(' ','').replace('-','');phone=phone if phone.startswith('+86') else '+86'+phone
    if self.vault.fingerprint(l['org_id'],phone)==l['phone_hmac']:
     row['phone']='';row['name']='';row['recipients']=[];row['evidence_ref']='REMOVED';row['yiche']={};pv['rows'][idx]['phone']='已删除';pv['rows'][idx]['status']='ERROR';pv['rows'][idx]['errors']=['本人已请求删除，禁止再次导入'];changed=True
   if changed:
    pv['valid']=sum(x['status']=='VALID' for x in pv['rows']);pv['duplicates']=sum(x['status']=='DUPLICATE' for x in pv['rows']);pv['errors']=sum(x['status']=='ERROR' for x in pv['rows']);c.execute('UPDATE imports SET rows_cipher=?,preview=? WHERE id=?',(self.vault.encrypt(canonical(rows)),canonical(pv),im['id']))
  c.execute("UPDATE consents SET evidence_ref='REMOVED' WHERE lead_id=?",(l['id'],))
 def _lead_field_override(self,c,a):
  row=c.execute('SELECT visible_fields FROM lead_field_permissions WHERE user_id=?',(a['id'],)).fetchone()
  return json.loads(row['visible_fields']) if row else None
 def lead_fields_settings(self,a):
  self.require(a,'settings')
  with self.tx() as c:
   accounts=c.execute("""SELECT u.id,u.username,u.role,p.visible_fields FROM users u
     LEFT JOIN lead_field_permissions p ON p.user_id=u.id WHERE u.org_id=? ORDER BY u.username""",
     (a['org_id'],)).fetchall()
   return {'columns':self._lead_column_definitions(), 'accounts':[
    {'id':r['id'],'username':r['username'],'role':r['role'],
     'fields':json.loads(r['visible_fields']) if r['visible_fields'] is not None else None} for r in accounts]}
 def update_lead_fields(self,a,account_id,fields):
  self.require(a,'settings')
  keys=dict(LEAD_COLUMNS)
  if fields is not None and (not isinstance(fields,list) or any(not isinstance(f,str) or f not in keys for f in fields)):
   raise Fault('LEAD_FIELDS_INVALID','请选择有效的线索字段')
  with self.tx() as c:
   self.one(c,'users',account_id,a)
   if fields is None:
    c.execute('DELETE FROM lead_field_permissions WHERE user_id=?',(account_id,))
   else:
    fields=[key for key in keys if key in fields]
    c.execute("""INSERT INTO lead_field_permissions VALUES(?,?)
     ON CONFLICT(user_id) DO UPDATE SET visible_fields=excluded.visible_fields""",(account_id,canonical(fields)))
   self.audit(c,a,'LEAD_FIELD_PERMISSIONS',account_id,{'fields':fields})
  return {'fields':fields}
 def _lead_column_definitions(self,fields=None):
  return [{'key':key,'label':label} for key,label in LEAD_COLUMNS if fields is None or key in fields]
 def _lead_columns(self,c,a,r,fields):
  out={}
  if 'customer' in fields:
   out['customer']=customer_label(self.vault.decrypt(r['name_cipher']),r['customer_title'])
  if 'supplier' in fields:
   supplier=c.execute('SELECT p.id,p.name FROM channels ch JOIN partners p ON p.id=ch.source_partner_id AND p.org_id=ch.org_id WHERE ch.id=? AND ch.org_id=?',(r['channel_id'],a['org_id'])).fetchone()
   out['supplier']=supplier['name'] if supplier else '';out['supplier_id']=supplier['id'] if supplier else ''
  if 'lead_no' in fields:out['lead_no']=r['lead_no']
  if 'city' in fields:
   out['city']={'310100':'上海市','110100':'北京市'}.get(r['city_code'],r['city_code'])
   out['city_code']=r['city_code']
  if 'brand' in fields or 'model' in fields:
   vehicle=c.execute('SELECT brand_name,series_name FROM catalog WHERE series_id=?',(r['series_id'],)).fetchone()
   if 'brand' in fields:out['brand']=vehicle['brand_name'] if vehicle else ''
   if 'model' in fields:
    out['model']=vehicle['series_name'] if vehicle else r['series_id']
    out['series_id']=r['series_id']
  if 'phone' in fields:
   phone=self.vault.decrypt(r['phone_cipher'])
   out['phone']=phone[:6]+'****'+phone[-4:] if phone else '已删除'
  if 'collected_at' in fields:out['collected_at']=r['collected_at']
  if 'acquisition_channel' in fields:out['acquisition_channel']=r['acquisition_channel']
  sale_fields={'is_sold','sold_media','sold_at','project','sales_count'}
  if fields & sale_fields:
   records=self._sale_records(c,r['id'])
   if 'is_sold' in fields:out['is_sold']='是' if records else '否'
   if 'sold_media' in fields:out['sold_media']=' / '.join(dict.fromkeys(row['media_name'] for row in records))
   if 'sold_at' in fields:out['sold_at']=max((row['sold_at'] for row in records),default='')
   if 'project' in fields:out['project']=' / '.join(dict.fromkeys({'KA':'KA','MEMBER':'会员','UNKNOWN':'待补录'}[row['project']] for row in records))
   if 'sales_count' in fields:out['sales_count']=len(self._matched_media(c,r['id']))
  return out
 def _vehicle_filter_options(self,c,a,fields):
  # Media dictionaries use different IDs. Reuse the internal IDs where names match
  # so historical leads can still be filtered against the full vehicle library.
  def normalized(value):
   return ''.join(unicodedata.normalize('NFKC',value).casefold().split())
  local=c.execute('SELECT brand_id,brand_name,series_id,series_name FROM catalog WHERE series_id NOT IN (SELECT series_id FROM manual_vehicle_entries WHERE org_id!=?) ORDER BY series_id',(a['org_id'],)).fetchall()
  brand_ids={normalized(row['brand_name']):row['brand_id'] for row in local}
  series_ids={(normalized(row['brand_name']),normalized(row['series_name'])):row['series_id'] for row in local}
  brands={}
  models={}
  records=c.execute("""SELECT DISTINCT brand_name,series_name FROM media_vehicle_items
     WHERE org_id=? ORDER BY brand_name,series_name""",(a['org_id'],))
  for row in records:
   brand_name,series_name=row['brand_name'],row['series_name']
   brand,series=normalized(brand_name),normalized(series_name)
   brand_value=brand_ids.get(brand,'library:'+brand)
   brands.setdefault(brand_value,{'value':brand_value,'label':brand_name})
   if 'model' in fields:
    series_value=series_ids.get((brand,series),'library:'+canonical([brand,series]))
    option={'value':series_value,'label':series_name}
    if 'brand' in fields:option['brand']=brand_value
    models.setdefault(series_value,option)
  for row in c.execute('''SELECT v.* FROM catalog v JOIN manual_vehicle_entries m ON m.series_id=v.series_id
                           WHERE m.org_id=? ORDER BY v.brand_name,v.series_name''',(a['org_id'],)):
   brand=normalized(row['brand_name'])
   brand_value=brand_ids.get(brand,row['brand_id'])
   brands.setdefault(brand_value,{'value':brand_value,'label':row['brand_name']})
   if 'model' in fields:
    option={'value':row['series_id'],'label':row['series_name']}
    if 'brand' in fields:option['brand']=brand_value
    models.setdefault(row['series_id'],option)
  options={}
  if 'brand' in fields:options['brand']=list(brands.values())
  if 'model' in fields:options['model']=list(models.values())
  return options
 def _lead_list(self,c,a,rows,page_size,page,q,status,channel,filters):
  at=now();rows=[self._effective_lead(row,at) for row in rows]
  override=self._lead_field_override(c,a)
  columns=self._lead_column_definitions(override)
  fields={col['key'] for col in columns}
  dimensions={
   'city':('city_code',lambda r:{'310100':'上海市','110100':'北京市'}.get(r['city_code'],r['city_code'])),
   'brand':('filter_brand_id',lambda r:r['filter_brand']),
   'model':('series_id',lambda r:r['filter_model'] or r['series_id']),
   'supplier':('filter_supplier_id',lambda r:r['filter_supplier']),
   'acquisition_channel':('acquisition_channel',lambda r:r['acquisition_channel']),
   'channel':('channel_id',lambda r:r['filter_channel']),
  }
  options=self._vehicle_filter_options(c,a,fields) if fields & {'brand','model'} else {}
  for key,(value_key,label) in dimensions.items():
   if key not in fields or key in ('brand','model'):continue
   choices={r[value_key]:label(r) for r in rows if r[value_key] and label(r)}
   options[key]=[{'value':value,'label':name} for value,name in sorted(choices.items(),key=lambda item:(item[1],item[0]))]
  sale_records={}
  if fields & {'sold_media','project'}:
   sale_records={row['id']:self._sale_records(c,row['id']) for row in rows}
   if 'sold_media' in fields:
    media={row['id']:row['name'] for row in c.execute("SELECT id,name FROM partners WHERE org_id=? AND role='MEDIA'",(a['org_id'],))}
    options['sold_media']=[{'value':key,'label':label} for key,label in sorted(media.items(),key=lambda item:(item[1],item[0]))]
   if 'project' in fields:
    projects={record['project'] for records in sale_records.values() for record in records}
    options['project']=[{'value':key,'label':label} for key,label in [('KA','KA'),('MEMBER','会员'),('UNKNOWN','待补录')] if key!='UNKNOWN' or key in projects]
  media_filter=filters.get('sold_media','') if 'sold_media' in fields else ''
  project_filter=filters.get('project','') if 'project' in fields else ''
  if media_filter or project_filter:
   rows=[row for row in rows if any(
      (not media_filter or record['media_id']==media_filter) and
      (not project_filter or record['project']==project_filter)
      for record in sale_records.get(row['id'],[]))]
  selected={**filters,'channel':channel}
  for key,(value_key,_) in dimensions.items():
   if key in fields and selected.get(key):
    rows=[r for r in rows if r[value_key]==selected[key]]
  if status and ('status' in fields or override is None):rows=[r for r in rows if r['status']==status]
  if 'collected_at' in fields:
   try:
    start=date.fromisoformat(filters['from']) if filters.get('from') else None
    end=date.fromisoformat(filters['to']) if filters.get('to') else None
   except ValueError:raise Fault('LEAD_DATE_INVALID','采集时间请填写有效日期') from None
   if start and end and start>end:raise Fault('LEAD_DATE_INVALID','采集开始日期不能晚于结束日期')
   if start or end:
    def within_range(row):
     collected=parse(row['collected_at']).astimezone(SH).date()
     return (start is None or collected>=start) and (end is None or collected<=end)
    rows=[r for r in rows if within_range(r)]
  if q:
   query=q.casefold()
   rows=[r for r in rows if any(query in str(v).casefold() for v in self._lead_columns(c,a,r,fields).values())]
  items=[]
  for row in rows[(max(1,page)-1)*page_size:max(1,page)*page_size]:
   item={'id':row['id'],'version':row['version'],**self._lead_columns(c,a,row,fields)}
   if self._lead_field_override(c,a) is None or 'status' in fields:
    item['status']=row['status']
    item['reason']=row['reason']
    item['consent_count']=c.execute('SELECT count(*) FROM consents WHERE lead_id=?',(row['id'],)).fetchone()[0]
   items.append(item)
  return {'columns':columns,'filter_options':options,'items':items,'total':len(rows),'page':page,'page_size':page_size,'has_more':page*page_size<len(rows)}
 def _lead_rows(self,c,a):
  return [dict(r) for r in c.execute("""SELECT l.*,v.brand_id AS filter_brand_id,v.brand_name AS filter_brand,
        v.series_name AS filter_model,p.id AS filter_supplier_id,p.name AS filter_supplier,
        CASE ch.type WHEN 'ADS' THEN '投放' WHEN 'PURCHASE' THEN '采买'
        WHEN 'TMK' THEN 'TMK' ELSE ch.type END AS filter_channel
      FROM leads l LEFT JOIN catalog v ON v.series_id=l.series_id
      LEFT JOIN channels ch ON ch.id=l.channel_id AND ch.org_id=l.org_id
      LEFT JOIN partners p ON p.id=ch.source_partner_id AND p.org_id=l.org_id
      WHERE l.org_id=? ORDER BY l.created_at DESC,l.id DESC""",(a['org_id'],))]
 def list(self,a,table,page_size=20,page=1,q='',status='',channel='',media='',lead_filters=None):
  self.require(a,'read')
  if table=='audit_log':self.require(a,'settings')
  if page_size not in [20,50,100]:raise Fault('PAGE_SIZE_INVALID','每页只能选择20、50、100条')
  if table not in ['leads','demands','deliveries','audit_log','callback_events','imports','privacy_requests','exports']:raise Fault('NOT_FOUND','页面不存在',404)
  c=self.connect()
  try:
   if table=='leads':
    rows=self._lead_rows(c,a)
    return self._lead_list(c,a,rows,page_size,page,q,status,channel,lead_filters or {})
   visibility=" AND COALESCE(demands.sync_state,'')!='REMOVED' AND "+demand_project_condition('demands') if table=='demands' else ''
   rows=[dict(r) for r in c.execute('SELECT * FROM '+table+' WHERE org_id=?'+visibility+' ORDER BY '+('received_at' if table=='callback_events' else 'created_at')+' DESC,id DESC',(a['org_id'],))]
   if status:rows=[r for r in rows if r.get('status',r.get('send_status'))==status]
   if channel:rows=[r for r in rows if r.get('channel_id')==channel]
   if media:rows=[r for r in rows if r.get('media_id')==media]
   if q:rows=[r for r in rows if q.lower() in canonical({k:v for k,v in r.items() if 'cipher' not in k and 'hmac' not in k and 'snapshot' not in k}).lower()]
   total=len(rows);rows=rows[(max(1,page)-1)*page_size:max(1,page)*page_size]
   items=[self._public(c,a,table,r) for r in rows];return {'items':items,'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}
  finally:c.close()
 def _public(self,c,a,table,r):
  r=self._effective_lead(r) if table=='leads' else dict(r)
  if table=='demands':
   r.update(self.demand_progress(r));r=self._sync_demand_public(c,r);r['demand_no']=external_demand_no(r)
  if table=='demands':
   r['yiche']=json.loads(r.pop('yiche_config'))
  if table=='leads':
   yc=self._yiche_lead(r);r['yiche']={k:yc[k] for k in yiche.LEAD_IDS if k in yc};r['yiche_optional_present']=[k for k in yiche.LEAD_TEXT if k in yc]
   phone=self.vault.decrypt(r['phone_cipher']);name=self.vault.decrypt(r['name_cipher']);r['phone']=phone[:6]+'****'+phone[-4:] if phone else '已删除';r['name']=customer_label(name,r['customer_title']) or ('已删除' if r['status']=='DELETED' else '—')
   r['gender']={'MALE':'男','FEMALE':'女','UNKNOWN':'未登记'}[r['gender']]
   r['consents']=[dict(x) for x in c.execute('SELECT recipient_id,review_status,revoked_at,expires_at,evidence_ref FROM consents WHERE lead_id=?',(r['id'],))]
   override=self._lead_field_override(c,a)
   fields=set(dict(LEAD_COLUMNS) if override is None else override)
   r.update(self._lead_columns(c,a,r,fields))
   hidden={
    'lead_no':('lead_no',),
    'customer':('customer','name','customer_title','gender'),
    'phone':('phone',),
    'city':('city','city_code'),
    'brand':('brand',),
    'model':('model','series_id'),
    'supplier':('supplier','supplier_id'),
    'channel':('channel','channel_id'),
    'acquisition_channel':('acquisition_channel',),
    'collected_at':('collected_at','expires_at'),
    'status':('status','reason'),
    'source_status':('source_status',),
    'sales_count':('sales_count',),
   }
   for key,properties in hidden.items():
    if key not in fields and not (key in ('status','channel') and override is None):
     for property_name in properties:r.pop(property_name,None)
   if 'city' not in fields:r['yiche'].pop('cityId',None)
   if 'model' not in fields:
    for key in ('serialId','manufacturerId','carId'):r['yiche'].pop(key,None)
  if table=='deliveries':
   submission=c.execute('SELECT callback_code code,callback_description description FROM yiche_submissions WHERE delivery_id IN (SELECT id FROM deliveries WHERE org_id=? AND id=?)',(a['org_id'],r['id'])).fetchone()
   agency=c.execute('SELECT code,description,result_code FROM zmh_submissions WHERE org_id=? AND delivery_id=?',(a['org_id'],r['id'])).fetchone()
   callback=agency if agency and agency['code'] else submission
   if callback and callback['code']:
    code=callback['code'];mapped=self._yiche_code_map(c,a['org_id']).get(code)
    # 回调状态保留原判定；码表编辑仅更新解释，不回写历史账本。
    description=mapped[1] if mapped and (r['validity_status']!='INVALID' or mapped[0]=='INVALID') else callback['description'] or '未配置失败原因（code='+code+'）'
    r['yiche_code']={'code':code,'description':description}
   l=c.execute('SELECT lead_no FROM leads WHERE id=?',(r['lead_id'],)).fetchone()
   fields=self._lead_field_override(c,a)
   if fields is None or 'lead_no' in fields:r['lead_no']=l['lead_no']
   if fields is not None and 'project' not in fields:r.pop('sale_project',None)
   r['demand_no']=external_demand_no(json.loads(r['demand_snapshot']));r['hops']=[dict(x) for x in c.execute('SELECT * FROM hops WHERE delivery_id=? ORDER BY hop_no',(r['id'],))]
  if 'finance' not in PERMS.get(a['role'],set()):
   for k in ['cost_fen','unit_price_fen','price_fen']:r.pop(k,None)
  for k in list(r):
   if any(x in k for x in ['cipher','hmac','token_hash','snapshot','payload_hash']):r.pop(k,None)
  if table=='callback_events':r['body']=json.loads(r['body'])
  return r
 def detail(self,a,table,id):
  self.require(a,'read');c=self.connect()
  try:
   r=self.one(c,table,id,a);out=self._public(c,a,table,r)
   if table=='leads':
    if 'channel_id' in out:
     keys=['channel_id','source_record_id','status']+(['created_at'] if 'collected_at' in out else [])
     out['ingestions']=[{k:x[k] for k in keys} for x in c.execute('SELECT channel_id,source_record_id,status,created_at FROM ingestions WHERE lead_id=?',(id,))]
    override=self._lead_field_override(c,a)
    fields=set(dict(LEAD_COLUMNS) if override is None else override)
    out['source_details']=self._source_details(c,a,id,fields|({'channel','source_status'} if override is None else set()))
    sale_fields={'sold_media':'media_name','project':'project','sold_at':'sold_at'}
    out['sale_history']=[{key:row[value] for key,value in sale_fields.items() if key in fields}
                         for row in self._sale_history(c,id)] if fields & sale_fields.keys() else []
    if 'sales_count' in out:
     out['deliveries']=[self._public(c,a,'deliveries',x) for x in c.execute('SELECT * FROM deliveries WHERE lead_id=?',(id,))]
   if table=='deliveries':
    out['attempts']=[dict(x) for x in c.execute('SELECT * FROM attempts WHERE delivery_id=? ORDER BY attempt_no',(id,))];out['callbacks']=[dict(x) for x in c.execute('SELECT event_id,event_type,event_version,occurred_at,received_at,status,error FROM callback_events WHERE delivery_id=? ORDER BY received_at',(id,))];out['ledger']=[dict(x) for x in c.execute('SELECT * FROM quota_ledger WHERE delivery_id=?',(id,))]
    payload=json.loads(self.vault.decrypt(r['payload_cipher'])) if r['payload_cipher'] else {};payload['customer']={'phone':'已脱敏','name':'已脱敏'};payload.pop('yiche_lead',None);out['outbound_payload']=payload;
    if 'yiche_request' in payload:
     payload['yiche_request']['param']={k:('已加密 · 模拟' if k in yiche.ENCRYPTED else v) for k,v in payload['yiche_request']['param'].items()};payload['orderId']=id
    out['demand_snapshot_public']={k:v for k,v in json.loads(r['demand_snapshot']).items() if k not in ['unit_price_fen','created_by']}
    out['demand_snapshot_public']['demand_no']=external_demand_no(out['demand_snapshot_public'])
    restricted=self._lead_field_override(c,a)
    if restricted is not None:
     if 'lead_no' not in restricted:payload.pop('lead_ref',None)
     if 'collected_at' not in restricted:payload.pop('collected_at',None)
     vehicle=payload.get('vehicle',{})
     media_codes=payload.get('media_dictionary',{});media_config=payload.get('yiche_config',{})
     if 'city' not in restricted:
      for key in ('city_id','city_name'):media_codes.pop(key,None)
      media_config.pop('cityId',None)
     if 'model' not in restricted:
      for key in ('series_id','series_name','factory_id','factory_name'):media_codes.pop(key,None)
      for key in ('serialId','manufacturerId','carId','csId2'):media_config.pop(key,None)
     if 'brand' not in restricted:
      media_codes.pop('brand_id',None);media_codes.pop('brand_name',None);media_codes.pop('factory_name',None);media_config.pop('brandId',None)
     if 'city' not in restricted:
      vehicle.pop('city',None)
      payload.get('yiche_request',{}).get('param',{}).pop('cityId',None)
     if 'model' not in restricted:
      vehicle.pop('series',None)
      param=payload.get('yiche_request',{}).get('param',{})
      for key in ('serialId','manufacturerId','carId','csId2'):param.pop(key,None)
     if not {'city','brand','model'}.issubset(restricted):out.pop('demand_snapshot_public',None)

   return out
  finally:c.close()
 def reports(self,a):
  self.require(a,'read');c=self.connect()
  try:
   ds=[dict(x) for x in c.execute('SELECT d.*,l.channel_id FROM deliveries d JOIN leads l ON l.id=d.lead_id WHERE d.org_id=?',(a['org_id'],))];ls=[dict(x) for x in c.execute('SELECT * FROM leads WHERE org_id=?',(a['org_id'],))];sent=[d for d in ds if d['first_sent_at']]
   def metrics(xs):
    n=len(xs);return {'sent':n,'acked':sum(d['send_status']=='ACKED' for d in xs),'stored':sum(d['storage_status']=='STORED' for d in xs),'valid':sum(d['validity_status']=='VALID' for d in xs),'invalid':sum(d['validity_status']=='INVALID' for d in xs),'pending':sum(d['validity_status']=='PENDING' for d in xs),'unknown':sum(d['send_status']=='UNKNOWN' for d in xs),'agency_reported':sum(d['evidence_level']=='AGENCY_REPORTED' for d in xs)}
   groups=[]
   for ch,med in sorted({(d['channel_id'],d['media_id']) for d in sent}):groups.append({'channel_id':ch,'media_id':med,**metrics([d for d in sent if d['channel_id']==ch and d['media_id']==med])})
   out={'leads':len(ls),'ready':sum(self._effective_lead(l)['status']=='READY' for l in ls),'ingestions':c.execute('SELECT count(*) FROM ingestions WHERE org_id=?',(a['org_id'],)).fetchone()[0],'deliveries':len(ds),'unique_sent_leads':len({d['lead_id'] for d in sent}),'metrics':metrics(sent),'groups':groups,'multisale':round(len(sent)/max(1,len({d['lead_id'] for d in sent})),2),'as_of':now()}
   out['matchable']=sum(1 for _ in self._matchable_lead_rows(c,a))
   # matchable旧字段保留完整可分发含义，新增两项供总览明确展示。
   out['dispatchable']=out['matchable']
   out['business_matchable']=sum(1 for _ in self._business_match_lead_rows(c,a))
   # Attribute each demand to its purchasing entity, so agency forwarding is
   # counted once rather than again under the destination media.
   from synced_demands import norm
   demand_rows=c.execute("SELECT d.status,CASE WHEN d.sync_state='ENDED' THEN 0 ELSE COALESCE(d.api_submit_limit,d.target_total) END AS target_total,d.quantity_basis,d.api_vehicles,p.role,COALESCE(NULLIF(trim(d.match_brand),''),cat.brand_name) AS brand FROM demands d JOIN partners p ON p.id=d.buyer_id AND p.org_id=d.org_id LEFT JOIN catalog cat ON cat.series_id=d.series_id WHERE d.org_id=? AND COALESCE(d.sync_state,'')!='REMOVED' AND "+demand_project_condition('d'),(a['org_id'],)).fetchall()
   partner_counts={r['role']:r['n'] for r in c.execute("SELECT role,count(*) AS n FROM partners WHERE org_id=? AND role IN ('MEDIA','AGENCY') GROUP BY role",(a['org_id'],))}
   out['overview']={'media_count':partner_counts.get('MEDIA',0),'agency_count':partner_counts.get('AGENCY',0),'media_demands':sum(r['role']=='MEDIA' for r in demand_rows),'agency_demands':sum(r['role']=='AGENCY' for r in demand_rows),'total_demands':len(demand_rows),'brand_count':len({norm(brand) for r in demand_rows for brand in ([x['brand'] for x in json.loads(r['api_vehicles'])] if r['api_vehicles']!='[]' else [r['brand']]) if brand and norm(brand)}),'active_demands':sum(r['status']=='ACTIVE' for r in demand_rows)}
   out['overview']['order_quantities']=summarize_order_quantities((r['quantity_basis'],r['target_total']) for r in demand_rows if r['target_total']>0)
   if 'finance' in PERMS[a['role']]:out['income_fen']=sum(d['price_fen'] for d in sent if d['settlement_status'] in ['ELIGIBLE','CONFIRMED']);out['cost_fen']=sum(l['cost_fen'] or 0 for l in ls if l['id'] in {d['lead_id'] for d in sent});out['unknown_cost_leads']=sum(l['cost_fen'] is None for l in ls)
   return out
  finally:c.close()
 def integrity(self,a):
  self.require(a,'read');c=self.connect()
  try:
   checks=[]
   for d in c.execute('SELECT * FROM demands WHERE org_id=?',(a['org_id'],)):
    ledger=c.execute('SELECT COALESCE(sum(reserved_delta),0),COALESCE(sum(valid_delta),0) FROM quota_ledger WHERE demand_id=?',(d['id'],)).fetchone();counts=c.execute("SELECT sum(quota_state='RESERVED'),sum(quota_state='VALID') FROM deliveries WHERE demand_id=?",(d['id'],)).fetchone();expect=[counts[0] or 0,counts[1] or 0];actual=[d['reserved_count'],d['valid_count']]
    checks.append({'name':d['demand_no']+' 配额账本与交付一致','ok':list(ledger)==actual==expect,'actual':actual,'expected':expect})
   checks.append({'name':'所有外键关联完整','ok':len(c.execute('PRAGMA foreign_key_check').fetchall())==0})
   checks.append({'name':'媒体重复分发检查','ok':not c.execute("SELECT lead_id,media_id,count(*) FROM deliveries WHERE org_id=? AND send_status NOT IN ('CANCELED','FAILED') GROUP BY lead_id,media_id HAVING count(*)>1",(a['org_id'],)).fetchall()})
   return {'checks':checks,'passed':all(x['ok'] for x in checks)}
  finally:c.close()
 def reveal(self,a,id,purpose):
  self.require(a,'reveal')
  if not purpose.strip():raise Fault('PURPOSE_REQUIRED','请填写查看用途')
  with self.tx() as c:
   fields=self._lead_field_override(c,a)
   if fields is not None and 'customer' not in fields:raise Fault('FORBIDDEN','当前账号无权查看客户字段',403)
   l=self.one(c,'leads',id,a);self.audit(c,a,'REVEAL',id,{'purpose':purpose});return {'phone':self.vault.decrypt(l['phone_cipher']),'name':self.vault.decrypt(l['name_cipher'])}
 def _record_excel_error(self,a,error_type,code):
  with self.tx() as c:self.audit(c,a,'IMPORT_FILE_REJECTED','excel',{'exception_type':error_type,'code':code})
 def import_preview(self,a,filename,content,channel=None,sheet=None,mapping=None,supplier_id=None):
  self.require(a,'write')
  if len(content)>10*1024*1024:raise Fault('FILE_TOO_LARGE','文件不得大于10MB')
  format_notice=''
  filename=filename.strip();sheet=str(sheet).strip() if sheet is not None else None
  if filename.lower().endswith('.xlsx'):
   from openpyxl import load_workbook
   from excel_intake import read_xml_values,ExcelIntakeError
   wb=None
   try:
    z=zipfile.ZipFile(io.BytesIO(content))
    if sum(x.file_size for x in z.infolist())>50*1024*1024:raise Fault('FILE_TOO_LARGE','表格解压后过大')
    z.close();wb=load_workbook(io.BytesIO(content),read_only=True,data_only=False)
    if sheet and sheet not in wb.sheetnames:raise Fault('EXCEL_SHEET_NOT_FOUND','工作表名称不存在，可用工作表：'+'、'.join(wb.sheetnames))
    ws=wb[sheet] if sheet else wb.worksheets[0];ws.reset_dimensions();vals=[]
    for row in ws.iter_rows():
     vals.append([('=FORMULA_REJECTED' if x.data_type=='f' else x.value) for x in row])
     if len(vals)>50001:raise Fault('TOO_MANY_ROWS','最多50000行')
   except Fault:raise
   except Exception as error:
    try:
     vals=read_xml_values(content,sheet)
     if not zipfile.is_zipfile(io.BytesIO(content)):format_notice='文件扩展名为.xlsx，实际内容为CSV文本；已自动识别并完成预检。'
    except ExcelIntakeError as e:
     self._record_excel_error(a,type(error).__name__,e.code)
     raise Fault(e.code,e.message) from None
   finally:
    if wb is not None:wb.close()
  elif filename.lower().endswith('.csv'):
   try:vals=list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))
   except:raise Fault('FILE_INVALID','CSV请使用UTF-8编码')
  else:raise Fault('FILE_TYPE','仅支持.xlsx和.csv，不支持.xls和宏文件')
  if not vals or len(vals)<2:raise Fault('FILE_EMPTY','文件缺少数据行')
  if len(vals)>50001:raise Fault('TOO_MANY_ROWS','最多50000行')
  aliases={'来源记录编号':'source_record_id','客户姓名':'name','姓名':'name','性别':'gender','称谓':'customer_title','手机号':'phone','电话':'phone','品牌':'brand_name','品牌名称':'brand_name','车型':'model','车系名称':'model','城市':'city','获客渠道':'acquisition_channel','是否售卖':'is_sold','售卖媒体':'sale_media','项目':'sale_project','售卖时间':'sold_at','车系':'series_id','城市编码':'city_code','采集时间':'collected_at','授权接收方':'recipients','授权证据':'evidence_ref','成本分':'cost_fen'};headers=[str(x or '').strip() for x in vals[0]];mapping=mapping or {};rows=[];preview=[];seen=set()
  c=self.connect()
  try:
   if supplier_id:
    if channel and self.one(c,'channels',channel,a)['source_partner_id']!=supplier_id:raise Fault('SUPPLIER_CHANNEL_CONFLICT','所选供应商与来源渠道不一致')
    channel=self._supplier_intake_channel(c,a,supplier_id)
   elif not channel:raise Fault('SUPPLIER_REQUIRED','请选择线索供应商')
   self.one(c,'channels',channel,a)
   for idx,values in enumerate(vals[1:],2):
    if not any(v is not None and str(v).strip() for v in values):continue
    row={mapping.get(h,aliases.get(h,h)):str(v if v is not None else '').strip() for h,v in zip(headers,values)}
    row['yiche']={key:row.pop(k) for k,key in yiche.LEAD_HEADERS.items() if k in row}
    for k in (*yiche.LEAD_IDS,*yiche.LEAD_TEXT):
     if k in row:row['yiche'][k]=row.pop(k)
    row.setdefault('source_record_id',digest(content)[:16]+':'+str(sheet or 0)+':'+str(idx));row['source_record_id']=row['source_record_id'] or digest(content)[:16]+':'+str(idx)
    row['recipients']=[v.strip() for v in row.get('recipients','').replace('，',',').split(',') if v.strip()];err=[]
    phone=row.get('phone','').removeprefix('+86')
    try:row=self._entry_fields(c,a,row,register=False)
    except Fault as error:err.append(error.message)
    phone=str(row.get('phone','')).removeprefix('+86')
    try:
     col=parse(row.get('collected_at') or now())
     if col>parse(now())+timedelta(minutes=5):err.append('采集时间晚于当前时间')
    except:err.append('采集时间格式错误')
    try:row['yiche']=yiche.lead_fields(row['yiche'])
    except ValueError as e:err.append(str(e))
    if any(str(v).startswith('=') for v in [*row.values(),*row['yiche'].values()] if isinstance(v,str)):err.append('公式不可导入，请粘贴为值')
    if row.get('gender','') not in ('','未登记','男','女','UNKNOWN','MALE','FEMALE'):err.append('性别只能是男、女或未登记')
    if row.get('customer_title','') not in ('','先生','女士'):err.append('称谓只能是先生、女士或留空')
    if any(not c.execute('SELECT 1 FROM partners WHERE id=? AND org_id=?',(r,a['org_id'])).fetchone() for r in row['recipients']):err.append('未知授权接收方')
    try:
     if row.get('cost_fen') and int(row['cost_fen'])<0:err.append('成本不可为负')
    except:err.append('成本必须为整数分')
    fp=self.vault.fingerprint(a['org_id'],'+86'+phone);dup=bool(c.execute('SELECT 1 FROM leads WHERE org_id=? AND phone_hmac=? AND series_id=? AND collected_at>=?',(a['org_id'],fp,row.get('series_id'),later(days=-c.execute('SELECT dedup_window_days FROM settings WHERE org_id=?',(a['org_id'],)).fetchone()[0]))).fetchone()) or (phone,row.get('series_id')) in seen
    if not err:seen.add((phone,row.get('series_id')))
    st='ERROR' if err else ('DUPLICATE' if dup else 'VALID');rows.append(row);preview.append({'row':idx,'status':st,'errors':err,'phone':phone[:3]+'****'+phone[-4:] if len(phone)>7 else '***','source_record_id':row['source_record_id']})
  finally:c.close()
  id=uid('im_');out={'id':id,'filename':filename,'format_notice':format_notice,'headers':headers,'rows':preview,'total':len(preview),'valid':sum(x['status']=='VALID' for x in preview),'duplicates':sum(x['status']=='DUPLICATE' for x in preview),'errors':sum(x['status']=='ERROR' for x in preview)}
  with self.tx() as c:c.execute('INSERT INTO imports VALUES(?,?,?,?,?,?,?,?,?,?)',(id,a['org_id'],channel,filename,self.vault.encrypt(canonical(rows)),canonical(out),'PREVIEW',a['id'],None,now()));self.audit(c,a,'IMPORT_PREVIEW',id,{'rows':len(rows)})
  return out
 def import_commit(self,a,id,mode):
  self.require(a,'write')
  with self.tx() as c:
   im=self.one(c,'imports',id,a)
   if im['status']=='COMPLETED':return json.loads(im['result'])
   if mode not in ['ALL_VALID','VALID_ROWS']:raise Fault('MODE_INVALID','请指定导入模式')
   pv=json.loads(im['preview'])
   if mode=='ALL_VALID' and pv['errors']:raise Fault('IMPORT_HAS_ERRORS','存在错误行，请修正或选择仅导入合格行')
   rows=json.loads(self.vault.decrypt(im['rows_cipher']));items=[]
   for row,v in zip(rows,pv['rows']):
    if v['status']=='ERROR':items.append({'row':v['row'],'status':'SKIPPED','errors':v['errors']});continue
    row=self._entry_fields(c,a,row)
    items.append({'row':v['row'],**self._ingest(c,a,im['channel_id'],row,direct=True)})
   result={'items':items,'created':sum(x.get('status')=='CREATED' for x in items),'duplicates':sum(x.get('status')=='DUPLICATE' for x in items),'skipped':sum(x.get('status')=='SKIPPED' for x in items)}
   c.execute("UPDATE imports SET status='COMPLETED',result=? WHERE id=?",(canonical(result),id));self.audit(c,a,'IMPORT_COMMIT',id,{'created':result['created']});return result
 def export_request(self,a,purpose):
  self.require(a,'export')
  if not purpose.strip():raise Fault('PURPOSE_REQUIRED','请填写导出用途')
  with self.tx() as c:
   id=uid('ex_');c.execute('INSERT INTO exports(id,org_id,created_by,purpose,status,created_at) VALUES(?,?,?,?,?,?)',(id,a['org_id'],a['id'],purpose,'PENDING',now()));self.audit(c,a,'EXPORT_REQUEST',id);return {'id':id}
 def export_approve(self,a,id):
  self.require(a,'export_approve')
  with self.tx() as c:
   x=self.one(c,'exports',id,a)
   if x['created_by']==a['id']:raise Fault('SELF_APPROVAL','不可审批本人导出申请',403)
   if x['status']!='PENDING':raise Fault('STATE_CONFLICT','申请已处理',409)
   c.execute("UPDATE exports SET status='APPROVED',approved_by=?,expires_at=? WHERE id=?",(a['id'],time.time()+900,id));self.audit(c,a,'EXPORT_APPROVE',id);return {'ok':True}
 def export_download(self,a,id):
  with self.tx() as c:
   x=self.one(c,'exports',id,a)
   if x['created_by']!=a['id']:raise Fault('NOT_FOUND','文件不存在或不属于此账号',404)
   if x['status']!='APPROVED' or x['used'] or (x['expires_at'] or 0)<time.time():raise Fault('DOWNLOAD_EXPIRED','未批准、已下载或下载权限已过期',403)
   out=io.StringIO();w=csv.writer(out)
   w.writerow(['导出用途',"'"+x['purpose'] if x['purpose'].startswith(('=','+','-','@')) else x['purpose'],'申请人',a['id'],'下载时间',now()])
   fields=self._lead_field_override(c,a)
   columns=self._lead_column_definitions(fields)
   w.writerow([col['label'] for col in columns])
   for row in c.execute('SELECT * FROM leads WHERE org_id=? ORDER BY lead_no',(a['org_id'],)):
    r=self._lead_columns(c,a,row,set(col['key'] for col in columns))
    vals=[r.get(col['key'],'') for col in columns]
    w.writerow(["'"+str(v) if str(v).startswith(('=','+','-','@')) else v for v in vals])
   c.execute('UPDATE exports SET used=1 WHERE id=?',(id,));self.audit(c,a,'EXPORT_DOWNLOAD',id);return '\ufeff'+out.getvalue()
 def maintenance(self,a):
  self.require(a,'engineering')
  with self.tx() as c:
   for d in c.execute("SELECT * FROM deliveries WHERE org_id=? AND first_sent_at IS NOT NULL AND validity_status='PENDING'",(a['org_id'],)).fetchall():
    if parse(d['first_sent_at'])<parse(now())-timedelta(hours=72):c.execute("UPDATE deliveries SET callback_status='TIMEOUT',error='CALLBACK_TIMEOUT' WHERE id=?",(d['id'],))
   c.execute("UPDATE leads SET status='EXPIRED' WHERE org_id=? AND status='READY' AND expires_at<=?",(a['org_id'],now()))
  return self.integrity(a)
 def backup(self,a):
  self.require(a,'settings');path=self.root/('backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite');src=self.connect();dst=sqlite3.connect(path);src.backup(dst);dst.close();src.close()
  # Protect local backup at rest with the same test vault; do not leave plaintext duplicate.
  encrypted=self.vault.f.encrypt(path.read_bytes());path.unlink();enc=path.with_suffix('.sqlite.enc');enc.write_bytes(encrypted);os.chmod(enc,0o600)
  with self.tx() as c:self.audit(c,a,'BACKUP_CREATE',enc.name)
  return {'file':enc.name,'encrypted':True,'note':'本地测试备份；密钥须另行安全备份'}
 def restore(self,a,filename):
  self.require(a,'settings')
  if '/' in filename or not filename.startswith('backup-') or not filename.endswith('.sqlite.enc'):raise Fault('FILE_INVALID','备份名称无效')
  # Called offline by CLI/tests, never exposed as a destructive HTTP action.
  data=self.vault.f.decrypt((self.root/filename).read_bytes());tmp=self.root/'restore-tmp.sqlite';tmp.write_bytes(data)
  src=sqlite3.connect(tmp);dst=self.connect();src.backup(dst);dst.close();src.close();tmp.unlink();self._migrate();self.enforce_tombstones()
