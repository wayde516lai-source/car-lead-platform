"""Shared dispatch policy, configuration and local automatic allocation."""
import json
from project_availability import demand_project_condition

class DistributionMixin:
 def _ensure_partner_projects(self,c,org,partner,projects=('MEMBER','KA')):
  for project in projects:c.execute('INSERT OR IGNORE INTO partner_projects(org_id,partner_id,project) VALUES(?,?,?)',(org,partner,project))

 def _demand_project_enabled(self,c,a,d,project=None):
  """共同校验主体与项目开关；UNKNOWN需求继承主体默认项目。"""
  selected=project if project in ('MEMBER','KA') else d['sale_project']
  row=c.execute("SELECT 1 FROM partners owner JOIN partner_projects p ON p.org_id=owner.org_id AND p.partner_id=owner.id WHERE owner.org_id=? AND owner.id=? AND owner.enabled=1 AND p.enabled=1 AND p.project=CASE WHEN ? IN ('MEMBER','KA') THEN ? ELSE COALESCE(owner.cooperation_project,'MEMBER') END",(a['org_id'],d['buyer_id'],selected,selected)).fetchone()
  return bool(row)

 def project_status_update(self,a,buyer,project,data):
  """只更改指定主体项目，暂停时取消该项目未发送预占，历史和另一项目不变。"""
  from domain import Fault
  self.require(a,'settings')
  if project not in ('MEMBER','KA') or type(data.get('enabled')) not in (int,bool) or data['enabled'] not in (0,1):raise Fault('PROJECT_STATUS_INVALID','请选择会员或KA项目及合法启用状态')
  with self.tx() as c:
   partner=self.one(c,'partners',buyer,a)
   if partner['role'] not in ('MEDIA','AGENCY'):raise Fault('PARTNER_INVALID','仅媒体或乙方支持线索供应项目')
   current=c.execute('SELECT * FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],buyer,project)).fetchone()
   if not current:raise Fault('COOPERATION_PROJECT_REQUIRED','该合作项目尚未开通')
   if data.get('version')!=current['version']:raise Fault('VERSION_CONFLICT','项目配置已变化，请刷新重试',409)
   enabled=int(data['enabled'])
   c.execute('UPDATE partner_projects SET enabled=?,version=version+1 WHERE org_id=? AND partner_id=? AND project=?',(enabled,a['org_id'],buyer,project))
   if not enabled:
    for row in c.execute("SELECT x.id FROM deliveries x JOIN demands d ON d.id=x.demand_id WHERE x.org_id=? AND d.org_id=? AND d.buyer_id=? AND (d.sale_project=? OR (d.sale_project='UNKNOWN' AND ?=?)) AND x.first_sent_at IS NULL AND x.send_status='QUEUED'",(a['org_id'],a['org_id'],buyer,project,partner['cooperation_project'] or 'MEMBER',project)).fetchall():self._cancel(c,a,row['id'],'PROJECT_PAUSED')
   self.audit(c,a,'PARTNER_PROJECT_STATUS',buyer+'|'+project,{'project':project,'enabled':enabled})
   result=dict(c.execute('SELECT * FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],buyer,project)).fetchone());result['subject_enabled']=bool(partner['enabled']);return result

 def _project_mode(self,c,a,buyer,project):
  row=c.execute('SELECT matching_mode FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],buyer,project)).fetchone()
  return row[0] if row else 'MANUAL'

 def _project_mode_block(self,c,a,d,mode,project=None):
  if not self._demand_project_enabled(c,a,d,project):return 'PROJECT_PAUSED'
  expected=self._project_mode(c,a,d['buyer_id'],project or d['sale_project'])
  return None if expected==mode else 'PROJECT_AUTO_ONLY' if expected=='AUTO' else 'PROJECT_MANUAL_ONLY'

 def _set_project_mode(self,c,a,buyer,project,mode):
  self._ensure_partner_projects(c,a['org_id'],buyer,(project,))
  c.execute('UPDATE partner_projects SET matching_mode=?,version=version+1 WHERE org_id=? AND partner_id=? AND project=?',(mode,a['org_id'],buyer,project))
  c.execute('UPDATE demands SET auto_enabled=?,version=version+1 WHERE org_id=? AND buyer_id=? AND sale_project=?',(int(mode=='AUTO'),a['org_id'],buyer,project))
  if mode=='MANUAL':
   for r in c.execute("SELECT x.id FROM deliveries x JOIN demands d ON d.id=x.demand_id WHERE x.org_id=? AND d.buyer_id=? AND d.sale_project=? AND x.dispatch_mode='AUTO' AND x.first_sent_at IS NULL AND x.send_status='QUEUED'",(a['org_id'],buyer,project)).fetchall():self._cancel(c,a,r['id'],'PROJECT_MODE_CHANGED')
  self.audit(c,a,'PROJECT_MATCHING_MODE',buyer+'|'+project,{'project':project,'mode':mode})

 def project_add(self,a,buyer,project):
  from domain import Fault
  self.require(a,'settings')
  if project not in ('MEMBER','KA'):raise Fault('COOPERATION_PROJECT_REQUIRED','请选择会员或KA项目')
  with self.tx() as c:
   p=self.one(c,'partners',buyer,a)
   if p['role'] not in ('MEDIA','AGENCY'):raise Fault('PARTNER_INVALID','仅媒体或乙方可以新增合作项目')
   if c.execute('SELECT 1 FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],buyer,project)).fetchone():raise Fault('PROJECT_EXISTS','该项目已经开通',409)
   self._ensure_partner_projects(c,a['org_id'],buyer,(project,));self.audit(c,a,'PARTNER_PROJECT_ADD',buyer,{'project':project})
  return {'created':True,'project':project,'matching_mode':'MANUAL'}

 def project_mode_update(self,a,buyer,project,data):
  from domain import Fault
  self.require(a,'dispatch')
  if project not in ('MEMBER','KA') or data.get('matching_mode') not in ('MANUAL','AUTO'):raise Fault('PROJECT_MODE_INVALID','请选择会员或KA项目及手动或自动匹配方式')
  with self.tx() as c:
   partner=self.one(c,'partners',buyer,a)
   if partner['role'] not in ('MEDIA','AGENCY'):raise Fault('PROJECT_MODE_INVALID','该主体不支持线索供应项目')
   current=c.execute('SELECT * FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],buyer,project)).fetchone()
   if not current or data.get('version')!=current['version']:raise Fault('VERSION_CONFLICT','匹配方式已变化，请刷新重试',409)
   if not current['enabled'] or not partner['enabled']:raise Fault('PROJECT_PAUSED','主体或项目已停用，请先启用后调整匹配方式')
   self._set_project_mode(c,a,buyer,project,data['matching_mode'])
   if data['matching_mode']=='AUTO':
    c.execute('UPDATE settings SET auto_dispatch_enabled=1,version=version+1 WHERE org_id=?',(a['org_id'],))
   return dict(c.execute('SELECT * FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],buyer,project)).fetchone())

 def _business_match_lead_rows(self,c,a):
  """按线索去重统计基础需求候选，停用主体/项目与执行限制留给可分发指标。

  仍排除过期线索、非ACTIVE及已移除需求；不复制车型和城市匹配规则。
  """
  demands=[dict(row) for row in c.execute("SELECT * FROM demands WHERE org_id=? AND status='ACTIVE' AND COALESCE(sync_state,'')!='REMOVED'",(a['org_id'],))]
  context={}
  for lead in self._lead_rows(c,a):
   if any(self._basic_match_block(c,a,lead,demand,match_context=context) is None for demand in demands):yield lead

 def _matchable_lead_rows(self,c,a,buyer_ids=None):
  from domain import now
  demands=[dict(d) for d in c.execute("SELECT * FROM demands WHERE org_id=? AND status='ACTIVE'",(a['org_id'],))]
  buyer_ids=[id for id in (buyer_ids or []) if id]
  if buyer_ids:
   partners={p['id']:dict(p) for p in c.execute("SELECT * FROM partners WHERE org_id=? AND role IN ('MEDIA','AGENCY') AND enabled=1",(a['org_id'],))}
   selections=[(value.split('|',1)+[''])[:2] if '|' in value else [value,''] for value in buyer_ids]
   demands=[d for d in demands if any(id in partners and (d['buyer_id']==id) and (not project or d['sale_project']==project or (d['sale_project']=='UNKNOWN' and partners[id]['cooperation_project']==project)) for id,project in selections)]
  at=now();match_context={}
  for row in self._lead_rows(c,a):
   lead=self._effective_lead(row,at)
   if lead['status']!='READY':continue
   # Only leads that can currently reach at least one active demand enter this pool.
   if not any(self._block(c,a,lead,d,project=d['sale_project'] if d['sale_project']!='UNKNOWN' else 'MEMBER',match_context=match_context) is None for d in demands):continue
   yield lead

 def matching_leads(self,a,page_size=20,page=1,filters=None,selected_ids=None,buyer_ids=None):
  from domain import Fault,LEAD_COLUMNS,now
  self.require(a,'dispatch')
  if page_size not in (20,50,100):raise Fault('PAGE_SIZE_INVALID','每页只能选择20、50、100条')
  filters=filters or {};c=self.connect()
  try:
   override=self._lead_field_override(c,a);fields=set(dict(LEAD_COLUMNS) if override is None else override)
   definitions=[('lead_no','编号'),('city','城市'),('brand','品牌'),('model','车型'),('sales_count','已匹配媒体'),('project','项目')]
   columns=[{'key':k,'label':v} for k,v in definitions if k in fields]
   rows=[]
   for lead in self._matchable_lead_rows(c,a,buyer_ids):
    records=[d for d in self._policy_records(c,a,lead) if d['lead_id']==lead['id']]
    rows.append((lead,records))
   eligible={r['id'] for r,_ in rows}
   options=self._vehicle_filter_options(c,a,fields) if fields & {'brand','model'} else {}
   # Retain filter choices for eligible historical/custom models even when a
   # media dictionary has removed them or has not been imported yet.
   for lead,_ in rows:
    if 'brand' in fields and not any(o['value']==lead['filter_brand_id'] for o in options['brand']):
     options['brand'].append({'value':lead['filter_brand_id'],'label':lead['filter_brand']})
    if 'model' in fields and not any(o['value']==lead['series_id'] for o in options['model']):
     option={'value':lead['series_id'],'label':lead['filter_model']}
     if 'brand' in fields:option['brand']=lead['filter_brand_id']
     options['model'].append(option)
   if 'city' in fields:
    cities={r['city_code'] for r,_ in rows};options['city']=[{'value':v,'label':{'310100':'上海市','110100':'北京市'}.get(v,v)} for v in sorted(cities)]
   if 'sales_count' in fields:
    counts=sorted({len({d['media_id'] for d in rec}) for _,rec in rows});options['sales_count']=[{'value':str(n),'label':str(n)+' 家'} for n in counts]
   if 'sales_count' in fields and 'sold_media' in fields:
    media=c.execute("SELECT id,name FROM partners WHERE org_id=? AND role='MEDIA' ORDER BY name,id",(a['org_id'],)).fetchall()
    options['matched_media']=[{'value':'NONE','label':'尚未匹配媒体'}]+[{'value':v['id'],'label':v['name']} for v in media]
   if 'project' in fields:options['project']=[{'value':v,'label':n} for v,n in [('NONE','尚无项目'),('KA','KA'),('MEMBER','会员'),('UNKNOWN','待补录')]]
   result=[]
   for lead,records in rows:
    if 'lead_no' in fields and filters.get('lead_no','').strip().casefold() not in lead['lead_no'].casefold():continue
    if 'city' in fields and filters.get('city') and filters['city']!=lead['city_code']:continue
    if 'brand' in fields and filters.get('brand') and filters['brand']!=lead['filter_brand_id']:continue
    if 'model' in fields and filters.get('model') and filters['model']!=lead['series_id']:continue
    count=len({d['media_id'] for d in records})
    if 'sales_count' in fields and filters.get('sales_count','') and filters['sales_count']!=str(count):continue
    media=filters.get('matched_media') if 'sales_count' in fields and 'sold_media' in fields else None
    project=filters.get('project') if 'project' in fields else None
    if (media=='NONE' or project=='NONE') and records:continue
    if (media and media!='NONE') or (project and project!='NONE'):
     if not any((not media or d['media_id']==media) and (not project or d['project']==project) for d in records):continue
    item={'id':lead['id'],'version':lead['version'],**self._lead_columns(c,a,lead,{k for k,_ in definitions if k in fields})}
    if 'sales_count' in fields:item['sales_count']=count
    if 'project' in fields:item['project']=' / '.join(dict.fromkeys({'KA':'KA','MEMBER':'会员','UNKNOWN':'待补录'}[d['project']] for d in records))
    result.append(item)
   total=len(result);page=max(1,page)
   return {'items':result[(page-1)*page_size:page*page_size],'columns':columns,'filter_options':options,'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total,
           'eligible_selected_ids':[v for v in (selected_ids or []) if v in eligible],'filtered_ids':[v['id'] for v in result]}
  finally:c.close()

 def demand_summary(self,a,page_size=20,page=1,q='',status='',media=''):
  from domain import Fault,now,day
  self.require(a,'read')
  if page_size not in (20,50,100):raise Fault('PAGE_SIZE_INVALID','每页只能选择20、50、100条')
  c=self.connect()
  try:
   partners={r['id']:r['name'] for r in c.execute('SELECT id,name FROM partners WHERE org_id=?',(a['org_id'],))}
   daily={r['demand_id']:(r['reserved'],r['valid']) for r in c.execute("SELECT demand_id,sum(quota_state='RESERVED') reserved,sum(quota_state='VALID') valid FROM deliveries WHERE org_id=? AND day=? GROUP BY demand_id",(a['org_id'],day()))}
   groups={};at=now()
   for row in c.execute("SELECT d.* FROM demands d WHERE d.org_id=? AND COALESCE(d.sync_state,'')!='REMOVED' AND "+demand_project_condition('d')+" ORDER BY buyer_id,media_id,sale_project,id",(a['org_id'],)):
    d=dict(row);key=(d['buyer_id'],d['media_id'],d['sale_project'])
    if key not in groups:groups[key]={'buyer_id':key[0],'media_id':key[1],'project':key[2],'buyer_name':partners.get(key[0],key[0]),'media_name':partners.get(key[1],key[1]),'active_count':0,'total_count':0,'valid_count':0,'reserved_count':0,'available_count':0,'target_total':0,'status_counts':{},'basis':{}}
    g=groups[key];g['total_count']+=1
    effective=d['status']
    if effective=='ACTIVE' and d['end_at']<=at:effective='CLOSED'
    elif effective=='ACTIVE' and d['start_at']>at:effective='WAITING'
    g['status_counts'][effective]=g['status_counts'].get(effective,0)+1
    if effective!='ACTIVE':continue
    g['active_count']+=1;basis=d.get('quantity_basis') or 'TOTAL'
    reserved,valid=daily.get(d['id'],(0,0)) if basis=='DAILY' else (d['reserved_count'],d['valid_count'])
    if d.get('api_submit_limit') is not None:
     from ka_demand_sources import submission_usage
     queued,submitted=submission_usage(c,d['id']);d['target_total']=d['api_submit_limit']
     available=max(0,d['target_total']-queued-submitted) if d['api_quota_day']==day().replace('-','') else 0
    else:available=max(0,d['target_total']-reserved-valid)
    b=g['basis'].setdefault(basis,{'active_count':0,'target_total':0,'valid_count':0,'reserved_count':0,'available_count':0})
    for item in (g,b):
     item['target_total']+=d['target_total'];item['valid_count']+=valid;item['reserved_count']+=reserved;item['available_count']+=available
    b['active_count']+=1
   for r in c.execute("SELECT r.buyer_id,r.media_id,p.project FROM routes r JOIN partner_projects p ON p.org_id=r.org_id AND p.partner_id=r.buyer_id JOIN partners owner ON owner.id=r.buyer_id AND owner.org_id=r.org_id WHERE r.org_id=? AND owner.enabled=1 AND p.enabled=1 ORDER BY r.buyer_id,r.media_id,p.project",(a['org_id'],)):
    key=(r['buyer_id'],r['media_id'],r['project'])
    if key not in groups:groups[key]={'buyer_id':key[0],'media_id':key[1],'project':key[2],'buyer_name':partners.get(key[0],key[0]),'media_name':partners.get(key[1],key[1]),'active_count':0,'total_count':0,'valid_count':0,'reserved_count':0,'available_count':0,'target_total':0,'status_counts':{},'basis':{}}
   rows=[]
   for g in groups.values():
    g['matching_mode']=self._project_mode(c,a,g['buyer_id'],g['project']);config=c.execute('SELECT version FROM partner_projects WHERE org_id=? AND partner_id=? AND project=?',(a['org_id'],g['buyer_id'],g['project'])).fetchone();g['mode_version']=config[0] if config else None
    g['status']='ACTIVE' if g['active_count'] else 'NONE' if not g['status_counts'] else next(iter(g['status_counts'])) if len(g['status_counts'])==1 else 'MIXED'
    g.update(self.demand_progress(g) if g['target_total'] else {'attainment_rate':0,'urgency_stars':0,'target_achieved':False})
    if media and g['media_id']!=media:continue
    if status and g['status']!=status:continue
    if q and q.strip().casefold() not in (' '.join([g['buyer_name'],g['media_name'],g['project'],{'MEMBER':'会员','KA':'KA','UNKNOWN':'待设置'}.get(g['project'],'')])).casefold():continue
    rows.append(g)
   total=len(rows);page=max(1,page)
   return {'items':rows[(page-1)*page_size:page*page_size],'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}
  finally:c.close()

 @staticmethod
 def demand_progress(d):
  rate=min(100,max(0,d['valid_count']/d['target_total']*100)) if d['target_total'] else 0
  return {'attainment_rate':round(rate,2),'urgency_stars':0 if rate>=100 else 5-min(4,int(rate//20)),
          'target_achieved':rate>=100}

 def _policy_records(self,c,a,l,skip_delivery=None):
  from domain import later
  window=c.execute('SELECT dedup_window_days FROM settings WHERE org_id=?',(a['org_id'],)).fetchone()[0]
  # This lead counts for its whole lifetime. Other records with the same phone
  # and brand count within the rolling duplicate window, across models/channels.
  rows=c.execute('''SELECT l.id FROM leads l JOIN catalog v ON v.series_id=l.series_id
       JOIN catalog current ON current.series_id=?
       WHERE l.org_id=? AND l.phone_hmac=? AND lower(trim(v.brand_name))=lower(trim(current.brand_name))''',
       (l['series_id'],a['org_id'],l['phone_hmac'])).fetchall()
  ids=[r['id'] for r in rows]
  if l['id'] not in ids:ids.append(l['id'])
  placeholders=','.join('?' for _ in ids)
  cutoff=later(days=-window)
  records=[dict(r) for r in c.execute(f'''SELECT id,lead_id,media_id,project,sold_at FROM lead_sale_history
     WHERE lead_id IN ({placeholders}) AND (lead_id=? OR sold_at>=?)''',(*ids,l['id'],cutoff))]
  records.extend(dict(r) for r in c.execute(f'''SELECT id,lead_id,media_id,sale_project AS project,
       COALESCE(first_sent_at,created_at) AS sold_at FROM deliveries
       WHERE lead_id IN ({placeholders}) AND id!=?
       AND (send_status NOT IN ('CANCELED','FAILED') OR storage_status='STORED')
       AND (lead_id=? OR COALESCE(first_sent_at,created_at)>=?)''',(*ids,skip_delivery or '',l['id'],cutoff)))
  return records

 def _sale_block(self,c,a,l,d,setting,skip_delivery=None,project=None):
  if skip_delivery and project is None:
   project=c.execute('SELECT sale_project FROM deliveries WHERE id=?',(skip_delivery,)).fetchone()[0]
  records=self._policy_records(c,a,l,skip_delivery)
  if setting['forbid_same_media'] and any(r['media_id']==d['media_id'] for r in records):return 'FINAL_MEDIA_DUPLICATE'
  cap=min(setting['max_media_count'],l['max_sales_override'] or setting['max_media_count'])
  if len(records)>=cap:return 'MAX_SALES_REACHED'
  if project=='KA':
   if any(r['project']=='UNKNOWN' for r in records):return 'SALE_PROJECT_UNKNOWN'
   if sum(r['project']=='KA' for r in records)>=setting['ka_max_count']:return 'KA_LIMIT_REACHED'
  return None

 def update_distribution_rules(self,a,data):
  from domain import Fault
  self.require(a,'distribution_rules')
  reason=str(data.get('reason','')).strip()
  if not reason:raise Fault('REASON_REQUIRED','请填写修改原因')
  limits={'max_media_count':(1,100),'dedup_window_days':(1,3650),'ka_max_count':(1,100),
          'forbid_same_media':(0,1),'dispatch_enabled':(0,1),'auto_dispatch_enabled':(0,1)}
  with self.tx() as c:
   old=dict(c.execute('SELECT * FROM settings WHERE org_id=?',(a['org_id'],)).fetchone())
   if data.get('version')!=old['version']:raise Fault('VERSION_CONFLICT','配置已变更，请刷新后重试',409)
   new={k:data.get(k,old[k]) for k in limits}
   for k,(lo,hi) in limits.items():
    if type(new[k]) is not int or not lo<=new[k]<=hi:raise Fault('RULE_INVALID','规则参数超出允许范围：'+k)
   c.execute('UPDATE settings SET '+','.join(k+'=?' for k in new)+',version=version+1 WHERE org_id=?',(*new.values(),a['org_id']))
   # Recheck unsent tasks under the new rule version. Sent history is immutable.
   for r in c.execute("SELECT * FROM deliveries WHERE org_id=? AND first_sent_at IS NULL AND send_status='QUEUED' ORDER BY created_at,id",(a['org_id'],)).fetchall():
    d=dict(r);l=self.one(c,'leads',d['lead_id'],a);dm=self.one(c,'demands',d['demand_id'],a)
    blocked=self._block(c,a,l,dm,d['id'])
    if blocked:self._cancel(c,a,d['id'],blocked)
   after=dict(c.execute('SELECT * FROM settings WHERE org_id=?',(a['org_id'],)).fetchone())
   self.audit(c,a,'DISTRIBUTION_RULES_UPDATED','distribution',{'before':old,'after':after,'reason':reason})
   return {'saved':True,'settings':after}

 def auto_bind(self,a,pairs):
  from domain import Fault,now
  self.require(a,'dispatch')
  if not pairs or len({l for l,d in pairs})!=len(pairs):raise Fault('AUTO_BIND_INVALID','每条线索只可指定一个自动分发需求')
  with self.tx() as c:
   for lid,did in pairs:
    self.one(c,'leads',lid,a);dm=self.one(c,'demands',did,a)
    if self._project_mode(c,a,dm['buyer_id'],dm['sale_project'])!='AUTO' or dm['sale_project']=='UNKNOWN':raise Fault('AUTO_CONFIG_INVALID','请在需求详情开启自动接收并选择项目')
    c.execute('INSERT INTO auto_dispatch_bindings VALUES(?,?,?,?,?) ON CONFLICT(org_id,lead_id) DO UPDATE SET demand_id=excluded.demand_id,actor_id=excluded.actor_id,created_at=excluded.created_at',(a['org_id'],lid,did,a['id'],now()))
   self.audit(c,a,'AUTO_BIND','distribution',{'pairs':pairs})
  return {'bound':len(pairs)}

 def auto_status(self,a):
  self.require(a,'dispatch');c=self.connect()
  try:
   setting=dict(c.execute('SELECT * FROM settings WHERE org_id=?',(a['org_id'],)).fetchone())
   items=[]
   for r in c.execute('''SELECT d.*,l.lead_no,b.demand_id AS binding FROM auto_dispatch_decisions d
         JOIN leads l ON l.id=d.lead_id LEFT JOIN auto_dispatch_bindings b ON b.org_id=d.org_id AND b.lead_id=d.lead_id
         WHERE d.org_id=? ORDER BY d.updated_at DESC,d.lead_id LIMIT 100''',(a['org_id'],)):
    item=dict(r);fields=self._lead_field_override(c,a)
    if fields is not None and 'lead_no' not in fields:item.pop('lead_no',None)
    item['candidates']=json.loads(item['candidates']);items.append(item)
   return {'enabled':bool(setting['auto_dispatch_enabled']),'interval_seconds':30,'items':items}
  finally:c.close()

 def auto_run(self,a):
  from domain import now,uid,external_demand_no
  self.require(a,'dispatch')
  created=0;waiting=0
  with self.tx() as c:
   setting=dict(c.execute('SELECT * FROM settings WHERE org_id=?',(a['org_id'],)).fetchone())
   if not setting['auto_dispatch_enabled'] or not setting['dispatch_enabled']:
    return {'created':0,'waiting':0,'paused':True}
   # This scheduler is intentionally local-only. It never enables a live adapter.
   if self.yiche_live:return {'created':0,'waiting':0,'paused':True,'reason':'LOCAL_ONLY'}
   demands=[dict(r) for r in c.execute("SELECT * FROM demands WHERE org_id=? AND status='ACTIVE' AND sale_project IN ('KA','MEMBER') ORDER BY created_at,id",(a['org_id'],))]
   demands=[d for d in demands if self._project_mode(c,a,d['buyer_id'],d['sale_project'])=='AUTO']
   for row in c.execute("SELECT * FROM leads WHERE org_id=? AND status='READY' ORDER BY created_at,id",(a['org_id'],)).fetchall():
    l=dict(row);candidates=[];blocked=[]
    binding=c.execute('SELECT demand_id FROM auto_dispatch_bindings WHERE org_id=? AND lead_id=?',(a['org_id'],l['id'])).fetchone()
    for candidate in demands:
     dm=self.one(c,'demands',candidate['id'],a)
     reason=self._block(c,a,l,dm,project=dm['sale_project'])
     if reason:blocked.append({'demand_id':dm['id'],'demand_no':external_demand_no(dm),'reason':reason})
     else:candidates.append({'demand_id':dm['id'],'demand_no':external_demand_no(dm),'project':dm['sale_project']})
    chosen=next((d for d in candidates if binding and d['demand_id']==binding['demand_id']),None) if binding else (candidates[0] if len(candidates)==1 else None)
    status='WAITING_MANUAL' if len(candidates)>1 else 'BLOCKED';reason='MULTIPLE_DEMANDS' if len(candidates)>1 else 'NO_ELIGIBLE_DEMAND'
    if binding and not chosen:status='BLOCKED';reason='BOUND_DEMAND_BLOCKED'
    if chosen:
     did=chosen['demand_id'];result=self.dispatch(a,[(l['id'],did)],uid('auto_'),projects={did:chosen['project']},_connection=c,mode='AUTO')
     created+=result['created'];status='DISPATCHED' if result['created'] else 'BLOCKED';reason='' if result['created'] else result['items'][0]['reason']
     if result['created'] and binding:c.execute('DELETE FROM auto_dispatch_bindings WHERE org_id=? AND lead_id=?',(a['org_id'],l['id']))
    waiting+=status=='WAITING_MANUAL'
    c.execute('INSERT INTO auto_dispatch_decisions VALUES(?,?,?,?,?,?) ON CONFLICT(org_id,lead_id) DO UPDATE SET status=excluded.status,candidates=excluded.candidates,reason=excluded.reason,updated_at=excluded.updated_at',(a['org_id'],l['id'],status,json.dumps(candidates if candidates else blocked,ensure_ascii=False),reason,now()))
  sent=self.worker(a,auto_only=True)
  return {'created':created,'waiting':waiting,'sent':sent['count'],'paused':False}
