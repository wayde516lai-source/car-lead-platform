"""Read-only source synchronization and reconciliation into live delivery demands."""
import json
import time
from datetime import timedelta
from contextlib import nullcontext
from domain import Fault,now,later,parse,uid
from security import canonical,digest
from demand_statistics import summarize_order_quantities, demand_has_ended, execution_days
from project_availability import SOURCE_PROJECT_AVAILABLE_SQL
from qq_sheet import QQSheetError,read_qq_sheet,sheet_url
from ka_demand_api import DemandProtocolError,read_demands
from synced_demands import source_order_quantity,source_execution_ended
from demand_source_deletion import require_current_source,delete_source

class DemandSync:
 def __init__(self,platform,fetcher=read_qq_sheet,api_fetcher=read_demands):self.p=platform;self.fetcher=fetcher;self.api_fetcher=api_fetcher
 def _source(self,c,a,id):return self.p.one(c,'demand_sync_sources',id,a)
 def _public_source(self,c,source):
  result=dict(source);result['headers']=json.loads(result['headers']);result['project_count']=c.execute('SELECT count(*) FROM demand_sync_projects WHERE source_id=? AND is_present=1',(result['id'],)).fetchone()[0]
  result['demand_counts']={r['status']:r['n'] for r in c.execute('SELECT d.status,count(*) AS n FROM demands d JOIN demand_sync_projects p ON p.id=d.external_project_id WHERE p.source_id=? AND p.is_present=1 GROUP BY d.status',(result['id'],))}
  result['running']=bool(result['running_until'] and result['running_until']>now())
  availability=c.execute('SELECT owner.enabled AS subject_enabled,p.enabled AS project_enabled FROM partners owner LEFT JOIN partner_projects p ON p.org_id=owner.org_id AND p.partner_id=owner.id AND p.project=? WHERE owner.org_id=? AND owner.id=?',(result['sale_project'],result['org_id'],result['partner_id'])).fetchone()
  result['subject_enabled']=bool(availability and availability['subject_enabled']);result['project_enabled']=bool(availability and availability['project_enabled']);result['available']=result['subject_enabled'] and result['project_enabled']
  result['replaced_sources']=[dict(row) for row in c.execute('WITH RECURSIVE previous(id) AS (SELECT id FROM demand_sync_sources WHERE org_id=? AND replaced_by=? UNION SELECT s.id FROM demand_sync_sources s JOIN previous p ON s.replaced_by=p.id WHERE s.org_id=?) SELECT id,name,url FROM demand_sync_sources WHERE org_id=? AND id IN (SELECT id FROM previous)',(result['org_id'],result['id'],result['org_id'],result['org_id']))]
  if result['source_kind']=='API_KA':
   config=c.execute('SELECT auth_mode,token_cipher FROM ka_demand_sources WHERE source_id=? AND org_id=?',(result['id'],result['org_id'])).fetchone()
   result['auth_mode']=config['auth_mode'] if config else 'UNCONFIRMED';result['has_token']=bool(config and config['token_cipher'])
  result.pop('run_id',None);result.pop('running_until',None);return result
 def delete(self,a,source_id,version):
  """按当前来源版本逻辑删除整份需求，保留历史交付及原始记录。"""
  return delete_source(self,a,source_id,version)
 def sources(self,a):
  self.p.require(a,'read');c=self.p.connect()
  try:return [self._public_source(c,r) for r in c.execute('SELECT * FROM demand_sync_sources WHERE org_id=? AND replaced_by IS NULL AND deleted_at IS NULL ORDER BY created_at,id',(a['org_id'],))]
  finally:c.close()
 def validate_config(self,c,a,data,old=None):
  """Validate shared subject/media/project configuration for link and file sources."""
  interval=data.get('interval_minutes',old['interval_minutes'] if old else 30);enabled=data.get('enabled',old['enabled'] if old else 1)
  if type(interval) is not int or not 1<=interval<=1440 or type(enabled) is not int or enabled not in (0,1):raise Fault('SYNC_CONFIG_INVALID','同步频率须为1～1440分钟，开关须为0或1')
  media=data.get('media_id',old['media_id'] if old else None) or None;project=data.get('sale_project',old['sale_project'] if old else 'UNKNOWN')
  partner=data.get('partner_id',old['partner_id'] if old else None) or None
  if not partner:raise Fault('SYNC_PARTNER_REQUIRED','请选择合作主体')
  subject=self.p.one(c,'partners',partner,a)
  if subject['role'] not in ('MEDIA','AGENCY'):raise Fault('SYNC_CONFIG_INVALID','合作主体请选择线索供应中的媒体或乙方')
  if not media:raise Fault('SYNC_MEDIA_REQUIRED','请选择最终媒体')
  if self.p.one(c,'partners',media,a)['role']!='MEDIA':raise Fault('SYNC_CONFIG_INVALID','最终媒体请选择合作角色为媒体的主体')
  if project not in ('KA','MEMBER'):raise Fault('SYNC_PROJECT_REQUIRED','请选择合作项目（会员或KA）')
  project_state=c.execute('SELECT enabled FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],partner,project)).fetchone()
  if not project_state:raise Fault('SYNC_PROJECT_REQUIRED','该主体尚未开通所选项目，请先在线索供应中添加项目')
  if not subject['enabled'] or not project_state['enabled']:raise Fault('PROJECT_PAUSED','该主体或合作项目已停用，请先启用后添加或调整需求')
  # A legal entity may operate both member and KA projects independently.
  if subject['role']=='AGENCY':
   links=[r[0] for r in c.execute('SELECT media_id FROM agency_final_media WHERE org_id=? AND agency_id=?',(a['org_id'],partner))]
   if links and media not in links:raise Fault('SYNC_CONFIG_INVALID','最终媒体与所选乙方关联的媒体不一致，请先在交付路由中配置关联')
  name=str(data.get('name',old['name'] if old else '')).strip()[:100]
  if not name:raise Fault('SYNC_CONFIG_INVALID','请填写来源名称')
  route=data.get('route_id',old['route_id'] if old else None) or None
  basis=data.get('quantity_basis',old['quantity_basis'] if old else 'DAILY')
  if basis not in ('DAILY','TOTAL'):raise Fault('SYNC_CONFIG_INVALID','请选择每日需求量或项目时间内总需求量')
  if route:
   selected_route=self.p.one(c,'routes',route,a)
   if selected_route['media_id']!=media or selected_route['buyer_id']!=partner:raise Fault('SYNC_CONFIG_INVALID','交付路由须同时匹配合作主体和最终媒体')
  return interval,enabled,media,project,partner,name,route,basis
 def save(self,a,data,id=None,_connection=None,_file_id=None):
  self.p.require(a,'demand')
  if id and 'url' in data and _connection is None:
   conn=self.p.connect()
   try:prior=self._source(conn,a,id)
   finally:conn.close()
   if prior['source_kind']=='QQ_DOC':
    try:normalized,new_doc,new_tab=sheet_url(data['url'])
    except QQSheetError as error:raise Fault('SYNC_URL_INVALID',str(error)) from None
    if (new_doc,new_tab)!=(prior['doc_id'],prior['tab_id']):
     from demand_source_replacement import replace_source_link
     return replace_source_link(self,a,id,data)
    data={**data,'url':normalized}
  with (nullcontext(_connection) if _connection else self.p.tx()) as c:
   old=self._source(c,a,id) if id else None
   if old:require_current_source(old)
   if old and old['replaced_by']:raise Fault('SOURCE_REPLACED','该来源已被更换，请刷新使用当前链接',409)
   if old and data.get('version')!=old['version']:raise Fault('VERSION_CONFLICT','同步配置已修改，请刷新',409)
   if old and old['running_until'] and old['running_until']>now():raise Fault('SYNC_RUNNING','该来源正在同步，请结束后修改',409)
   if old and old['source_kind']=='EXCEL' and data.get('enabled',old['enabled'])!=0:raise Fault('FILE_SOURCE_NO_SYNC','Excel来源不能开启定时同步')
   data=dict(data)
   if old and any(key in data and data[key]!=old[key] for key in ('partner_id','media_id')) and 'route_id' not in data:data['route_id']=None
   interval,enabled,media,project,partner,name,route,basis=self.validate_config(c,a,data,old)
   remapping=bool(old and (any(old[key]!=value for key,value in [('partner_id',partner),('media_id',media),('sale_project',project),('quantity_basis',basis)]) or (route is not None and route!=old['route_id'])))
   if remapping:
    if c.execute('SELECT 1 FROM deliveries x JOIN demands d ON d.id=x.demand_id JOIN demand_sync_projects p ON p.id=d.external_project_id WHERE d.org_id=? AND p.source_id=? AND x.first_sent_at IS NOT NULL LIMIT 1',(a['org_id'],id)).fetchone():raise Fault('SOURCE_ASSIGNMENT_LOCKED','该来源已有发送记录，不能直接更换归属；请另建来源保留历史交付',409)
    if not route:
     routes=c.execute('SELECT id FROM routes WHERE org_id=? AND buyer_id=? AND media_id=?',(a['org_id'],partner,media)).fetchall()
     if len(routes)!=1:raise Fault('SYNC_ROUTE_REQUIRED','请选择匹配新主体与最终媒体的交付路由；没有路由时请先在合作与渠道新增')
     route=routes[0]['id']
   if old:
    if 'url' in data and data['url']!=old['url']:
     if old['source_kind']=='EXCEL':raise Fault('IMMUTABLE','Excel导入来源不可改为文档链接')
     try:normalized,new_doc,new_tab=sheet_url(data['url'])
     except QQSheetError as error:raise Fault('SYNC_URL_INVALID',str(error)) from None
     if (new_doc,new_tab)!=(old['doc_id'],old['tab_id']):raise Fault('IMMUTABLE','跨文档更换须先校验并读取新链接')
     c.execute('UPDATE demand_sync_sources SET url=? WHERE id=? AND org_id=?',(normalized,id,a['org_id']))
    c.execute('UPDATE demand_sync_sources SET name=?,interval_minutes=?,enabled=?,media_id=?,sale_project=?,next_run_at=?,version=version+1 WHERE id=?',(name,interval,enabled,media,project,later(hours=interval/60),id))
   else:
    if _file_id:url,doc,tab='excel://'+_file_id,_file_id,'IMPORT'
    else:
     try:url,doc,tab=sheet_url(data.get('url',''))
     except QQSheetError as e:raise Fault('SYNC_URL_INVALID',str(e)) from None
    existing=c.execute('SELECT deleted_at FROM demand_sync_sources WHERE org_id=? AND doc_id=? AND tab_id=?',(a['org_id'],doc,tab)).fetchone()
    if existing and existing['deleted_at']:raise Fault('SOURCE_DELETED','该工作表已有已删除来源，请使用更正后的新链接或联系管理员恢复',409)
    if existing:raise Fault('SYNC_DUPLICATE','该工作表已添加，请编辑已有来源',409)
    id=uid('ds_');c.execute('INSERT INTO demand_sync_sources(id,org_id,name,url,doc_id,tab_id,interval_minutes,enabled,media_id,sale_project,next_run_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(id,a['org_id'],name,url,doc,tab,interval,enabled,media,project,now(),now()))
   if _file_id:c.execute("UPDATE demand_sync_sources SET source_kind='EXCEL',enabled=0 WHERE id=?",(id,))
   c.execute('UPDATE demand_sync_sources SET route_id=?,quantity_basis=?,partner_id=? WHERE id=?',(route,basis,partner,id))
   linked=self.p._reconcile_source(c,a,id)
   if remapping and linked['errors']:raise Fault('SOURCE_REASSIGN_FAILED','归属修改后有需求无法映射，已整体回滚；请检查重复编号或需求字段')
   self.p.audit(c,a,'DEMAND_SYNC_CONFIG',id,{'interval_minutes':interval,'enabled':enabled,'partner_id':partner,'media_id':media,'sale_project':project})
   return self._public_source(c,self._source(c,a,id))
 def approve_duplicate(self,a,source_id,key):
  self.p.require(a,'demand');key=str(key).strip()
  if not key:raise Fault('SYNC_KEY_REQUIRED','请指定需求编号')
  with self.p.tx() as c:
   require_current_source(self._source(c,a,source_id))
   c.execute("INSERT OR REPLACE INTO demand_sync_duplicate_rules VALUES(?,?,?,?,?,?)",(source_id,a['org_id'],key,'LAST_ROW_WINS',a['id'],now()))
   self.p.audit(c,a,'DEMAND_SYNC_DUPLICATE_APPROVED',source_id,{'key':key,'policy':'LAST_ROW_WINS'})
 def _archive(self,c,a,source_id,row,reason):
  content=digest(canonical({'values':row['values'],'raw':row['raw_values']}))
  c.execute('INSERT OR IGNORE INTO demand_sync_project_history VALUES(?,?,?,?,?,?,?,?,?,?)',(uid('dph_'),a['org_id'],source_id,row['key'],row['row_number'],canonical(row['values']),canonical(row['raw_values']),content,reason,now()))
 def run(self,a,id,trigger='manual'):
  self.p.require(a,'demand');run=uid('dsr_')
  with self.p.tx() as c:
   source=self._source(c,a,id)
   require_current_source(source)
   if source['replaced_by']:raise Fault('SOURCE_REPLACED','该来源已被更换，请刷新使用当前链接',409)
   if source['source_kind']=='EXCEL':raise Fault('FILE_SOURCE_NO_SYNC','Excel需求请通过表格导入，不支持链接同步')
   rules={r['source_key']:r['policy'] for r in c.execute('SELECT * FROM demand_sync_duplicate_rules WHERE source_id=? AND org_id=?',(id,a['org_id']))}
   if source['running_until'] and source['running_until']>now():raise Fault('SYNC_RUNNING','该来源正在同步，请稍后刷新',409)
   if source['run_id']:
    c.execute("UPDATE demand_sync_runs SET status='FAILED',finished_at=?,error='上次同步中断，将重新读取' WHERE id=? AND status='RUNNING'",(now(),source['run_id']))
   c.execute('UPDATE demand_sync_sources SET run_id=?,running_until=?,last_attempt_at=?,last_error=? WHERE id=?',(run,later(hours=5/60),now(),'',id))
   c.execute('INSERT INTO demand_sync_runs(id,source_id,org_id,trigger_kind,status,started_at) VALUES(?,?,?,?,?,?)',(run,id,a['org_id'],trigger,'RUNNING',now()))
  try:
   if source['source_kind']=='API_KA':
    if trigger not in ('manual','scheduled'):raise DemandProtocolError('KA需求拉取触发方式无效')
    from ka_demand_sources import KaDemandSources
    result=KaDemandSources(self).fetch(a,source)
   else:result=self.fetcher(source['url'],duplicate_rules=rules) if self.fetcher is read_qq_sheet else self.fetcher(source['url'])
   records=result['records']
   # Guard injected/adapter results as well as the transport parser.
   if (not records and source['source_kind']!='API_KA') or len({r['key'] for r in records})!=len(records) or any(not r['key'] for r in records):raise QQSheetError('项目为空或需求编号重复，未更新已有数据')
   with self.p.tx() as c:
    current=self._source(c,a,id)
    require_current_source(current)
    if current['run_id']!=run:raise QQSheetError('同步锁已失效，未覆盖其他同步结果')
    stored=self._store_result(c,a,id,source,result,rules,run)
    if source['source_kind']=='API_KA' and stored['demands']['errors']:raise DemandProtocolError('接口需求映射失败，整批保留原数据；请检查路由或重复编号')
    return stored
  except Exception as e:
   from ka_demand_sources import next_daily_pull
   message=e.message if isinstance(e,Fault) and e.code=='SOURCE_DELETED' else str(e)[:350] if isinstance(e,(QQSheetError,DemandProtocolError)) else '同步处理失败，已有项目保持不变，请重试'
   with self.p.tx() as c:
    current=self._source(c,a,id)
    if current['run_id']==run:c.execute('UPDATE demand_sync_sources SET last_error=?,next_run_at=?,run_id=NULL,running_until=NULL WHERE id=?',(message,next_daily_pull() if source['source_kind']=='API_KA' else later(hours=current['interval_minutes']/60),id))
    c.execute("UPDATE demand_sync_runs SET status='FAILED',finished_at=?,error=? WHERE id=? AND status='RUNNING'",(now(),message,run));self.p.audit(c,a,'DEMAND_SYNC_FAILED',id,{'run_id':run,'error':message})
   return {'id':run,'status':'FAILED','error':message,'fetched':0,'added':0,'updated':0}
 def _store_result(self,c,a,id,source,result,rules,run):
  """写入已在事务外读取的结果；复用原表历史、配额及映射规则，不执行网络请求。"""
  records=result['records'];current=self._source(c,a,id)
  require_current_source(current)
  if result.get('notice') and result.get('tab')!=source['tab_id']:
   resolved_url,resolved_doc,resolved_tab=sheet_url(result['url'])
   if resolved_doc!=source['doc_id'] or result.get('requested_tab')!=source['tab_id'] or resolved_tab!=result['tab']:raise QQSheetError('解析后的文档身份不一致，未更改来源')
   if c.execute('SELECT 1 FROM demand_sync_sources WHERE org_id=? AND doc_id=? AND tab_id=? AND id!=?',(a['org_id'],resolved_doc,resolved_tab,id)).fetchone():raise QQSheetError('当前工作表已有同步来源，请检查并合并来源配置')
   c.execute('UPDATE demand_sync_sources SET url=?,tab_id=?,version=version+1 WHERE id=?',(resolved_url,resolved_tab,id))
   self.p.audit(c,a,'DEMAND_SYNC_TAB_RESOLVED',id,{'old_tab':source['tab_id'],'new_tab':resolved_tab,'notice':result['notice']})

  for replaced in result.get('superseded_records',[]):
   if rules.get(replaced['key'])!='LAST_ROW_WINS':raise QQSheetError('重复需求尚未确认处理规则')
   self._archive(c,a,id,replaced,'同一需求采用原表后行，旧行保留历史')
  old={r['source_key']:dict(r) for r in c.execute('SELECT * FROM demand_sync_projects WHERE source_id=?',(id,))};seen=set();added=updated=unchanged=0;stamp=now()
  for row in records:
   key=row['key'];seen.add(key);content=digest(canonical({'values':row['values'],'raw':row['raw_values']}));prior=old.get(key)
   if not prior:
    added+=1;c.execute('INSERT INTO demand_sync_projects(id,source_id,org_id,source_key,row_number,values_json,raw_json,content_hash,is_present,first_seen_at,last_seen_at,changed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(uid('dp_'),id,a['org_id'],key,row['row_number'],canonical(row['values']),canonical(row['raw_values']),content,1,stamp,stamp,stamp))
   else:
    changed=prior['content_hash']!=content or not prior['is_present'];updated+=bool(changed);unchanged+=not changed
    if prior['content_hash']!=content:self._archive(c,a,id,{'key':key,'row_number':prior['row_number'],'values':json.loads(prior['values_json']),'raw_values':json.loads(prior['raw_json'])},'原表更新前版本')
    c.execute('UPDATE demand_sync_projects SET row_number=?,values_json=?,raw_json=?,content_hash=?,is_present=1,last_seen_at=?,changed_at=? WHERE id=?',(row['row_number'],canonical(row['values']),canonical(row['raw_values']),content,stamp,stamp if changed else prior['changed_at'],prior['id']))
  missing=[r['id'] for key,r in old.items() if key not in seen and r['is_present']]
  for project_id in missing:c.execute('UPDATE demand_sync_projects SET is_present=0,changed_at=? WHERE id=?',(stamp,project_id))
  linked=self.p._reconcile_source(c,a,id)
  if result.get('superseded_records'):
   resolved=[{'key':r['key'],'old_row':r['row_number'],'current_row':next(v['row_number'] for v in records if v['key']==r['key'])} for r in result['superseded_records']]
   self.p.audit(c,a,'DEMAND_SYNC_DUPLICATE_RESOLVED',id,{'resolutions':resolved})
   result['notice']='；'.join(filter(None,[result.get('notice',''),'重复编号已按确认规则采用原表后行，旧版本已保留历史']))
  from ka_demand_sources import next_daily_pull
  c.execute('UPDATE demand_sync_sources SET title=?,headers=?,last_success_at=?,next_run_at=?,last_error=?,run_id=NULL,running_until=NULL WHERE id=?',(result['title'],canonical(result['headers']),stamp,next_daily_pull() if source['source_kind']=='API_KA' else later(hours=current['interval_minutes']/60),'',id))
  c.execute("UPDATE demand_sync_runs SET status='SUCCESS',finished_at=?,fetched=?,added=?,updated=?,unchanged=?,removed=? WHERE id=?",(stamp,len(records),added,updated,unchanged,len(missing),run))
  self.p.audit(c,a,'DEMAND_SYNC_SUCCESS',id,{'run_id':run,'fetched':len(records),'added':added,'updated':updated,'removed':len(missing),'demands':linked})
  return {'id':run,'status':'SUCCESS','fetched':len(records),'added':added,'updated':updated,'unchanged':unchanged,'removed':len(missing),'demands':linked,'notice':result.get('notice','')}
 def all_projects(self,a,page=1,page_size=20,filters=None):
  from synced_demands import split_values,city_name,norm
  self.p.require(a,'read');filters=filters or {}
  if filters.get('project') not in (None,'','MEMBER','KA'):raise Fault('SYNC_PROJECT_INVALID','项目筛选仅支持会员或KA')
  if page_size not in (20,50,100):raise Fault('PAGE_SIZE_INVALID','每页只能选择20、50、100条')
  c=self.p.connect()
  try:
   sources={r['id']:dict(r) for r in c.execute('SELECT s.* FROM demand_sync_sources s WHERE s.org_id=? AND s.replaced_by IS NULL AND s.deleted_at IS NULL AND '+SOURCE_PROJECT_AVAILABLE_SQL+' ORDER BY s.created_at,s.id',(a['org_id'],))}
   headers=list(dict.fromkeys(h for source in sources.values() for h in json.loads(source['headers'])))
   rows=[];at=now()
   for r in c.execute("SELECT p.*,s.partner_id,s.sale_project,s.quantity_basis,s.source_kind,s.name AS source_name,d.id AS demand_id,d.status AS demand_status,d.sync_state AS demand_sync_state,d.start_at AS demand_start_at,d.end_at AS demand_end_at FROM demand_sync_projects p JOIN demand_sync_sources s ON s.id=p.source_id LEFT JOIN demands d ON d.external_project_id=p.id WHERE p.org_id=? AND s.org_id=? AND s.replaced_by IS NULL AND s.deleted_at IS NULL AND p.is_present=1 AND "+SOURCE_PROJECT_AVAILABLE_SQL+" ORDER BY s.created_at,s.id,p.row_number,p.id",(a['org_id'],a['org_id'])):
    row=dict(r);row['values']=json.loads(row.pop('values_json'));api_values=json.loads(row.pop('raw_json')) if row['source_kind']=='API_KA' else None;row.pop('raw_json',None);row['api_vehicles']=api_values['vehicles'] if api_values else [];row.pop('content_hash');row['match_status']='UNCONFIGURED' if row['mapping_error'] or not row['demand_id'] else row['demand_status'];row['regions']=split_values(row['values'].get('城市&地区') or row['values'].get('城市'));row['models']=['全系' if v in ('全车型','全系') else v for v in split_values(row['values'].get('车型'))]
    from synced_demands import source_time,source_execution_ended
    try:end=row['demand_end_at'] or source_time(row['values'].get('结束时间（渠道编辑）') or row['values'].get('结束时间'),True)
    except ValueError:end=None
    if source_execution_ended(row['values']) or demand_has_ended({'status':row['demand_status'],'sync_state':row['demand_sync_state'],'end_at':end},at):continue
    try:start=row['demand_start_at'] or source_time(row['values'].get('开始时间') or row['values'].get('下单日期'))
    except ValueError:start=None
    row['execution_days']=execution_days(start,at)
    rows.append(row)
    row['order_quantity']=source_order_quantity(row['values'],row['demand_sync_state'])
   def matches(row,skip=None):
    value=row['values']
    if row['api_vehicles'] and filters.get('brand') and filters.get('model') and skip not in ('brand','model'):
     if not any(filters['brand'].strip().casefold() in v['brand'].casefold() and filters['model'].strip().casefold() in v['model'].casefold() for v in row['api_vehicles']):return False
    for key,selected in filters.items():
     if not selected or key==skip:continue
     if key=='partner_id' and row['partner_id']!=selected:return False
     if key=='project' and row['sale_project']!=selected:return False
     if key=='brand' and selected.strip().casefold() not in value.get('品牌','').casefold():return False
     if key=='model' and not any(selected.strip().casefold() in v.casefold() for v in row['models']):return False
     if key=='match_status' and row['match_status']!=selected:return False
     if key=='city':
      regions={city_name(v) for v in row['regions']};target=city_name(selected)
      if target=='全国' and '全国' not in regions:return False
      if target!='全国' and '全国' not in regions and not any(target in region for region in regions):return False
    return True
   choices={}
   for key in ('brand','model','city','match_status'):
    candidates=[row for row in rows if matches(row,key)];values=set()
    for row in candidates:
     if key=='city':values.update(row['regions'])
     elif key=='model':values.update(v['model'] for v in row['api_vehicles'] if not filters.get('brand') or filters['brand'].strip().casefold() in v['brand'].casefold()) if row['api_vehicles'] else values.update(row['models'])
     elif key=='brand' and row['api_vehicles']:values.update(v['brand'] for v in row['api_vehicles'])
     elif key=='match_status':values.add(row['match_status'])
     else:values.add(row['values'].get('品牌' if key=='brand' else '车型',''))
    labels={'ACTIVE':'执行中','CLOSED':'已结束','PAUSED':'已暂停','COMPLETED':'已完成','UNCONFIGURED':'待配置'}
    choices[key]=[{'value':v,'label':labels.get(v,v) if key=='match_status' else v} for v in sorted(values) if v]
    if key=='model':
     for option in choices[key]:option['brands']=sorted({brand for row in candidates if option['value'] in row['models'] for brand in ([v['brand'] for v in row['api_vehicles'] if v['model']==option['value']] if row['api_vehicles'] else [row['values'].get('品牌','')])})
   choices['project']=[{'value':'MEMBER','label':'会员'},{'value':'KA','label':'KA'}]
   filtered=[row for row in rows if matches(row)];total=len(filtered);counts={}
   for row in filtered:counts[row['match_status']]=counts.get(row['match_status'],0)+1
   # 按筛选后的全量行统计，再分页；多城市拆分去重，全国不冒充某个具体城市。
   wide_regions={'全国','不限','全部地区'}
   cities={city_name(region) for row in filtered for region in row['regions'] if region not in wide_regions and city_name(region)}
   summary={'brand_count':len({norm(brand) for row in filtered for brand in (split_values(row['values'].get('品牌','')) if row['source_kind']=='API_KA' else [row['values'].get('品牌','')]) if norm(brand)}),
            'city_count':len(cities),'nationwide_demands':sum(any(region in wide_regions for region in row['regions']) for row in filtered),
            'order_quantities':summarize_order_quantities((row['quantity_basis'],row['order_quantity']) for row in filtered if not (row['order_quantity']=='0' and (row['source_kind']=='API_KA' or row['demand_sync_state']=='ENDED' or source_execution_ended(row['values']))))}
   page=max(1,page);items=filtered[(page-1)*page_size:page*page_size]
   for item in items:item['demand']=self.p._public(c,a,'demands',self.p.one(c,'demands',item['demand_id'],a)) if item['demand_id'] else None
   scoped_sources=[s['id'] for s in sources.values() if (not filters.get('partner_id') or s['partner_id']==filters['partner_id']) and (not filters.get('project') or s['sale_project']==filters['project'])]
   return {'items':items,'summary':summary,'headers':headers,'filter_options':choices,'counts':counts,'source_ids':scoped_sources,'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}
  finally:c.close()
 def projects(self,a,source_id,page=1,page_size=20,q='',present='1'):
  self.p.require(a,'read')
  if page_size not in (20,50,100):raise Fault('PAGE_SIZE_INVALID','每页只能选择20、50、100条')
  c=self.p.connect()
  try:
   source=self._source(c,a,source_id);args=[source_id,a['org_id']];where='source_id=? AND org_id=?'
   if present in ('0','1'):where+=' AND is_present=?';args.append(int(present))
   rows=[]
   for r in c.execute('SELECT * FROM demand_sync_projects WHERE '+where+' ORDER BY row_number,id',args):
    item=dict(r);item['values']=json.loads(item.pop('values_json'));item.pop('raw_json');item.pop('content_hash')
    linked=c.execute('SELECT * FROM demands WHERE external_project_id=?',(item['id'],)).fetchone()
    item['demand']=self.p._public(c,a,'demands',dict(linked)) if linked else None
    item['order_quantity']=source_order_quantity(item['values'],linked['sync_state'] if linked else None)
    if not q or q.casefold() in canonical(item['values']).casefold():rows.append(item)
   page=max(1,page);total=len(rows)
   return {'items':rows[(page-1)*page_size:page*page_size],'headers':json.loads(source['headers']),'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}
  finally:c.close()
 def detail(self,a,id):
  self.p.require(a,'read');c=self.p.connect()
  try:
   row=self.p.one(c,'demand_sync_projects',id,a);row['values']=json.loads(row.pop('values_json'));row['raw_values']=json.loads(row.pop('raw_json'));linked=c.execute('SELECT * FROM demands WHERE external_project_id=?',(id,)).fetchone();row['demand']=self.p._public(c,a,'demands',dict(linked)) if linked else None
   row['history']=[]
   for h in c.execute('SELECT * FROM demand_sync_project_history WHERE org_id=? AND source_id=? AND source_key=? ORDER BY created_at DESC,id DESC LIMIT 50',(a['org_id'],row['source_id'],row['source_key'])):
    item=dict(h);item['values']=json.loads(item.pop('values_json'));item['raw_values']=json.loads(item.pop('raw_json'));row['history'].append(item)
   return row
  finally:c.close()
 def runs(self,a,source_id):
  self.p.require(a,'read');c=self.p.connect()
  try:self._source(c,a,source_id);return [dict(r) for r in c.execute('SELECT * FROM demand_sync_runs WHERE source_id=? ORDER BY started_at DESC,id DESC LIMIT 20',(source_id,))]
  finally:c.close()
 def tick(self):
  c=self.p.connect()
  try:sources=[dict(r) for r in c.execute("SELECT id,org_id FROM demand_sync_sources WHERE source_kind IN ('QQ_DOC','API_KA') AND replaced_by IS NULL AND deleted_at IS NULL AND enabled=1 AND next_run_at<=? AND (running_until IS NULL OR running_until<=?)",(now(),now()))]
  finally:c.close()
  for source in sources:
   actor={'id':'system:demand-sync','org_id':source['org_id'],'role':'admin'}
   try:self.run(actor,source['id'],'scheduled')
   except Fault as e:
    if e.code not in ('SYNC_RUNNING','SOURCE_DELETED'):raise
 def schedule(self,stop):
  while not stop.wait(10):
   try:self.tick()
   except Exception as e:print('项目定时同步失败',type(e).__name__,flush=True)
