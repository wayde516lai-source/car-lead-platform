"""Convert synchronized source projects into demands used by the common dispatcher."""
import json,re,unicodedata
from datetime import datetime,time as datetime_time,timezone

def norm(value):return re.sub(r'[\s\-·（）()]+','',unicodedata.normalize('NFKC',str(value)).casefold())
def split_values(value):return list(dict.fromkeys(v.strip() for v in re.split(r'[,，、;；\n\r/／|｜]+',str(value or '')) if v.strip()))
def city_name(value):return norm({'310100':'上海市','110100':'北京市'}.get(str(value),str(value))).removesuffix('市')
def model_name(value,brand):
 # 车型中的字母和数字属于型号，符号与空格不参与比较。
 value=''.join(ch for ch in unicodedata.normalize('NFKC',str(value)).casefold() if ch.isalnum())
 brand=''.join(ch for ch in unicodedata.normalize('NFKC',str(brand)).casefold() if ch.isalnum())
 return value[len(brand):] if brand and value.startswith(brand) else value

def city_in_regions(city, encoded_regions, context=None):
 """同次匹配只解析一次地区列表，不跨请求保留地区或客户数据。"""
 if context is None:return city_name(city) in {city_name(value) for value in json.loads(encoded_regions)}
 regions=context.setdefault('normalized_regions',{})
 if encoded_regions not in regions:regions[encoded_regions]={city_name(value) for value in json.loads(encoded_regions)}
 names=context.setdefault('normalized_cities',{})
 if city not in names:names[city]=city_name(city)
 return names[city] in regions[encoded_regions]

def source_execution_ended(values: dict) -> bool:
    """识别原表明确结束标记，优先于待定结束时间和原下单量。"""
    return str(values.get('需求执行状态（渠道维护）', '')).strip() in (
        '结束', '已结束', '停止', '已停止', '关闭', '已关闭',
    )


def source_order_quantity(values: dict, sync_state=None) -> str:
    """返回当前有效下单量，保留原表值供历史追溯，不修改来源原文。"""
    if source_execution_ended(values) or sync_state == 'ENDED':
        return '0'
    return str(values.get('下单量', '')).strip()

def source_time(value,finish=False):
 from domain import SH,now
 text=str(value or '').strip()
 if text in ('','待定','长期','长期有效','无'):return None
 try:
  if len(text)==10:dt=datetime.combine(datetime.fromisoformat(text).date(),datetime_time(23,59,59) if finish else datetime_time())
  else:dt=datetime.fromisoformat(text)
  if dt.tzinfo is None:dt=dt.replace(tzinfo=SH)
  return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
 except ValueError:raise ValueError('原表时间格式无法识别：'+text)

class SyncedDemandsMixin:
 def _daily_counts(self,c,demand_id,exclude=None):
  from domain import day
  r=c.execute("SELECT COALESCE(sum(quota_state='RESERVED'),0),COALESCE(sum(quota_state='VALID'),0) FROM deliveries WHERE demand_id=? AND day=? AND id!=?",(demand_id,day(),exclude or '')).fetchone()
  return int(r[0]),int(r[1])
 def _sync_demand_public(self,c,d):
  """投影来源需求的当前额度，结束需求归零，原文及交付账本保持历史含义。"""
  if d.get('api_submit_limit') is not None:d['target_total']=d['api_submit_limit'];d['daily_limit']=d['api_submit_limit']
  if d.get('quantity_basis')=='DAILY':
   reserved,valid=self._daily_counts(c,d['id']);d.update(effective_reserved=reserved,effective_valid=valid,remaining_today=max(0,d['target_total']-reserved-valid))
   d.update(self.demand_progress({'valid_count':valid,'target_total':d['target_total']}))
  if d.get('api_submit_limit') is not None:
   from ka_demand_sources import submission_usage
   from domain import day
   queued,submitted=submission_usage(c,d['id']);d.update(submitted_today=submitted,quota_stale=d['api_quota_day']!=day().replace('-',''),remaining_today=max(0,d['api_submit_limit']-queued-submitted) if d['api_quota_day']==day().replace('-','') else 0)
  if d.get('external_project_id'):
   project=c.execute('SELECT values_json,source_id,mapping_error FROM demand_sync_projects WHERE id=?',(d['external_project_id'],)).fetchone()
   if project:d.update(source_values=json.loads(project['values_json']),source_id=project['source_id'])
  if d.get('sync_state')=='ENDED':
   # 旧库数量有正数约束；当前投影清零，原始目标与已交付账本保持可追溯。
   d.update(target_total=0,daily_limit=0,remaining_today=0)
   d.update(self.demand_progress(d))
  return d
 def _synced_vehicle_region_block(self,c,a,l,d,context=None):
  """同步需求按共享车型名称匹配，实际分发仍限制需求指定地区。"""
  if not d['all_regions'] and not city_in_regions(l['city_code'],d['match_regions'],context):return 'CITY_MISMATCH'
  from vehicle_matching import demand_model_matches
  return None if demand_model_matches(c,a['org_id'],l,d,context) else 'MODEL_MISMATCH'
 def _reconcile_source(self,c,a,source_id):
  """按本组织来源更新执行需求及版本；结束标记停发并取消尚未发送的交付。"""
  from domain import now,uid,later,SH
  from security import canonical,digest
  source=self.one(c,'demand_sync_sources',source_id,a);route=None
  if source.get('route_id'):route=self.one(c,'routes',source['route_id'],a)
  elif source['media_id']:
   r=c.execute('SELECT * FROM routes WHERE org_id=? AND media_id=? AND buyer_id=? AND enabled=1 ORDER BY id LIMIT 1',(a['org_id'],source['media_id'],source['partner_id'])).fetchone()
   if r:route=dict(r)
  result={'linked':0,'active':0,'closed':0,'errors':0}
  for record in c.execute('SELECT * FROM demand_sync_projects WHERE source_id=? ORDER BY row_number,id',(source_id,)).fetchall():
   p=dict(record);raw=json.loads(p['values_json']);old=c.execute('SELECT * FROM demands WHERE external_project_id=?',(p['id'],)).fetchone();old=dict(old) if old else None
   # 已移除的原表行只保留历史，不因旧字段失效继续占用当前需求或产生映射错误。
   if not p['is_present'] or source.get('deleted_at'):
    if old:
     if old['sync_state']!='REMOVED' or old['status']!='CLOSED':
      c.execute("UPDATE demands SET sync_state='REMOVED',status='CLOSED',version=version+1 WHERE id=? AND org_id=?",(old['id'],a['org_id']))
      for delivery in c.execute("SELECT id FROM deliveries WHERE demand_id=? AND first_sent_at IS NULL AND send_status='QUEUED'",(old['id'],)).fetchall():self._cancel(c,a,delivery['id'],'SOURCE_DELETED' if source.get('deleted_at') else 'SOURCE_DEMAND_CHANGED')
      self._snapshot_demand(c,a,old['id']);self.audit(c,a,'SOURCE_DEMAND_SYNC',old['id'],{'project_id':p['id'],'state':'REMOVED'})
     result['linked']+=1;result['closed']+=1
    continue
   try:
    if not route or route['media_id']!=source['media_id'] or route['buyer_id']!=source['partner_id']:raise ValueError('请在同步设置选择对应媒体的交付路由')
    if source['sale_project']=='UNKNOWN':raise ValueError('请在同步设置选择KA或会员项目')
    brand=str(raw.get('品牌','')).strip();models=split_values(raw.get('车型'));regions=split_values(raw.get('城市&地区') or raw.get('城市'))
    if not brand or not models or not regions:raise ValueError('原表缺少品牌、车型或地区')
    qty=str(raw.get('下单量','')).strip();ended=source_execution_ended(raw)
    api_row=json.loads(p['raw_json']) if source['source_kind']=='API_KA' else None
    if not re.fullmatch(r'[0-9]{1,7}' if api_row or ended else r'[1-9][0-9]{0,6}',qty):raise ValueError('下单量须为合法整数')
    qty=max(1,int(qty));start=source_time(raw.get('开始时间') or raw.get('下单日期')) or (old['start_at'] if old else now());end=source_time(raw.get('结束时间（渠道编辑）') or raw.get('结束时间'),True)
    sync_state='REMOVED' if not p['is_present'] else 'ENDED' if ended or (end and end<=now()) else 'ACTIVE'
    status='ACTIVE' if sync_state=='ACTIVE' else 'CLOSED'
    if old and old['sync_state']=='ACTIVE' and old['status'] in ('PAUSED','CLOSED') and status=='ACTIVE' and not p['mapping_error']:status=old['status']
    if source['quantity_basis']=='TOTAL' and old and old['valid_count']>=qty and status=='ACTIVE':status='COMPLETED'
    series='sync_model:'+digest(canonical([a['org_id'],brand,models]))[:24]
    existing=c.execute('SELECT brand_id FROM catalog WHERE brand_name=? LIMIT 1',(brand,)).fetchone();brand_id=existing['brand_id'] if existing else 'sync_brand:'+digest(brand)[:16]
    c.execute('INSERT OR IGNORE INTO catalog VALUES(?,?,?,?)',(series,brand_id,brand,' / '.join(models)))
    c.execute('INSERT OR IGNORE INTO manual_vehicle_entries VALUES(?,?,?)',(a['org_id'],series,int('全系' in models)))
    conflicts=c.execute('SELECT d.id,d.buyer_id,d.sale_project,d.demand_no FROM demands d WHERE d.org_id=? AND d.media_id=? AND COALESCE(d.external_demand_no,d.demand_no)=? AND d.id!=? AND NOT EXISTS (SELECT 1 FROM demand_sync_projects prior JOIN demand_sync_sources previous ON previous.id=prior.source_id WHERE prior.id=d.external_project_id AND previous.org_id=d.org_id AND (previous.replaced_by IS NOT NULL OR previous.deleted_at IS NOT NULL))',(a['org_id'],route['media_id'],p['source_key'],old['id'] if old else '')).fetchall()
    if any(row['buyer_id']==route['buyer_id'] and row['sale_project']==source['sale_project'] for row in conflicts):raise ValueError('该主体项目的媒体需求编号已存在，未覆盖其他来源需求')
    # 不同主体/项目可收到同一媒体编号；仅隔离内部唯一键，外部编号保持原样。
    storage_no=old['demand_no'] if old else p['source_key']
    if c.execute('SELECT 1 FROM demands WHERE org_id=? AND media_id=? AND demand_no=? AND id!=?',(a['org_id'],route['media_id'],storage_no,old['id'] if old else '')).fetchone():storage_no='sync_ref:'+digest(canonical([a['org_id'],source_id,p['source_key']]))[:32]
    values={'demand_no':storage_no,'buyer_id':route['buyer_id'],'receiver_id':route['receiver_id'],'media_id':route['media_id'],'route_id':route['id'],'series_id':series,'city_codes':canonical(regions),'target_total':qty,'daily_limit':qty,'start_at':start,'end_at':end or '9999-12-31T23:59:59+00:00','status':status,'sale_project':source['sale_project'],'auto_enabled':int(self._project_mode(c,a,route['buyer_id'],source['sale_project'])=='AUTO'),'quantity_basis':source['quantity_basis'],'match_brand':brand,'match_models':canonical(models),'match_regions':canonical(regions),'all_models':int('全系' in models),'all_regions':int(any(v in ('全国','不限','全部地区') for v in regions)),'open_ended':int(end is None),'sync_state':sync_state}
    if api_row:values.update(api_submit_limit=api_row['daily_limit'],api_monthly_limit=api_row['monthly_limit'],api_quota_day=api_row['day'],api_vehicles=canonical(api_row['vehicles']))
    values['external_demand_no']=raw.get('需求编号','') if source.get('source_kind')=='EXCEL' else p['source_key'] if storage_no!=p['source_key'] else None
    changed=not old or any(old[k]!=v for k,v in values.items());id=old['id'] if old else uid('dm_')
    if not old:
     insert={'id':id,'org_id':a['org_id'],'external_project_id':p['id'],'created_by':a['id'],'approved_by':'system:source-sync','created_at':now(),**values}
     c.execute('INSERT INTO demands('+','.join(insert)+') VALUES('+','.join('?' for _ in insert)+')',tuple(insert.values()))
    elif changed:c.execute('UPDATE demands SET '+','.join(k+'=?' for k in values)+',version=version+1 WHERE id=?',(*values.values(),id))
    if changed:
     # Unsent payloads must never retain stale source criteria or route snapshots.
     if old:
      for delivery in c.execute("SELECT id FROM deliveries WHERE demand_id=? AND first_sent_at IS NULL AND send_status='QUEUED'",(id,)).fetchall():self._cancel(c,a,delivery['id'],'SOURCE_DEMAND_CHANGED')
     self._snapshot_demand(c,a,id);self.audit(c,a,'SOURCE_DEMAND_SYNC',id,{'project_id':p['id'],'state':sync_state,'quantity_basis':source['quantity_basis']})
    c.execute("UPDATE demand_sync_projects SET mapping_error='' WHERE id=?",(p['id'],));result['linked']+=1;result['active']+=status=='ACTIVE';result['closed']+=status=='CLOSED'
   except ValueError as error:
    c.execute('UPDATE demand_sync_projects SET mapping_error=? WHERE id=?',(str(error),p['id']));result['errors']+=1
    if old and old['status']=='ACTIVE':
     c.execute("UPDATE demands SET status='PAUSED',version=version+1 WHERE id=?",(old['id'],))
     for delivery in c.execute("SELECT id FROM deliveries WHERE demand_id=? AND first_sent_at IS NULL AND send_status='QUEUED'",(old['id'],)).fetchall():self._cancel(c,a,delivery['id'],'SOURCE_DEMAND_INVALID')
  return result
 def reconcile_external_sources(self):
  c=self.connect()
  try:sources=[dict(r) for r in c.execute('SELECT id,org_id FROM demand_sync_sources WHERE replaced_by IS NULL AND deleted_at IS NULL')]
  finally:c.close()
  for source in sources:
   actor={'id':'system:source-sync','org_id':source['org_id'],'role':'admin'}
   with self.tx() as c:self._reconcile_source(c,actor,source['id'])
