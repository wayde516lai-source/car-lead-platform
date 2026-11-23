const $=s=>document.querySelector(s),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={READY:'可分发',QUARANTINED:'待核验',EXPIRED:'已过期',BLOCKED:'已停用',DELETED:'已删除',INVALID:'无效',VALID:'有效',ACTIVE:'执行中',DRAFT:'草稿',PENDING_REVIEW:'待审核',PAUSED:'已暂停',CLOSED:'已关闭',COMPLETED:'已完成',QUEUED:'待发送',ACKED:'已接收',UNKNOWN:'结果未知',CANCELED:'已取消',PENDING:'待处理',STORED:'已入库',REJECTED:'已拒绝',NONE:'暂无',PARTIAL:'部分回调',COMPLETE:'回调完整',TIMEOUT:'已超时',CONFLICT:'有冲突',PROCESSED:'已处理',STALE:'旧版本忽略',UNMATCHED:'待关联',APPROVED:'已核验',PREVIEW:'待确认',CONFIRMED:'已确认',PENDING_NOTICES:'待接收方确认',ELIGIBLE:'预估应收',UNCONFIRMED:'未确认',REVERSED:'已冲正',ACCEPTED:'已接收',MEDIA_RAW:'媒体原始回执',AGENCY_REPORTED:'乙方转述',CREATED:'新增',DUPLICATE:'重复',SKIPPED:'跳过',ERROR:'错误',MEDIA:'媒体',AGENCY:'代运营方',SOURCE:'来源方',ADS:'投放',PURCHASE:'采购',TMK:'TMK',ALL_VALID:'全量无错入库',VALID_ROWS:'仅导入合格行'};
const reasons={KA_DELIVERY_NOT_CONFIGURED:'KA线索交付接口尚未配置，不能使用会员接口',API_QUOTA_STALE:'每日额度已过期，请重新拉取需求',PROJECT_PAUSED:'主体或合作项目已停用',MOCK_DISABLED:'当前环境已禁用模拟分发',PROJECT_AUTO_ONLY:'该主体项目设置为自动分发，请由系统自动匹配',PROJECT_MANUAL_ONLY:'该主体项目设置为手动分发',PROJECT_MODE_CHANGED:'主体项目已切换为手动分发',COOPERATION_PROJECT_REQUIRED:'采购主体未设置合作项目',PROJECT_MISMATCH:'项目与原表需求不一致',SOURCE_DEMAND_CHANGED:'原表需求变更，已取消未发送任务',SOURCE_DEMAND_INVALID:'原表需求信息不完整',YICHE_CONFIG_REQUIRED:'易车必填字段未配置',YICHE_MAPPING_MISMATCH:'线索与需求的易车编码不一致',CONSENT_MISSING:'缺少接收方授权',LEAD_INVALID:'线索待核验',LEAD_EXPIRED:'线索已过期',DEMAND_LEAD_TOO_OLD:'超过该需求允许的线索年龄',LEAD_BLOCKED:'线索已撤回或停用',LEAD_DELETED:'线索已删除',DEMAND_INACTIVE:'需求未激活或已结束',MODEL_MISMATCH:'车系不符',CITY_MISMATCH:'城市不符',SOURCE_BLOCKED:'来源停用或风控拦截',FINAL_MEDIA_DUPLICATE:'该最终媒体已上报',KA_LIMIT_REACHED:'已达到KA售卖上限',SALE_PROJECT_UNKNOWN:'历史项目待补录，暂不能分发KA',MULTIPLE_DEMANDS:'多个候选需求，等待人工指定',NO_ELIGIBLE_DEMAND:'暂无符合规则的需求',BOUND_DEMAND_BLOCKED:'指定需求暂不符合分发规则',MAX_SALES_REACHED:'已达累计售卖次数上限',QUOTA_FULL:'需求配额已占满',DAILY_QUOTA_FULL:'今日配额已用完',ROUTE_UNAVAILABLE:'路由已停用',DISPATCH_PAUSED:'全局分发已暂停',CONTRACT_EXPIRED:'合同已失效',EXCLUSIVE_CONFLICT:'独家约定不符'};
const roles={admin:'管理员',operator:'产品运营',business:'商务审核',compliance:'合规专员',engineer:'接入工程师',finance:'财务',analyst:'只读分析'};
const navs=[['dashboard','◫','运营总览'],['leads','◈','线索池'],['demands','▤','需求池'],['matching','⇄','匹配分发'],['deliveries','⌁','分发记录'],['imports','↥','表格导入'],['partners','◇','合作与渠道'],['mediaVehicles','▱','车型库管理'],['yicheCodes','#','Code 码管理'],['exceptions','⚑','异常中心'],['reports','▥','数据报表'],['governance','⊙','授权与治理'],['settings','⚙','系统设置'],['acceptance','✓','验收向导']];
let state={user:null,catalog:null,view:location.hash.slice(1)||'dashboard',page:1,size:20,q:'',status:'',channel:'',media:'',selected:new Set(),selectedDemands:new Set(),selectedBuyers:new Set(),activeDemands:[],preview:null,importFile:null,importPreview:null};
const has=p=>state.user?.permissions?.includes(p),fmt=x=>x?new Date(x).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—',money=x=>'¥'+((x||0)/100).toFixed(2),pct=(a,b)=>b?Math.round(a/b*100)+'%':'0%';
const badge=x=>`<span class="badge ${['READY','VALID','ACTIVE','ACKED','STORED','APPROVED','PROCESSED','COMPLETED','COMPLETE','ACCEPTED','CONFIRMED','CREATED'].includes(x)?'green':['INVALID','REJECTED','ERROR','CONFLICT','BLOCKED','DELETED'].includes(x)?'red':['PENDING','QUEUED','UNKNOWN','QUARANTINED','PAUSED','TIMEOUT','PENDING_REVIEW','PARTIAL','UNMATCHED'].includes(x)?'gold':'blue'}">${esc(labels[x]||x||'—')}</span>`;
const partner=id=>state.catalog?.partners.find(x=>x.id===id)?.name||id,channel=id=>state.catalog?.channels.find(x=>x.id===id)?.name||id,series=id=>state.catalog?.vehicles.find(x=>x.series_id===id)?.series_name||id,city=id=>state.catalog?.cities.find(x=>x.code===id)?.name||id;
const opt=(value,label,selected='')=>`<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`;
function toast(t,error=false){const el=$('#toast');el.textContent=t;el.style.display='block';el.style.background=error?'#943f35':'#153c30';clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.style.display='none',5500)}
function demandExcelFeedback(container, message, isError = false) {
  // A document-level toast is painted behind a modal dialog. Keep import
  // feedback inside the dialog's top layer until the next attempt or close.
  let feedback = container.querySelector('.demand-excel-feedback');
  if (!feedback) {
    feedback = document.createElement('div');
    feedback.className = 'demand-excel-feedback callout span2';
    feedback.tabIndex = -1;
    container.prepend(feedback);
  }
  feedback.classList.toggle('error', isError);
  feedback.setAttribute('role', isError ? 'alert' : 'status');
  feedback.textContent = message;
  feedback.scrollIntoView({block: 'nearest'});
  if (isError) feedback.focus({preventScroll: true});
}
async function api(path,method='GET',body,headers={}){const r=await fetch('/api/v1/'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':state.user?.csrf||'',...headers},body:body===undefined?undefined:JSON.stringify(body)});const j=await r.json();if(!r.ok){const e=new Error(j.message+'（'+j.code+'）');e.status=r.status;throw e}return j.data}
function table(headers,rows,empty='暂无记录'){return `<div class="table-wrap"><table><thead><tr>${headers.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(x=>`<tr>${x.map(c=>`<td>${c}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}" class="empty">${esc(empty)}</td></tr>`}</tbody></table></div>`}
function pagination(d){return `<div class="pagination"><span>共 ${d.total} 条记录</span><div class="pager"><span>每页</span><select id="page-size" aria-label="每页条数">${[20,50,100].map(x=>opt(String(x),x+' 条',String(state.size))).join('')}</select><button data-action="prev" ${state.page<=1?'disabled':''}>‹</button><span>第 ${state.page} / ${Math.max(1,Math.ceil(d.total/state.size))} 页</span><button data-action="next" ${!d.has_more?'disabled':''}>›</button></div></div>`}
function filters(statuses=[],extra='',placeholder='搜索编号、车型或记录'){return `<div class="toolbar"><form id="search-form" class="filter-row"><input id="search" placeholder="${esc(placeholder)}" value="${esc(state.q)}" aria-label="搜索">${statuses.length?`<select id="status-filter" aria-label="状态筛选"><option value="">全部状态</option>${statuses.map(x=>opt(x,({WAITING:'未开始',MIXED:'无执行中需求'})[x]||labels[x]||x,state.status)).join('')}</select>`:''}${extra}<button type="submit">筛选</button><button type="button" data-action="clear-filters">重置</button></form><span class="tiny muted">数据实时保存 · 上海时间</span></div>`}
const mediaFilter=()=>`<select id="media-filter" aria-label="媒体筛选"><option value="">全部媒体</option>${state.catalog.partners.filter(x=>x.role==='MEDIA').map(x=>opt(x.id,x.name,state.media)).join('')}</select>`;
const channelFilter=()=>`<select id="channel-filter" aria-label="来源筛选"><option value="">全部来源</option>${state.catalog.channels.map(x=>opt(x.id,x.name,state.channel)).join('')}</select>`;
const list=(t,more='')=>api(`${t}?page_size=${state.size}&page=${state.page}&q=${encodeURIComponent(state.q)}&status=${state.status}&channel=${state.channel}&media=${state.media}${more}`);
function heading(eyebrow,title,desc,actions=''){return `<div class="page-title"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${desc}</p></div><div class="actions">${actions}</div></div>`}
function orderQuantityText(quantities = {}) {
 const daily = quantities.daily || 0, total = quantities.total || 0;
 if (daily && total) return `${daily} 条/日 · ${total} 条/项目`;
 return total ? `${total} 条/项目` : `${daily} 条/日`;
}
function stat(label,num,foot,icon){return `<div class="stat"><div class="label">${label}<span class="stat-icon">${icon}</span></div><div class="number">${num}</div><div class="foot">${foot}</div></div>`}
function modal(title,html,wide=false){$('#dialog-body').innerHTML=`<div class="modal-head"><h2>${esc(title)}</h2><button class="close" data-action="close-modal" aria-label="关闭">×</button></div><div class="modal-content">${html}</div>`;$('#dialog').style.width=wide?'980px':'720px';if(!$('#dialog').open)$('#dialog').showModal()}
function shell(){const title=navs.find(x=>x[0]===state.view)?.[2]||'运营总览';$('#app').innerHTML=`<aside class="sidebar"><div class="brand"><span class="brand-logo">⌁</span><div><strong>车联汇</strong><small>AUTO LEAD WORKSPACE</small></div></div><div class="nav-label">业务工作台</div><nav class="nav">${navs.slice(0,6).map(([id,ic,t])=>`<button data-nav="${id}" class="${id===state.view?'active':''}"><span class="nav-icon">${ic}</span>${t}</button>`).join('')}</nav><div class="nav-label">管理与洞察</div><nav class="nav">${navs.slice(6).map(([id,ic,t])=>`<button data-nav="${id}" class="${id===state.view?'active':''}"><span class="nav-icon">${ic}</span>${t}</button>`).join('')}</nav><div class="sidebar-bottom"><span class="status-dot"></span>本地测试环境<br><span class="muted">模拟数据 · 外部发送已禁用</span><br>PRD v1.1 · 可持久化业务台账</div></aside><div class="shell"><header class="topbar"><div class="breadcrumb">工作空间　/　<b>${title}</b></div><div class="userbox"><span class="pill">TEST ENVIRONMENT</span><span class="avatar">${esc(roles[state.user.role]?.slice(0,1)||'测')}</span><span>${esc(roles[state.user.role]||state.user.role)}</span><button class="link tiny" data-action="logout">切换账号</button></div></header><main class="content" id="main"><div class="loading">正在加载业务数据…</div></main></div>`}
const vehicleColumns=[
 'id','carId','carName','seriesId','seriesName','factoryId','factoryName','brandId','brandName',
 'year','guidePrice','displacement','power','gearbox','saleStatus','memberBrandName'
];
const vehicleSyncLabels={RUNNING:'同步中',SUCCESS:'成功',FAILED:'失败'};
let vehicleLibrary={id:'',q:'',page:1};
async function mediaVehicles(){
 const libraries=await api('media-vehicles');
 const selected=libraries.find(x=>x.id===vehicleLibrary.id)||libraries[0];
 const intro=heading('MEDIA VEHICLES / 车型库','车型库管理','各媒体独立存储车款。已启用的接口每天北京时间 00:00 同步。', '<button data-action="refresh">刷新状态</button>');
 if(!selected)return intro+'<section class="card"><p class="empty">请先添加媒体</p></section>';
 vehicleLibrary.id=selected.id;
 const data=await api('media-vehicles/'+encodeURIComponent(selected.id)+'?page='+vehicleLibrary.page+'&q='+encodeURIComponent(vehicleLibrary.q));
 const latest=selected.runs[0];
 const status=selected.adapter?(selected.enabled?(vehicleSyncLabels[latest?.state]||'等待同步'):'已暂停'):(selected.id==='MEDIA_A'?'待配置':'待接入');
 const mayConfigure=has('settings')&&(selected.adapter||selected.id==='MEDIA_A');
 const actions=mayConfigure?`<button data-action="vehicle-config">同步配置</button>${selected.enabled?`<button class="primary" data-action="vehicle-sync" ${latest?.state==='RUNNING'?'disabled':''}>立即同步</button>`:''}`:'';
 return intro+`<section class="card">
   <div class="toolbar"><form class="filter-row" id="vehicle-search-form">
    <select name="media" id="vehicle-media" aria-label="媒体">${libraries.map(x=>opt(x.id,x.name,x.id===selected.id?x.id:'')).join('')}</select>
    <input name="q" aria-label="搜索车款" placeholder="品牌、车系、厂商、车款或车款 ID" value="${esc(vehicleLibrary.q)}">
    <button type="submit">搜索</button>
   </form><div class="actions">${actions}</div></div>
   <div class="toolbar"><span>${badge(status)} · 库存 ${selected.total} 款</span><span class="muted smalltext">最近成功更新 ${esc(fmt(selected.last_success))}${selected.start_on&&!selected.last_success?' · 首次定时 '+esc(selected.start_on)+' 00:00':''}</span></div>
   ${latest?.error?`<div class="callout warning">${esc(latest.error)}</div>`:''}
   <p class="smalltext muted vehicle-columns-note">按原表顺序显示 16 个字段，可横向滚动查看。</p>
   ${table(vehicleColumns,data.items.map(row=>vehicleColumns.map(key=>esc(row[key]))),
    selected.adapter?'暂无符合条件的车款':selected.id==='MEDIA_A'?'配置易车 CID 和签名密钥后开始同步':'等待此媒体提供车型库接口')}
   <div class="pagination"><span>搜索结果 ${data.total} 款</span><div class="pager">
    <button data-action="vehicle-prev" ${vehicleLibrary.page<=1?'disabled':''}>‹</button>
    <span>第 ${vehicleLibrary.page} / ${Math.max(1,Math.ceil(data.total/30))} 页</span>
    <button data-action="vehicle-next" ${vehicleLibrary.page*30>=data.total?'disabled':''}>›</button>
   </div></div></section>
   <section class="card"><div class="card-head"><h2>最近同步记录</h2></div>
   ${table(['同步日期','触发方式','开始时间','状态','页数','入库车款','详情'],selected.runs.map(x=>[
    esc(x.sync_day),({auto:'定时同步',manual:'手动同步',import:'表格导入'})[x.trigger_kind]||'—',esc(fmt(x.started_at)),badge(vehicleSyncLabels[x.state]),x.trigger_kind==='import'?'—':x.pages,x.item_count,esc(x.error||'—')
   ]),'暂无同步记录')}</section>`;
}
let renderSequence=0,renderedView=null;
async function render(){const sequence=++renderSequence,view=state.view;if(!$('#main')||renderedView!==view){shell();renderedView=view}$('#main').setAttribute('aria-busy','true');try{const views={dashboard,leads,demands,matching,deliveries,imports,partners,mediaVehicles,yicheCodes,exceptions,reports,governance,settings,acceptance};const html=await (views[view]||dashboard)();if(sequence!==renderSequence)return;$('#main').innerHTML=html+`<footer class="page-footer"><span>车联汇 · 本地业务测试系统</span><span>线索分发为本地模拟　／　车型库按媒体同步</span></footer>`;mountLeadBrandPicker();mountDemandFilterPickers();$('#main').removeAttribute('aria-busy')}catch(e){if(sequence!==renderSequence)return;$('#main').removeAttribute('aria-busy');$('#main').innerHTML=`<div class="callout error">${esc(e.message)}</div><button data-action="refresh">重新加载</button>`}}
async function dashboard(){const r=await api('reports/funnel'),d=await api('demands?page_size=20'),l=await api('leads?page_size=20');const m=r.metrics,o=r.overview;return heading('OVERVIEW / 运营总览','每一条线索，都有清晰的去向。','统一查看线索库存、需求进度与交付结果。',`<button data-action="refresh">↻ 刷新数据</button><button class="primary" data-nav="matching">开始匹配分发 ↗</button>`)+`<section class="card" id="dashboard-business-summary"><div class="card-head"><div><h2>合作与需求概况</h2><span class="sub">需求按采购主体归属统计，乙方转发不重复计入媒体需求。</span></div><button class="link tiny" data-nav="demands">查看需求 →</button></div><div class="card-body"><div class="stats dashboard-overview-stats">${stat('媒体总数',o.media_count,'合作角色为媒体的主体数','◇')}${stat('乙方总数',o.agency_count,'合作角色为乙方的主体数','◇')}${stat('需求总数',o.total_demands,`其中执行中 ${o.active_demands} 项`,'▤')}${stat('需求品牌数',o.brand_count,'按当前展示需求中的品牌去重统计','▱')}${stat('下单量级',`<span class="order-quantity-value">${esc(orderQuantityText(o.order_quantities))}</span>`,'按下单目标汇总，每日与项目总量分列','▤')}</div><div class="stats dashboard-demand-breakdown">${stat('媒体需求数',o.media_demands,'采购主体为媒体的需求项数','▤')}${stat('乙方需求数',o.agency_demands,'采购主体为乙方的需求项数','▤')}</div><p class="smalltext muted">主体数量包含已停用主体；需求仅展示已启用的主体项目，含已结束等状态，不含原表已移除的需求。会员与 KA 项目中的每条需求分别计数，品牌跨项目去重。</p></div></section><div class="stats dashboard-overview-stats">${stat('累计入库线索',r.leads,'含已过期、已停用及已删除记录，每条线索计一次','◈')}${stat('可匹配条数',r.business_matchable??'待更新','满足需求时间、品牌、车型和城市条件，每条线索计一次','✓')}${stat('可分发条数',r.dispatchable??r.matchable,'进一步通过开关、合作状态、路由、售卖限制及额度检查','⇄')}${stat('累计创建分发',r.deliveries,'含排队、失败和取消记录；重试不重复计数','⇄')}${stat('媒体判定有效',m.valid,`本地模拟结果 · 待判定 ${m.pending} 条 · 已判定有效率 ${pct(m.valid,m.valid+m.invalid)}`,'↗')}</div><div class="cols"><section class="card"><div class="card-head"><div><h2>交付转化概览</h2><span class="sub">按首次发送队列统计 · 截至当前结果</span></div><span class="badge green">实时台账</span></div><div class="card-body"><div class="chart">${[['首次发送',m.sent],['接收成功',m.acked],['媒体入库',m.stored],['最终有效',m.valid]].map(([n,v])=>`<div class="bar-group"><strong>${v}</strong><div class="bar" style="height:${Math.max(4,v/Math.max(1,m.sent)*125)}px"></div><span>${n}</span></div>`).join('')}</div><div class="chart-note"><span>接收成功 ≠ 媒体入库 ≠ 最终有效</span><span>结果未知 ${m.unknown} 条</span></div></div></section><section class="card"><div class="card-head"><h2>待办与提醒</h2><button class="link tiny" data-nav="exceptions">查看异常 →</button></div><div class="card-body">${[[l.items.filter(x=>x.status==='QUARANTINED').length+' 条线索待核验','检查来源、车型映射与接收方授权','governance','⊙'],[m.pending+' 笔分发待媒体反馈','在模拟回调中验证入库和有效状态','deliveries','⌁'],[o.active_demands+' 项需求执行中','额度仅在最终有效后计入达成','demands','▤']].map(([n,t,v,ic])=>`<div class="work-item"><span class="icon">${ic}</span><div><p>${n}</p><small>${t}</small></div><button class="link" data-nav="${v}">→</button></div>`).join('')}</div></section></div><section class="card"><div class="card-head"><div><h2>执行中的采购需求</h2><span class="sub">媒体原始编号 · 有效 / 在途 / 剩余配额</span></div><button class="link tiny" data-nav="demands">全部需求 →</button></div>${demandTable(d.items.filter(x=>x.status==='ACTIVE').slice(0,4),false)}</section><div class="callout">首次使用？打开「验收向导」，按步骤验证表格导入、5条线索分发给两家媒体、跨链路去重和回调状态。所有演示数据与真实媒体隔离。</div>`}
function leadCell(lead,key){
 if(key==='status')return badge(lead.status);
 if(key==='collected_at'||key==='sold_at')return esc(fmt(lead[key]));
 if(key==='sales_count')return esc(lead.sales_count)+' 家';
 return esc(lead[key]||'—');
}
function leadTable(rows,columns){
 return table([...columns.map(c=>esc(c.label)),'操作'],rows.map(lead=>[
  ...columns.map(c=>leadCell(lead,c.key)),
  `<button class="link" data-action="lead-detail" data-id="${esc(lead.id)}">查看链路</button>`
 ]));
}

let leadFilters={},leadModels=[],leadBrands=[];
function leadModelOptions(brand,selected=''){
 return opt('','全部车型',selected)+leadModels.filter(v=>!brand||v.brand===brand).map(v=>opt(v.value,v.label,selected)).join('');
}
function leadBrandPicker(values=leadFilters){
 const current=leadBrands.find(v=>v.value===values.brand);
 return `<div class="lead-brand-picker" id="lead-brand-picker"><label for="lead-brand-search">品牌</label><input type="hidden" name="brand" value="${esc(values.brand||'')}"><div class="lead-brand-control"><input id="lead-brand-search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="lead-brand-options" autocomplete="off" placeholder="全部品牌" value="${esc(current?.label||'')}"><button type="button" aria-label="展开品牌" tabindex="-1">▾</button></div><div id="lead-brand-options" role="listbox" aria-label="品牌选项" hidden></div></div>`;
}
function mountLeadBrandPicker(){
 const picker=$('#lead-brand-picker');if(!picker)return;
 const input=picker.querySelector('#lead-brand-search'),value=picker.querySelector('[name=brand]'),list=picker.querySelector('[role=listbox]'),form=picker.closest('form');let active=-1;
 const options=[{value:'',label:'全部品牌'},...leadBrands];
 function close(){list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');}
 function select(option){value.value=option.value;input.value=option.value?option.label:'';const model=form?.elements.model;if(model){const choices=leadModels.filter(v=>!option.value||v.brand===option.value);model.innerHTML=leadModelOptions(option.value,choices.some(v=>v.value===model.value)?model.value:'');}close();}
 function open(query=''){const q=query.trim().toLowerCase();active=-1;const matches=options.filter(o=>!q||o.label.toLowerCase().includes(q));list.innerHTML=matches.length?matches.map((o,i)=>`<button type="button" role="option" tabindex="-1" id="lead-brand-option-${i}" data-value="${esc(o.value)}" aria-selected="false">${esc(o.label)}</button>`).join(''):'<p class="lead-brand-empty">暂无匹配品牌</p>';list.hidden=false;input.setAttribute('aria-expanded','true');}
 function settle(){const exact=options.find(o=>o.label===input.value.trim()||(!o.value&&!input.value.trim()));if(exact)select(exact);else{const current=options.find(o=>o.value===value.value);input.value=current?.value?current.label:'';close();}}
 picker.querySelector('button').addEventListener('mousedown',e=>e.preventDefault());picker.querySelector('button').addEventListener('click',()=>{if(list.hidden){input.focus();open();}else close();});
 input.addEventListener('click',()=>{if(list.hidden)open(input.value)});input.addEventListener('input',()=>open(input.value));
 list.addEventListener('mousedown',e=>e.preventDefault());list.addEventListener('click',e=>{const b=e.target.closest('[role=option]');if(b)select(options.find(o=>o.value===b.dataset.value))});
 picker.addEventListener('focusout',e=>{if(!picker.contains(e.relatedTarget))settle()});
 input.addEventListener('keydown',e=>{const items=[...list.querySelectorAll('[role=option]')];if(e.key==='Escape'){close();return;}if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(list.hidden){open(input.value);return;}if(!items.length)return;active=(active+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items.forEach((b,i)=>b.setAttribute('aria-selected',String(i===active)));input.setAttribute('aria-activedescendant',items[active].id);items[active].scrollIntoView?.({block:'nearest'});}if(e.key==='Enter'){e.preventDefault();if(active>=0&&items[active])items[active].click();else if(items.length===1)items[0].click();else settle();}});
}
function leadFilterBar(data){
 const fields=new Set(data.columns.map(c=>c.key));
 leadModels=data.filter_options.model||[];leadBrands=data.filter_options.brand||[];
 const labelsByKey={supplier:'供应商',acquisition_channel:'获客渠道',city:'城市',brand:'品牌',model:'车型',sold_media:'售卖媒体',project:'项目'};
 const selects=Object.entries(labelsByKey).filter(([key])=>fields.has(key)).map(([key,label])=>key==='brand'?leadBrandPicker():
  `<label>${label}<select name="${key}">${key==='model'?leadModelOptions(leadFilters.brand,leadFilters.model):opt('','全部'+label)+(data.filter_options[key]||[]).map(x=>opt(x.value,x.label,leadFilters[key]||'')).join('')}</select></label>`);
 if(fields.has('collected_at')){selects.push(`<label>采集开始日期<input type="date" name="from" value="${esc(leadFilters.from)}"></label>`);selects.push(`<label>采集结束日期<input type="date" name="to" value="${esc(leadFilters.to)}"></label>`);}
 return `<div class="toolbar"><form id="lead-search-form" class="lead-filter-form"><label>关键词<input name="q" placeholder="搜索当前可见字段" value="${esc(leadFilters.q)}"></label>${selects.join('')}<div class="actions"><button type="submit">筛选</button><button type="button" data-action="clear-filters">重置</button></div></form><span class="tiny muted">采集日期按北京时间</span></div>`;
}
async function leads(){
 const params=new URLSearchParams({page_size:state.size,page:state.page,...leadFilters});
 const data=await api('leads?'+params);
 return heading('LEAD INVENTORY / 线索池','线索池','按账号显示已授权的字段。',`
  ${has('settings')?'<button data-action="lead-field-settings">字段权限</button>':''}
  <button data-nav="imports">↥ 表格导入</button>${has('write')?'<button class="primary" data-action="new-lead">＋ 录入线索</button>':''}`)
  +`<section class="card lead-pool-card">${leadFilterBar(data)}${leadTable(data.items,data.columns)}${pagination(data)}</section>`;
}
let leadFieldSettings;
function showLeadFieldSettings(accountId){
 const {accounts,columns}=leadFieldSettings;
 const account=accounts.find(a=>a.id===accountId)||accounts[0];
 if(!account)return;
 const allowed=new Set(account.fields??columns.map(c=>c.key));
 modal('线索池字段权限',`<form id="lead-fields-form" class="grid-form">
  <label class="span2">账号<select name="account_id" id="lead-fields-account">${accounts.map(a=>opt(a.id,a.username+' · '+(roles[a.role]||a.role),account.id)).join('')}</select></label>
  <p class="smalltext muted span2">勾选此账号可见的字段。取消所有勾选可隐藏全部业务列。</p>
  <div class="checklist span2">${columns.map(c=>`<label><input type="checkbox" name="fields" value="${esc(c.key)}" ${allowed.has(c.key)?'checked':''}>${esc(c.label)}</label>`).join('')}</div>
  <div class="form-actions span2"><button type="submit" value="reset">恢复默认</button><button class="primary" type="submit">保存权限</button></div>
 </form>`);
}
function urgency(d){const rate=d.attainment_rate??Math.min(100,d.valid_count/d.target_total*100),stars=d.urgency_stars??(rate>=100?0:5-Math.min(4,Math.floor(rate/20)));return stars?`<span class="urgency-stars" aria-label="紧急程度${stars}星" title="按有效达成率计算；星级不参与排序">${'★'.repeat(stars)}${'☆'.repeat(5-stars)}</span><span class="sub">有效达成率 ${rate.toFixed(1)}%</span>`:'<span class="badge green">已达成</span>'}
function regionValues(value){return Array.isArray(value)?value:String(value||'').split(/[,，、;；\n\r/／|｜]+/).map(x=>x.trim()).filter(Boolean)}
function demandValueCell(value, kind = 'regions') {
  const values = [...new Set(regionValues(value))];
  if (values.length <= 1) return esc(values[0] || '—');
  const models = kind === 'models';
  return `<button class="link region-toggle demand-value-toggle" data-action="${models ? 'show-models' : 'show-regions'}" data-regions="${esc(JSON.stringify(values))}" title="点击查看全部${models ? '车型' : '地区'}"><span class="demand-value-first">${esc(values[0])}</span><span class="sub">共 ${values.length} ${models ? '个车型' : '个地区'} · 点击展开</span></button>`;
}
function regionCell(value) { return demandValueCell(value); }
function modelCell(value) { return demandValueCell(value, 'models'); }
function demandNumbers(d){return {valid:d.effective_valid??d.valid_count,reserved:d.effective_reserved??d.reserved_count,remaining:d.remaining_today??Math.max(0,d.target_total-d.valid_count-d.reserved_count)}}
function demandTable(rows,actions=true){return table(['媒体需求编号','交付链路','品牌 / 车型','城市 / 地区','有效 / 在途 / 可用','需求进度','紧急程度','状态',...(actions?['操作']:[])],rows.map(d=>{
 const n=demandNumbers(d),daily=d.quantity_basis==='DAILY';
 const controls=`<div class="actions"><button class="link" data-action="demand-detail" data-id="${esc(d.id)}">详情</button>${d.status==='DRAFT'&&has('demand')?`<button class="small" data-action="demand-submit" data-id="${d.id}" data-version="${d.version}">提交审核</button>`:''}${d.status==='PENDING_REVIEW'&&has('approve')?`<button class="small" data-action="demand-approve" data-id="${d.id}" data-version="${d.version}">批准</button>`:''}${d.status==='ACTIVE'&&has('demand')?`<button class="small" data-action="demand-pause" data-id="${d.id}" data-version="${d.version}">暂停</button>`:''}${d.status==='PAUSED'&&has('demand')?`<button class="small" data-action="demand-resume" data-id="${d.id}" data-version="${d.version}">恢复</button>`:''}</div>`;
 return [`<span class="name-primary mono">${esc(d.demand_no||'—')}</span><span class="sub">${d.external_project_id?'腾讯原表需求':'媒体业务编号'} · 版本 ${d.version}</span>`,`${esc(partner(d.media_id))}<span class="sub">${d.receiver_id===d.media_id?'甲方直连':esc(partner(d.receiver_id))+' → 最终媒体'} · ${d.sale_project==='MEMBER'?'会员':d.sale_project==='KA'?'KA':'项目待设置'}</span>`,`${esc(d.match_brand||'')}<span class="sub">${esc(series(d.series_id))}</span>`,regionCell(d.external_project_id?JSON.parse(d.match_regions):JSON.parse(d.city_codes).map(city)),`<strong>${n.valid}</strong> / ${n.reserved} / <span class="name-primary">${n.remaining}</span><span class="sub">${daily?'今日目标':'总目标'} ${d.target_total}${daily?' · 北京时间每日重算':''}</span>`,`<div class="track"><div class="fill" style="width:${Math.min(100,n.valid/d.target_total*100)}%"></div></div><span class="sub">${pct(n.valid,d.target_total)}${daily?' 今日达成':' 已达成'}</span>`,urgency({...d,valid_count:n.valid}),badge(d.status),...(actions?[controls]:[])];
 }))}

let demandSyncView={source:'',page:1,size:20,q:'',present:'1'},demandSyncSources=[];
async function demandSyncPanel(){
 demandSyncSources=await api('demand-sync/sources');const editable=has('demand');
 const params=new URLSearchParams({page:demandSyncView.page,page_size:demandSyncView.size,...(demandSyncView.filters||{})});
 const data=await api('demand-sync/projects?'+params);const values=demandSyncView.filters||{};const scoped=demandSyncSources.filter(s=>data.source_ids.includes(s.id));
 const displayHeaders=data.headers.filter(h=>!['需求执行状态（渠道维护）','结束时间（渠道编辑）'].includes(h));demandSyncView.options=data.filter_options;
 const subjects=state.catalog.partners.filter(p=>['MEDIA','AGENCY'].includes(p.role));
 const controls=[`<label>合作商<select name="partner_id">${opt('','全部合作商',values.partner_id||'')}${subjects.map(p=>opt(p.id,p.name,values.partner_id||'')).join('')}</select></label>`,...Object.entries({brand:'品牌',model:'车型',city:'城市',match_status:'匹配状态'}).map(([key,label])=>key==='match_status'?`<label>${label}<select name="${key}">${opt('','全部'+label,values[key]||'')}${(data.filter_options[key]||[]).map(o=>opt(o.value,o.label,values[key]||'')).join('')}</select></label>`:entryPicker('demand-filter-'+key,key,label,200).replace(' required','').replace('autocomplete="off"',`value="${esc(values[key]||'')}" autocomplete="off"`))];
 return `<section class="card"><div class="card-head"><div><h2>需求列表 <span class="tiny muted">${data.total} 条</span></h2><span class="sub">${values.partner_id?esc(partner(values.partner_id)):'全部合作商'} · 执行中 ${data.counts.ACTIVE||0} · 已结束 ${data.counts.CLOSED||0}</span></div><div class="actions"><button data-action="refresh">刷新状态</button><button data-action="demand-sync-manage">归属 / 同步管理</button>${editable?'<button data-action="demand-excel-add">↥ Excel导入</button><button class="primary" data-action="demand-sync-add">＋ 新增需求</button>':''}</div></div>
 <div class="toolbar"><form id="demand-sync-search" class="filter-row">${controls.join('')}<button type="submit">筛选</button><button type="button" data-action="demand-sync-reset">重置</button></form></div>
 ${table(['合作商',...displayHeaders.map(esc),'匹配状态','操作'],data.items.map(row=>[esc(partner(row.partner_id)),...displayHeaders.map(h=>h==='城市&地区'||h==='城市'?regionCell(row.regions||row.values[h]):h==='车型'?modelCell(row.models||row.values[h]):esc(row.values[h]||'—')),row.match_status==='UNCONFIGURED'?`<span class="badge gold" title="${esc(row.mapping_error)}">待配置</span>`:row.match_status==='CLOSED'?'<span class="badge blue">已结束</span>':badge(row.match_status),`<button class="link" data-action="demand-sync-detail" data-id="${esc(row.id)}">详情</button>${row.demand?.status==='ACTIVE'&&has('dispatch')?`<button class="link" data-action="source-demand-match" data-id="${esc(row.demand.id)}">匹配分发</button>`:''}`]),'暂无符合条件的需求')}
 <div class="pagination demand-list-summary"><span title="按当前筛选的全部需求统计，品牌和具体城市去重；全国需求单独计数">共 ${data.total} 条需求 · ${data.summary.brand_count} 个品牌 · ${data.summary.city_count} 个城市${data.summary.nationwide_demands?`（另含全国 ${data.summary.nationwide_demands} 条需求）`:''} · 下单量 ${esc(orderQuantityText(data.summary.order_quantities))}${data.summary.order_quantities.invalid_rows?`（${data.summary.order_quantities.invalid_rows} 条数量待校验）`:''}</span><div class="pager"><select id="demand-sync-page-size" aria-label="项目每页条数">${[20,50,100].map(v=>opt(String(v),v+' 条',String(demandSyncView.size))).join('')}</select><button data-action="demand-sync-prev" ${demandSyncView.page<=1?'disabled':''}>‹</button><span>第 ${demandSyncView.page} / ${Math.max(1,Math.ceil(data.total/demandSyncView.size))} 页</span><button data-action="demand-sync-next" ${!data.has_more?'disabled':''}>›</button></div></div>
 <div class="card-body demand-sync-info">${scoped.map(source=>`<div class="sync-source-info"><strong>${esc(source.name)}</strong>${editable?`<button class="small" data-action="demand-sync-edit" data-id="${esc(source.id)}">修改归属 / 设置</button>`:''}<span class="sub">合作商：${esc(partner(source.partner_id))} · ${source.source_kind==='API_KA'?'API · 每天08:00（北京时间）':source.source_kind==='EXCEL'?'Excel导入 · 不定时同步':source.enabled?'每 '+source.interval_minutes+' 分钟同步':'已暂停定时同步'} · 最近成功：${fmt(source.last_success_at)} · 下次同步：${source.source_kind==='EXCEL'?'不适用':source.enabled?fmt(source.next_run_at):'已暂停'} ${['EXCEL','API_KA'].includes(source.source_kind)?'':`<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">查看原表 ↗</a>`}</span>${source.last_error?`<p class="callout error">${esc(source.last_error)}，已保留上次成功数据。</p>`:''}</div>`).join('')||'<p class="muted">该合作商尚未添加需求链接。</p>'}</div></section>`;
}

function setDemandSourceProjects(form, selected = '', chooseDefault = false) {
 const subject = state.catalog.partners.find(row => row.id === form.elements.partner_id.value);
 const opened = new Set((subject?.cooperation_projects || []).filter(row => row.enabled !== 0).map(row => row.project));
 const projects = subject?.enabled === 0 ? [] : ['MEMBER', 'KA'].filter(project => opened.has(project) && (form.dataset.sourceKind !== 'API_KA' || project === 'KA'));
 let value = projects.includes(selected) ? selected : '';
 if (!value && chooseDefault) {
  value = projects.includes(subject?.cooperation_project) ? subject.cooperation_project : projects.length === 1 ? projects[0] : '';
 }
 const field = form.elements.sale_project;
 const placeholder = !subject ? '请先选择合作主体' : subject.enabled === 0 ? '该合作主体已停用' : projects.length ? '请选择合作项目' : '该主体暂无已启用的合作项目';
 field.innerHTML = opt('', placeholder, value) + projects.map(project => opt(project, project === 'MEMBER' ? '会员' : 'KA', value)).join('');
 field.disabled = !subject || projects.length === 0;
}

async function demandSyncForm(id,fromExcel=false,fromApi=false){
 if(id){const [catalog,sources]=await Promise.all([api('catalogs'),api('demand-sync/sources')]);state.catalog=catalog;demandSyncSources=sources;}else state.catalog=await api('catalogs');
 const v=demandSyncSources.find(s=>s.id===id)||{id:'',name:fromApi?'易车KA接口需求':'',auth_mode:'NONE',url:fromApi?'http://yiche.asoapple.cn/api/v2/pullKaCrid':'',partner_id:fromApi?(state.catalog.partners.find(p=>p.name.replace(/\s/g,'')==='北京软米科技有限公司(会员2)')?.id||''):'',interval_minutes:30,enabled:1,media_id:fromApi?(state.catalog.partners.find(p=>p.role==='MEDIA'&&p.name==='易车')?.id||''):'',sale_project:fromApi?'KA':''};
 const apiSource=fromApi||v.source_kind==='API_KA';
 const fileSource=v.source_kind==='EXCEL';
 const subjects=state.catalog.partners.filter(p=>['MEDIA','AGENCY'].includes(p.role));
 modal(apiSource?'API拉取需求':fromExcel?'Excel导入需求':v.id?'修改需求归属与设置':'添加腾讯文档项目',`<form id="${fromExcel?'demand-excel-form':'demand-sync-form'}" data-id="${esc(v.id)}" data-version="${v.version||''}" data-source-kind="${apiSource?'API_KA':v.source_kind||'QQ_DOC'}" class="grid-form">
 ${apiSource?`<label class="span2">KA需求接口<select name="url">${opt('http://yiche.asoapple.cn/api/v2/pullKaCrid','HTTP（文档地址）',v.url)}${opt('https://yiche.asoapple.cn/api/v2/pullKaCrid','HTTPS（须甲方确认可用）',v.url)}</select></label><div class="span2 callout">每天北京时间08:00自动拉取全部需求，也可在人工通知后立即拉取。月度总量仅展示；每日量级限制当天提交数量，重新拉取后更新。额度日期必须是北京时间今日，过日须重新拉取。</div>`:fromExcel?'<div class="span2 callout">支持.xlsx，最多5000条需求。必填列：品牌、车型（车系）、城市、下单量（数量）。需求编号选填，未填保留为空；填写时请使用文本格式保留前导零。空白下单日期、开始时间使用上传时间；空白结束时间为待定。<br><a href="/api/v1/demand-excel/template">下载Excel模板</a></div><label class="span2">需求表格<input name="file" type="file" accept=".xlsx" required></label>':`<label class="span2">腾讯文档表格链接<input name="url" type="${fileSource?'text':'url'}" required value="${esc(v.url)}" ${fileSource?'readonly':''} placeholder="https://docs.qq.com/sheet/…?tab=…"></label>`}

 ${v.id&&!fileSource&&!apiSource?'<p class="callout span2">可以修改链接。保存时先校验并读取新表，失败保留原需求；更换文档后旧来源停止匹配并保留历史，新表需求单独接入。</p>':''}
 ${v.replaced_sources?.length?`<details class="span2"><summary>查看旧链接（${v.replaced_sources.length}）</summary>${v.replaced_sources.map(old=>`<p><a href="${esc(old.url)}" target="_blank" rel="noopener noreferrer">${esc(old.name)} · ${esc(old.url)}</a></p>`).join('')}</details>`:''}
 <label>合作主体<select name="partner_id" required>${opt('','请选择合作主体',v.partner_id||'')}${subjects.map(p=>opt(p.id,p.name,v.partner_id)).join('')}</select></label>
 <label>最终媒体<select name="media_id" required>${opt('','请选择最终媒体',v.media_id||'')}${subjects.filter(p=>p.role==='MEDIA').map(p=>opt(p.id,p.name,v.media_id)).join('')}</select></label>
 <label>合作项目<select name="sale_project" required disabled><option value="">请先选择合作主体</option></select><span class="sub">仅显示该主体已开通的合作项目</span></label>
 <label>来源名称<input name="name" required value="${esc(v.name)}" placeholder="例如：湖北会员需求表"></label>
 ${fromExcel||fileSource||apiSource?'':`<label>同步频率（分钟）<input type="number" name="interval_minutes" min="1" max="1440" required value="${v.interval_minutes}"></label>
 <label>定时同步<select name="enabled">${opt('1','开启',String(v.enabled))}${opt('0','暂停',String(v.enabled))}</select></label>`}

 <label>交付路由<select name="route_id">${opt('','按合作主体与最终媒体匹配路由',v.route_id||'')}${state.catalog.routes.filter(r=>r.buyer_id===v.partner_id&&r.media_id===v.media_id).map(r=>opt(r.id,partner(r.receiver_id)+(r.receiver_id!==r.media_id?' → '+partner(r.media_id):' · 直连'),v.route_id)).join('')}</select></label>
 ${apiSource?`<label>接口鉴权<select name="auth_mode">${opt('UNCONFIRMED','待甲方确认（仅保存）',v.auth_mode||'NONE')}${opt('NONE','甲方已确认无需Token',v.auth_mode)}${opt('BEARER','专属Token（需HTTPS）',v.auth_mode)}</select></label><label>专属Token<input type="password" name="bearer_token" autocomplete="new-password" placeholder="${v.has_token?'已保存，留空保持原Token':'不使用文档示例Token'}"></label><input type="hidden" name="quantity_basis" value="DAILY">`:'<label>下单量口径<select name="quantity_basis">'+`${opt('DAILY','每日需求量',v.quantity_basis||'DAILY')}${opt('TOTAL','项目时间内总需求量',v.quantity_basis)}</select></label>`}
 <div class="span2 callout">${apiSource?'已提交的失败或未知结果也占当天提交次数；未发送取消会释放预占。同编号更新，保留历史版本；全量结果中已消失的需求停止新分发。':`每日需求量按北京时间跨日重新计算；项目时间内总需求量在项目期间累计计算。${fromExcel?'需求编号选填；已填写的编号不可重复，已有媒体需求不会覆盖。':'按原表需求编号更新，不重复新增。修改归属只影响该来源的未发送需求；有发送记录时需另建来源，保留原交付。'}`}</div><div class="form-actions span2"><button type="button" data-action="close-modal">取消</button><button class="primary" type="submit">${apiSource?v.id?'保存设置':'保存并按鉴权配置拉取':fromExcel?'开始预检':v.id?'保存设置':'添加并首次同步'}</button></div></form>`,true);
 setDemandSourceProjects($('#'+(fromExcel?'demand-excel-form':'demand-sync-form')),v.sale_project||'');
}
document.addEventListener('change',e=>{const f=e.target.closest('#demand-sync-form,#demand-excel-form');if(!f||!['partner_id','media_id'].includes(e.target.name))return;const p=state.catalog.partners.find(p=>p.id===f.elements.partner_id.value);if(e.target.name==='partner_id'){setDemandSourceProjects(f,'',true);if(!p)f.elements.media_id.value='';}if(e.target.name==='partner_id'&&p){if(p.role==='MEDIA')f.elements.media_id.value=p.id;else if(p.role==='AGENCY'&&p.final_media_ids?.length)f.elements.media_id.value=p.final_media_ids[0]}const routes=state.catalog.routes.filter(r=>r.buyer_id===f.elements.partner_id.value&&r.media_id===f.elements.media_id.value);f.elements.route_id.innerHTML=opt('','按合作主体与最终媒体匹配路由')+routes.map(r=>opt(r.id,partner(r.receiver_id)+(r.receiver_id!==r.media_id?' → '+partner(r.media_id):' · 直连'))).join('')});

let demandSummaryRows=[];
const summaryStatus=s=>s==='WAITING'?'未开始':s==='MIXED'?'无执行中需求':labels[s]||s;
function demandSummaryTable(rows){return table(['合作主体','最终媒体','项目','执行中需求（项）','目标线索量（条）','有效 / 在途 / 可用','需求进度','进展程度','状态','匹配方式','操作'],rows.map((g,i)=>[
 esc(g.buyer_name),esc(g.media_name),g.project==='MEMBER'?'会员':g.project==='KA'?'KA':'待设置',String(g.active_count),String(g.target_total),`${g.valid_count} / ${g.reserved_count} / ${g.available_count}`,g.target_total?`<div class="track"><div class="fill" style="width:${g.attainment_rate}%"></div></div><span class="sub">${g.attainment_rate.toFixed(1)}% · ${g.valid_count} / ${g.target_total}</span>`:'—',g.active_count?urgency(g):'—',g.status==='WAITING'||g.status==='MIXED'?`<span class="badge blue">${esc(summaryStatus(g.status))}</span>`:badge(g.status),['MEMBER','KA'].includes(g.project)?`<select data-project-mode data-partner="${esc(g.buyer_id)}" data-project="${g.project}" data-version="${g.mode_version}" aria-label="${esc(g.buyer_name)}${g.project==='MEMBER'?'会员':'KA'}匹配方式" ${!has('dispatch')?'disabled':''}>${opt('MANUAL','手动',g.matching_mode)}${opt('AUTO','自动',g.matching_mode)}</select>`:'待设置项目',`<button class="link" data-action="demand-summary-detail" data-id="${i}">查看汇总</button>`
 ]),'暂无符合条件的需求汇总')}
async function demands(){
 const params=new URLSearchParams({page_size:state.size,page:state.page,q:state.q,status:state.status,media:state.media});
 const [d,sync]=await Promise.all([api('demands/summary?'+params),demandSyncPanel()]);demandSummaryRows=d.items;
 return heading('DEMAND POOL / 需求池','把需求变成可追踪的交付','易车需求配置专用字典 ID 和 crId；交付编号 orderId 在分发时自动生成。')+sync+`<section class="card" id="demand-summary-card"><div class="card-head"><h2>全部需求 · 匹配与交付</h2></div>${filters(['ACTIVE','DRAFT','PENDING_REVIEW','PAUSED','COMPLETED','CLOSED','WAITING','MIXED'],mediaFilter(),'搜索合作主体、最终媒体或项目')}<p class="smalltext muted" style="padding:0 20px">仅展示已启用的主体项目，暂停项目可在合作与渠道管理。按合作主体、最终媒体和项目汇总。需求数量以项计，目标线索量以条计。数量及进度仅统计执行中需求；每日需求按今日、项目总量按累计计算。</p>${demandSummaryTable(d.items)}${pagination(d)}</section>`;
}

async function autoPanel(){if(!has('dispatch'))return '';const a=await api('distribution/auto');return `<section class="card"><div class="card-head"><div><h2>自动分发 · ${a.enabled?'已开启':'已关闭'}</h2><span class="sub">每30秒扫描；唯一匹配或人工指定需求时执行；使用与手动分发相同的规则</span></div><div class="actions"><button data-action="auto-bind-selected">将所选线索绑定到所选需求</button><button data-action="auto-run">立即扫描</button></div></div><div class="card-body"><p class="smalltext muted">请在需求详情设置项目并开启自动接收，在系统设置开启自动分发。多个候选需求先交人工决定，星级与价格不参与排序。自动任务仅执行本地模拟发送。</p></div>${table(['线索编号','处理状态','候选需求 / 拦截原因','指定需求','操作'],a.items.map(x=>[esc(x.lead_no),esc(({WAITING_MANUAL:'待人工分配',BLOCKED:'暂不可分发',DISPATCHED:'已创建分发'})[x.status]||x.status),x.candidates.map(d=>`${esc(d.demand_no||'—')}${d.reason?'：'+esc(reasons[d.reason]||d.reason):' · '+(d.project==='KA'?'KA':'会员')}`).join('<br>')||esc(reasons[x.reason]||x.reason),x.status==='WAITING_MANUAL'?`<select data-auto-demand="${esc(x.lead_id)}">${x.candidates.map(d=>opt(d.demand_id,d.demand_no)).join('')}</select>`:esc(x.binding||'—'),x.status==='WAITING_MANUAL'?`<button class="small" data-action="auto-bind-one" data-id="${esc(x.lead_id)}">指定并分发</button>`:'—']))}</section>`}
let matchingFilters={};
function matchingFilterBar(data){
 leadBrands=data.filter_options.brand||[];leadModels=data.filter_options.model||[];
 const fields=new Set(data.columns.map(c=>c.key));
 const inputs=[];
 if(fields.has('lead_no'))inputs.push(`<label>编号<input name="lead_no" placeholder="输入线索编号" value="${esc(matchingFilters.lead_no)}"></label>`);
 for(const [key,label] of [['city','城市'],['brand','品牌'],['model','车型'],['sales_count','匹配媒体数量'],['matched_media','已匹配媒体'],['project','项目']]){
  if(key==='matched_media'?!data.filter_options.matched_media:!fields.has(key))continue;
  inputs.push(key==='brand'?leadBrandPicker(matchingFilters):`<label>${label}<select name="${key}">${key==='model'?leadModelOptions(matchingFilters.brand,matchingFilters.model):opt('','全部'+label)+(data.filter_options[key]||[]).map(o=>opt(o.value,o.label,matchingFilters[key]||'')).join('')}</select></label>`);
 }
 return `<div class="toolbar"><form id="matching-search-form" class="lead-filter-form">${inputs.join('')}<div class="actions"><button type="submit">筛选</button><button type="button" data-action="matching-reset">重置</button></div></form></div>`;
}
async function allActiveDemands(){const items=[];for(let page=1;page<=200;page++){const d=await api('demands?page_size=100&status=ACTIVE&page='+page);items.push(...d.items);if(!d.has_more)break}return {items}}
function procurementProjects(){return state.catalog.partners.filter(p=>['MEDIA','AGENCY'].includes(p.role)).flatMap(p=>(p.cooperation_projects||[]).map(config=>({...p,...config,subject_enabled:!!p.enabled,project_enabled:config.enabled!==0,enabled:!!p.enabled&&config.enabled!==0,key:p.id+'|'+config.project})));}
function buyerDemands(key){const [id,project]=key.split('|'),p=state.catalog.partners.find(p=>p.id===id);return p?.enabled?state.activeDemands.filter(d=>d.buyer_id===id&&(!project||d.sale_project===project||(d.sale_project==='UNKNOWN'&&project===p.cooperation_project))):[];}
function resolveBuyerSelection(){state.selectedDemands=new Set([...state.selectedBuyers].flatMap(id=>buyerDemands(id).map(d=>d.id)))}
function selectedAutoProject(){return procurementProjects().some(p=>p.enabled&&state.selectedBuyers.has(p.key)&&p.matching_mode==='AUTO')}
async function matching(){
 const available=new Set(procurementProjects().filter(p=>p.enabled).map(p=>p.key));state.selectedBuyers=new Set([...state.selectedBuyers].filter(key=>available.has(key)));
 const demandRequest=allActiveDemands();
 const makeParams=()=>new URLSearchParams({page_size:state.size,page:state.page,...matchingFilters,selected_ids:[...state.selected].join(','),buyer_ids:[...state.selectedBuyers].join(',')});
 let d,ls;
 if(!state.selectedBuyers.size&&state.selectedDemands.size){
  d=await demandRequest;for(const dm of d.items)if(state.selectedDemands.has(dm.id)){state.selectedBuyers.add(dm.buyer_id+'|'+(dm.sale_project==='UNKNOWN'?'MEMBER':dm.sale_project));break;}
  ls=await api('matches/leads?'+makeParams());
 }else [d,ls]=await Promise.all([demandRequest,api('matches/leads?'+makeParams())]);
 state.activeDemands=d.items;resolveBuyerSelection();state.selected=new Set(ls.eligible_selected_ids);state.matchingFilteredIds=ls.filtered_ids;
 return heading('MATCH & DISTRIBUTE / 匹配分发','选择采购主体，匹配并分发线索','默认展示全部可分发线索；选择采购主体后，按该主体的需求及分发规则筛选。')
 +`<div class="callout">累计售卖上限：每条线索最多 ${state.catalog.settings.max_media_count} 次（含历史售卖）；KA最多 ${state.catalog.settings.ka_max_count} 次。去重窗口 ${state.catalog.settings.dedup_window_days} 天。当前仅发送到本地模拟器。</div>
 <div class="matching-layout buyer-first-layout"><section class="card"><div class="card-head"><h2>① 选择采购主体</h2><button class="link tiny" data-action="matching-all-buyers">全部主体</button></div><div class="card-body"><input id="matching-demand-search" placeholder="搜索合作主体或合作项目" aria-label="搜索采购主体"><div class="matching-demand-options">${procurementProjects().filter(p=>p.enabled).map(p=>{const ds=buyerDemands(p.key),project=p.project==='MEMBER'?'会员':'KA';return `<label class="work-item" data-demand-search="${esc(p.name+' '+project)}"><input type="radio" name="matching-buyer" data-check-buyer="${esc(p.key)}" ${state.selectedBuyers.has(p.key)?'checked':''} ${!p.enabled?'disabled':''}><div><p class="name-primary">${esc(p.name)} · ${project}</p><small>${p.role==='MEDIA'?'媒体':'乙方'} · ${p.matching_mode==='AUTO'?'自动匹配':'手动匹配'}</small><p class="tiny muted">${!p.enabled?'已停用':ds.length+' 项进行中需求'}</p></div></label>`}).join('')||'<p class="muted">暂无线索供应合作主体</p>'}</div></div></section>
 <section class="card lead-pool-card"><div class="card-head"><div><h2>② 选择线索</h2><span id="selected-note" class="sub">已选择 ${state.selected.size} 条线索</span></div><div class="actions"><button data-action="matching-select-all" ${!ls.total?'disabled':''}>全选筛选结果（${ls.total} 条）</button><button data-action="matching-clear-selection">取消全选</button></div></div>
 ${matchingFilterBar(ls)+table(['选择',...ls.columns.map(c=>esc(c.label))],ls.items.map(l=>[`<input type="checkbox" data-check-lead="${esc(l.id)}" ${state.selected.has(l.id)?'checked':''} aria-label="选择${esc(l.lead_no||'线索')}">`,...ls.columns.map(c=>leadCell(l,c.key))]),'暂无符合该采购主体需求及分发规则的线索')+pagination(ls)}
 ${selectedAutoProject()?'<p class="card-body muted">此项目为自动匹配，系统每30秒扫描；多个需求同时符合时等待指定。可在需求池切换匹配方式。</p>':''}<div class="card-body form-actions"><button data-action="preview" class="primary" ${!state.selected.size||!state.selectedDemands.size||selectedAutoProject()?'disabled':''}>③ 生成匹配预览 →</button></div></section></div>`+await autoPanel();
}

let yicheCodeGroups=[];
async function yicheCodes(){
 const rows=await api('yiche-codes');
 const groups=new Map();
 for(const {code,status,description} of rows){
  const key=JSON.stringify([status,description]);
  if(!groups.has(key))groups.set(key,{status,description,codes:[]});
  groups.get(key).codes.push(code);
 }
 yicheCodeGroups=[...groups.values()];
 const editable=has('settings');
 return heading('CALLBACK CODES / 易车回调','Code 码管理','查看易车回调原始码对应的线索结果和原因。',
  editable?'<button class="primary" data-action="yiche-code-add">＋ 新增 Code 码</button>':'')
 +`<section class="card"><div class="card-head"><h2>易车回调码字典</h2><span class="muted">共 ${rows.length} 个码</span></div>
 ${table(['媒体','Code 码','分发结果','原因',...(editable?['操作']:[])],yicheCodeGroups.map((r,i)=>[
  '易车',`<span class="mono">${esc(r.codes.join('/'))}</span>`,badge(r.status),esc(r.description),
  ...(editable?[`<button class="link" data-action="yiche-code-edit" data-id="${i}">编辑</button>
   <button class="link" data-action="yiche-code-delete" data-id="${i}">删除</button>`]:[])
 ]),'暂无 Code 码')}</section>`;
}
function yicheCodeForm(action,index){
 const group=yicheCodeGroups[index];
 const deleting=action==='delete';
 const fields=deleting?`<p>确定删除易车 Code 码 <strong>${esc(group.codes.join('/'))}</strong>？</p>
  <p class="muted">后续收到这些 Code 码时，会标记为异常待核对。</p>`:
  `<label class="span2">Code 码<input name="codes" value="${esc(group?.codes.join('/')||'')}" placeholder="例如 -14/1" required></label>
   <label>分发结果<select name="status">${opt('VALID','有效',group?.status||'INVALID')}${opt('INVALID','无效',group?.status||'INVALID')}</select></label>
   <label>原因<input name="description" value="${esc(group?.description||'')}" maxlength="200" required></label>`;
 modal(({add:'新增',edit:'编辑',delete:'删除'})[action]+'易车 Code 码',
  `<form id="yiche-code-form" data-mode="${action}" class="grid-form">
   <div class="span2">${deleting?fields:'多个 Code 码用 / 隔开。'}</div>
   ${deleting?'':fields}
   <div class="form-actions span2"><button type="button" data-action="close-modal">取消</button>
    <button type="submit" class="${deleting?'danger':'primary'}">${deleting?'确认删除':'保存'}</button></div>
  </form>`);
 $('#yiche-code-form').previousGroup=group;
}
function yicheCodeLabel(d){
 return d.yiche_code?`<span class="sub">易车 Code ${esc(d.yiche_code.code)} · ${esc(d.yiche_code.description)}</span>`:'';
}
function deliveryTable(rows){return table(['线索 / 需求','最终媒体与路径','分发方式 / 规则版本','上报状态','媒体入库','最终有效','回调完整性','操作'],rows.map(d=>[`<span class="name-primary">${esc(d.lead_no)}</span><span class="sub mono">${esc(d.demand_no||'—')}</span>`,`${esc(partner(d.media_id))}<span class="sub">${d.receiver_id===d.media_id?'直连':'经 '+esc(partner(d.receiver_id))}</span>`,`${d.dispatch_mode==='AUTO'?'自动分发':'手动分发'}<span class="sub">规则 ${d.rule_version?'v'+d.rule_version:'历史记录'}</span>`,badge(d.send_status),badge(d.storage_status),badge(d.validity_status)+yicheCodeLabel(d),badge(d.callback_status),`<button class="link" data-action="delivery-detail" data-id="${d.id}">完整链路 →</button>${has('write')&&d.sale_project==='UNKNOWN'&&d.first_sent_at?`<button class="link" data-action="delivery-project" data-id="${esc(d.id)}">补录项目</button>`:''}`]))}
async function deliveries(){const d=await list('deliveries');return heading('DELIVERY TRACKING / 分发记录','看清来源，也看清每一站去向','接收成功、媒体入库和最终有效独立展示，不相互覆盖。',`<button data-action="refresh">↻ 刷新</button>${has('dispatch')?'<button class="primary" data-action="run-worker">执行待发送任务</button>':''}`)+`<section class="card">${filters(['QUEUED','ACKED','UNKNOWN','CANCELED'],mediaFilter())}${deliveryTable(d.items)}${pagination(d)}</section>`}
async function imports(){
 const [data,entryOptions]=await Promise.all([list('imports'),api('lead-entry-options')]);
 return heading('IMPORT CENTER / 表格导入','表格导入线索','支持 Excel 和 CSV，预检通过后即可入库分发。',
  '<a class="btn" href="/api/v1/template?format=csv">下载 CSV 模板</a><a class="btn primary" href="/api/v1/template">下载 Excel 模板</a>')
 +`<section class="card"><div class="card-head"><h2>上传线索表格</h2><span class="tiny muted">单文件 ≤ 10MB · 最多 50,000 行</span></div>
 <div class="card-body"><form id="import-form" class="grid-form">
  <label>线索供应商<select name="supplier_id" required>${opt('','请选择线索供应商')}${(entryOptions.suppliers||[]).map(row=>opt(row.id,row.name)).join('')}</select><span class="sub">在合作与渠道的线索采购中维护；本次导入全部归属所选供应商</span></label>
  <label>选择表格文件<input name="file" type="file" accept=".xlsx,.csv" required></label>
  <label>工作表名称（选填）<input name="sheet" placeholder="留空使用第一个工作表"></label>
  <div class="span2 smalltext muted">必填姓名、电话、品牌、车型、城市、是否售卖。是否售卖填“是”时，售卖媒体、项目（KA 或会员）、售卖时间也必填；填“否”时这三项可留空。媒体请填平台媒体编号或完整名称。</div>
  <div class="span2"><button class="primary" type="submit">开始预检</button></div>
 </form></div></section>
 <section class="card"><div class="card-head"><h2>导入历史</h2></div>
 ${table(['文件','预检结果','状态','创建时间','操作'],data.items.map(row=>{
  const preview=JSON.parse(row.preview);
  return [esc(row.filename),`合格 ${preview.valid} · 重复 ${preview.duplicates} · 错误 ${preview.errors}`,badge(row.status),fmt(row.created_at),
   `<button class="link" data-action="import-result" data-id="${esc(row.id)}">查看结果</button>`];
 }))}${pagination(data)}</section>`;
}
const acquisitionLabels=new Map([['ADS','投放'],['PURCHASE','采买'],['TMK','TMK']]);
function acquisitionMethods(){
 return [...new Set([...acquisitionLabels.keys(),...state.catalog.channels.map(row=>row.type)])];
}
function acquisitionLabel(type){
 return acquisitionLabels.get(type)??type;
}
function apiStatusBadge(row){return `<span class="badge ${row.api_status==='CONNECTED'?'green':'gold'}" title="${row.api_status==='CONNECTED'?'存在该渠道或主体项目的实际线索API成功记录；不代表当前已启用分发':'未对接或尚无可核验的线索API联调成功记录'}">${row.api_status==='CONNECTED'?'接通':'未接通'}</span>`;}
async function partners(){
 const editable=has('settings');
 const price=row=>row.cooperation_price_fen==null?'—':(row.cooperation_price_fen/100).toFixed(2);
 const toggle=(kind,row)=>editable?`<button class="link" data-action="toggle-${kind}" data-id="${esc(row.id)}" data-enabled="${row.enabled}">${row.enabled?'停用':'启用'}</button>`:'只读';
 return heading('PARTNERS & CHANNELS / 合作与渠道','合作与渠道','管理线索采购、线索供应和交付路由。',
  editable?'<button class="primary" data-action="new-config" data-kind="routes">＋ 交付路由</button>':'')
 +`<section class="card"><div class="card-head"><h2>线索采购</h2>
 ${editable?'<button data-action="new-config" data-kind="channels">＋ 供应商</button>':''}</div>
 ${table(['供应商名称','渠道号','获客方式','合作价格（元）','状态','API状态','操作','备注说明'],state.catalog.channels.map(row=>[
  esc(partner(row.source_partner_id)),esc(row.id),`<span class="badge blue">${esc(acquisitionLabel(row.type))}</span>`,price(row),
  badge(row.enabled?'ACTIVE':'PAUSED'),apiStatusBadge(row),`<div class="partner-row-actions">${toggle('channel',row)+(editable?`<button class="link" data-action="source-api" data-id="${esc(row.id)}">API 对接</button>`:'')}</div>`,esc(row.cooperation_note||'—')
 ]))}</section>
 <section class="card"><div class="card-head"><h2>线索供应</h2>
 ${editable?'<button data-action="new-config" data-kind="partners">＋ 合作主体</button>':''}</div>
 ${table(['主体名称','合作角色','合作项目','最终媒体','合作价格（元）','合同截止','状态','API状态','匹配方式','操作','备注说明'],
  procurementProjects().map(row=>[
   esc(row.name),badge(row.role==='MEDIA'?'媒体':'乙方'),row.project==='MEMBER'?'会员':'KA',esc(row.role==='MEDIA'?row.name:(row.final_media_ids||[]).map(partner).join('、')||'待设置'),price(row),
   fmt(row.contract_end),row.subject_enabled?badge(row.project_enabled?'ACTIVE':'PAUSED'):'<span class="badge gold">主体已停用</span>',apiStatusBadge(row),`<select data-project-mode data-partner="${esc(row.id)}" data-project="${row.project}" data-version="${row.version}" aria-label="${esc(row.name)}${row.project==='MEMBER'?'会员':'KA'}匹配方式" ${!has('dispatch')||!row.enabled?'disabled':''}>${opt('MANUAL','手动',row.matching_mode)}${opt('AUTO','自动',row.matching_mode)}</select>`,`<div class="partner-row-actions">${(editable?`<button class="link" data-action="toggle-partner-project" data-id="${esc(row.id)}" data-project="${esc(row.project)}" data-version="${row.version}" data-enabled="${row.project_enabled?'1':'0'}">${row.project_enabled?'停用项目':'启用项目'}</button>`:'只读')+(editable?`<button class="link" data-action="partner-edit" data-id="${esc(row.id)}">编辑主体</button>`:'')+(editable&&(row.cooperation_projects||[]).length<2?`<button class="link" data-action="partner-project-add" data-id="${esc(row.id)}">添加项目</button>`:'')}</div>`,esc(row.cooperation_note||'—')
  ]))}</section>
 <section class="card"><div class="card-head"><h2>交付路由与模拟器</h2><span class="tiny muted">超时模式只影响后续新建交付快照</span></div>${table(['路径','接入方式','模拟行为','能力','操作'],state.catalog.routes.map(r=>[`${esc(partner(r.receiver_id))}${r.receiver_id!==r.media_id?' → '+esc(partner(r.media_id)):''}`,badge(r.receiver_id===r.media_id?'甲方直连':'乙方转发'),badge(r.mode==='TIMEOUT'?'UNKNOWN':'ACKED'),`业务幂等 ${r.supports_idempotency?'✓':'—'} · 查询 ${r.supports_query?'✓':'—'}`,has('engineering')?`<button class="link" data-action="route-config" data-id="${r.id}">配置模拟器</button>`:'只读']))}</section>`}
async function exceptions(){const ds=await api('deliveries?page_size=100'),cs=await api('callback_events?page_size=100'),inc=await api('integrity');return heading('EXCEPTION CENTER / 异常中心','异常有记录，处理有依据','结果未知不会自动释放配额，也不会在不安全的情况下盲目重发。',has('engineering')?'<button data-action="maintenance">检查超时</button><button class="primary" data-action="process-inbox">处理已持久化回调</button>':'')+`<div class="callout ${inc.passed?'':'error'}">${inc.passed?'✓ 数据一致性检查正常':'发现账本差异，请暂停新增分发并核验'} · 配额、账本、交付记录与关联完整性实时比对。</div><section class="card"><div class="card-head"><h2>异常交付</h2></div>${deliveryTable(ds.items.filter(x=>x.send_status==='UNKNOWN'||['TIMEOUT','CONFLICT'].includes(x.callback_status)))}</section><section class="card"><div class="card-head"><h2>待处理与冲突回调</h2></div>${table(['事件编号','事件类型','处理状态','原因','接收时间'],cs.items.filter(x=>['PENDING','CONFLICT','UNMATCHED'].includes(x.status)).map(x=>[esc(x.event_id),esc(x.event_type),badge(x.status),esc(x.error||'等待链路关联或处理'),fmt(x.received_at)]))}</section>`}
async function reports(){const r=await api('reports/funnel'),m=r.metrics;return heading('REPORTS & INSIGHTS / 数据报表','统一口径，才能准确判断质量','当前报表按全部首次发送队列统计，截至最新结果；未回调不会被当作无效。',has('export')?'<button class="primary" data-action="export-request">申请导出脱敏明细</button>':'')+`<div class="stats">${stat('接收成功率',pct(m.acked,m.sent),m.acked+' / '+m.sent+' 笔首次发送','↗')}${stat('最终入库率',pct(m.stored,m.sent),m.stored+' / '+m.sent+' 笔首次发送','▤')}${stat('最终有效率',pct(m.valid,m.sent),'待判定 '+m.pending+' 笔','✓')}${stat('线索多售倍数',r.multisale.toFixed(2)+'×',r.unique_sent_leads+' 条唯一已发送线索','⇄')}</div>${has('finance')?`<div class="callout">预估收入 ${money(r.income_fen)}　·　已知归因成本 ${money(r.cost_fen)}　·　贡献 ${money(r.income_fen-r.cost_fen)}　·　成本未知线索 ${r.unknown_cost_leads} 条。模拟回执产生测试预估值，不形成真实应收。</div>`:''}<section class="card"><div class="card-head"><h2>来源 × 最终媒体表现</h2><span class="tiny muted">同一线索多媒体分发按业务交付分别计数</span></div>${table(['来源渠道','最终媒体','首次发送','接收成功','入库','有效','待判定','乙方转述'],r.groups.map(g=>[esc(channel(g.channel_id)),esc(partner(g.media_id)),g.sent,`${g.acked} / ${pct(g.acked,g.sent)}`,g.stored,g.valid,g.pending,g.agency_reported]))}</section><section class="card"><div class="card-head"><h2>指标说明</h2></div><div class="card-body"><p class="smalltext muted">业务分发次数与网络尝试次数分开；唯一线索人数按线索去重；成本按唯一获客线索只计算一次；乙方转述与媒体原始证据分开。当前版本提供全量队列统计，日期队列、日报快照与延迟分位数仍待扩展验证。</p></div></section>`}
async function governance(){const ls=await api('leads?page_size=100'),xs=await api('exports?page_size=100'),ps=await api('privacy_requests?page_size=100');let ns=[];if(has('privacy'))ns=await api('privacy-notices');return heading('CONSENT & GOVERNANCE / 授权与治理','授权可核验，撤回可执行','授权核验、明文查看、导出审批与停止处理均留有审计记录。')+`<section class="card"><div class="card-head"><h2>待核验线索</h2><span class="tiny muted">操作需切换到合规专员账号</span></div>${table(['线索','车型','当前原因','已提交授权','操作'],ls.items.filter(x=>x.status==='QUARANTINED').map(x=>[esc(x.lead_no),esc(series(x.series_id)),esc(x.reason),(x.consent_count??0)+' 个接收主体',has('consent')?`<button class="small" data-action="review" data-id="${x.id}">核验授权</button>`:'无核验权限']))}</section><section class="card"><div class="card-head"><h2>导出申请</h2><span class="tiny muted">申请人不能自审 · 审批后15分钟内单次下载</span></div>${table(['用途','申请人','状态','时间','操作'],xs.items.map(x=>[esc(x.purpose),esc(roles[x.created_by.replace('u_','')]||x.created_by),badge(x.status),fmt(x.created_at),x.status==='PENDING'&&has('export_approve')?`<button class="small" data-action="approve-export" data-id="${x.id}">批准</button>`:x.status==='APPROVED'&&x.created_by===state.user.id&&!x.used?`<a class="btn small" href="/api/v1/exports/${x.id}/download">下载脱敏文件</a>`:'—']))}</section><section class="card"><div class="card-head"><h2>撤回与删除请求</h2></div>${table(['类型','请求编号','办理状态','申请时间'],ps.items.map(x=>[x.type==='DELETE'?'删除':'撤回',esc(x.id),badge(x.status),fmt(x.created_at)]))}</section><section class="card"><div class="card-head"><h2>接收方停止处理通知</h2><span class="tiny muted">测试确认只记录模拟接收结果</span></div>${table(['接收方','类型','状态','操作'],ns.map(x=>[esc(partner(x.recipient_id)),x.type==='DELETE'?'删除':'撤回',badge(x.status),x.status==='PENDING'?`<button class="small" data-action="confirm-notice" data-id="${x.id}">模拟接收方确认</button>`:'已确认']))}</section>`}
const ruleNames={max_media_count:'累计售卖上限（次）',dedup_window_days:'去重窗口（天）',ka_max_count:'KA售卖上限（次）',forbid_same_media:'禁止同媒体重复售卖（1开启/0关闭）',auto_dispatch_enabled:'自动分发（1开启/0关闭）',dispatch_enabled:'允许分发（1开启/0关闭）'};
function ruleChanges(e){const f=e.target.closest('#settings-form');if(!f)return;const old=state.catalog.settings;$('#rule-changes').innerHTML='<h3>修改前 / 修改后</h3>'+table(['规则','修改前','修改后'],Object.entries(ruleNames).filter(([k])=>Number(f.elements.namedItem(k).value)!==old[k]).map(([k,n])=>[esc(n),old[k],esc(f.elements.namedItem(k).value)]),'尚未修改规则');}
document.addEventListener('input',ruleChanges);document.addEventListener('change',ruleChanges);
async function settings(){const s=state.catalog.settings;let audit={items:[]};if(has('distribution_rules'))audit=await api('audit_log?page_size=20');state.ruleAuditItems=audit.items;return heading('SYSTEM SETTINGS / 系统设置','业务规则，由你掌控','修改配置会记录版本和操作者；历史交付与价格快照保持不变。')+`<section class="card"><div class="card-head"><h2>分发规则</h2><span class="badge blue">当前版本 ${s.version}</span></div><div class="card-body"><form id="settings-form" class="grid-form"><label>单条线索累计售卖上限（次）<input type="number" name="max_media_count" min="1" value="${s.max_media_count}" required ${!has('distribution_rules')?'disabled':''}></label><label>去重窗口（天）<input type="number" name="dedup_window_days" min="1" max="3650" value="${s.dedup_window_days}" required ${!has('distribution_rules')?'disabled':''}></label><label>KA累计售卖上限（次）<input type="number" name="ka_max_count" min="1" max="100" value="${s.ka_max_count}" required ${!has('distribution_rules')?'disabled':''}></label><label>同一最终媒体重复售卖<select name="forbid_same_media" ${!has('distribution_rules')?'disabled':''}>${opt('1','禁止',String(s.forbid_same_media))}${opt('0','允许（仍受总次数限制）',String(s.forbid_same_media))}</select></label><label>自动分发<select name="auto_dispatch_enabled" ${!has('distribution_rules')?'disabled':''}>${opt('0','关闭',String(s.auto_dispatch_enabled))}${opt('1','开启 · 每30秒本地扫描',String(s.auto_dispatch_enabled))}</select></label><label>全局分发状态<select name="dispatch_enabled" ${!has('distribution_rules')?'disabled':''}>${opt('1','允许分发',String(s.dispatch_enabled))}${opt('0','暂停新分发',String(s.dispatch_enabled))}</select></label><label class="span2">修改原因<input name="reason" placeholder="请填写业务原因，操作将记录审计" required ${!has('distribution_rules')?'disabled':''}></label><div class="span2" id="rule-changes"><h3>修改前 / 修改后</h3><p class="smalltext muted">调整参数后将在这里显示变更。</p></div><div class="span2 callout warning">累计次数不会随去重窗口结束而清零。同一手机号＋品牌的其他线索在窗口内共享售卖限制，跨车型、渠道同样检查。保存后对后续分发生效，并重新检查未发送任务；已发送历史保留。自动分发开启后按相同规则执行。</div>${has('distribution_rules')?'<div class="span2"><button type="submit" class="primary">保存分发策略</button></div>':'<p class="muted">当前账号仅可查看，请用管理员账号修改。</p>'}</form></div></section><section class="card"><div class="card-head"><h2>本地测试环境</h2>${has('distribution_rules')?'<button class="small" data-action="backup">创建加密备份</button>':''}</div><div class="card-body"><p class="smalltext muted">数据保存在本机独立数据库，刷新页面不会丢失。本次本地预览未启用真实媒体发送，自动分发仅执行本地模拟任务。本地密钥与备份不得用于正式生产；生产部署需配置数据库服务、身份服务、密钥托管和真实适配器。</p></div></section>${has('distribution_rules')?`<section class="card"><div class="card-head"><h2>最近操作审计</h2></div>${table(['操作','操作者','关联记录','时间','变更详情'],audit.items.map(x=>[esc(x.action),esc(x.actor_id),`<span class="mono">${esc(x.target_id)}</span>`,fmt(x.created_at),x.action==='DISTRIBUTION_RULES_UPDATED'?`<button class="link" data-action="rule-history" data-id="${esc(x.id)}">查看变更</button>`:'—']))}</section>`:''}`}
async function acceptance(){const r=await api('acceptance'),inc=await api('integrity');return heading('ACCEPTANCE GUIDE / 验收向导','用真实业务动作，验证系统','自动检查与人工业务验收分开；绿色测试结果不代表真实媒体接口已接通。',`<button data-action="integrity" class="primary">检查当前数据库一致性</button>`)+`<div class="callout ${inc.passed?'':'error'}">当前数据库一致性：${inc.passed?'全部通过':'发现差异'}。自动验收运行时间：${fmt(r.generated_at)}。</div><div class="stats">${stat("业务检查",(r.passed||0)+" / "+(r.total||0),"独立临时数据库及模拟接口","✓")}${stat("权限与安全检查",(r.security?.passed||0)+" / "+(r.security?.total||0),"会话、审批、删除及越权检查","⊙")}${stat("页面联动检查",r.interaction?.checks?.length||0,"页面事件与本地接口已联动","⇄")}${stat("浏览器排版验收","待人工确认","自动浏览器启动受环境限制","◇")}</div><section class="card"><div class="card-head"><h2>建议你亲自验证这五步</h2><span class="badge gold">使用测试数据</span></div>${[['下载并导入一份Excel','进入表格导入，下载模板。模板含一条正确记录和一条错误号码，检查预检分类与入库结果。','imports'],['选择5条线索，匹配两家媒体','进入匹配分发，点击选择示例前5条，选择两项直连需求。应产生最多10笔合规候选，提交后记录独立。','matching'],['模拟媒体入库和有效回调','进入分发记录，执行待发送任务，打开完整链路。先模拟入库，再模拟有效，观察状态和配额。','deliveries'],['修改媒体数量上限','以管理员账号进入系统设置修改上限，返回匹配分发验证。历史交付仍应可追溯。','settings'],['切换账号核验授权与导出','运营账号申请导出；切换合规账号审核；回到申请人账号下载。本人不能审批自己的申请。','governance']].map(([n,t,v],i)=>`<div class="acceptance-step"><span class="step-number">${i+1}</span><div><h3>${n}</h3><p>${t}</p></div><button class="small" data-nav="${v}">前往 →</button></div>`).join('')}</section><section class="card"><div class="card-head"><h2>自动化验收结果</h2><span class="badge ${r.failed?'red':'green'}">${r.passed||0} / ${r.total||0} 通过</span></div>${table(['编号','业务验证','结果','证据'],(r.cases||[]).map(x=>[esc(x.id),esc(x.name),badge(x.passed?'APPROVED':'ERROR'),`<span class="smalltext">${esc(x.evidence||x.error||'独立临时数据库验证')}</span>`]))}</section><section class="card"><div class="card-head"><h2>正式上线前仍需完成</h2><span class="badge gold">未验收</span></div><div class="card-body"><p class="smalltext muted">真实媒体及乙方接口联调、生产身份认证和MFA、PostgreSQL/Redis部署、密钥托管、法务与授权文本、规模压测及异地恢复。当前版本面向本机业务验证，不能直接导入真实客户资料投入生产。</p></div></section>`}
function genderOptions(selected='UNKNOWN'){
 const values={'男':'MALE','女':'FEMALE','未登记':'UNKNOWN','先生':'MALE','女士':'FEMALE','':'UNKNOWN'};
 selected=values[selected]||selected;
 return opt('UNKNOWN','未登记',selected)+opt('MALE','男',selected)+opt('FEMALE','女',selected);
}
function entryPicker(id,name,label,limit){
 return `<div class="lead-brand-picker entry-picker" id="${id}-picker">
  <label for="${id}">${label}</label>
  <div class="lead-brand-control">
   <input id="${id}" name="${name}" role="combobox" aria-autocomplete="list" aria-expanded="false"
    aria-controls="${id}-options" autocomplete="off" placeholder="选择或手动输入${label}" maxlength="${limit}" required>
   <button type="button" aria-label="展开${label}" tabindex="-1">▾</button>
  </div>
  <div id="${id}-options" class="editable-options" role="listbox" aria-label="${label}选项" hidden></div>
 </div>`;
}
let entryModels=[];
async function newLead(){
 const entryOptions=await api('lead-entry-options');entryModels=entryOptions.models;
 const brands=[...new Set(entryModels.map(model=>model.brand_name))];
 modal('录入线索',`<div class="callout">填写必填信息后即可入库分发。已售卖时，请补充媒体、项目和售卖时间。</div>
 <form id="lead-form" class="grid-form">
  <input type="hidden" name="source_record_id" value="MANUAL-${crypto.randomUUID()}">
  <label>线索供应商<select name="supplier_id" required>${opt('','请选择线索供应商')}${(entryOptions.suppliers||[]).map(row=>opt(row.id,row.name)).join('')}</select><span class="sub">仅可选择已启用且合同有效的采购供应商</span></label>
  <label>姓名<input name="name" maxlength="50" required></label>
  <label>电话<input name="phone" type="tel" inputmode="numeric" minlength="11" maxlength="11" pattern="1[3-9][0-9]{9}" title="请输入以1开头、第二位为3至9的11位手机号" placeholder="请输入11位手机号" required></label>
  ${entryPicker('entry-brand','brand_name','品牌',100)}
  ${entryPicker('entry-model','model','车型',150)}
  <label>城市<input name="city" list="entry-cities" maxlength="100" placeholder="请输入城市名称" required></label>
  <datalist id="entry-cities">${state.catalog.cities.map(row=>opt(row.name,row.name)).join('')}</datalist>
  <label>获客渠道（选填）<input name="acquisition_channel" maxlength="100" placeholder="例如：信息流投放"></label>
  <label>是否售卖<select name="is_sold" id="entry-is-sold" required>${opt('','请选择')}${opt('否','否')}${opt('是','是')}</select></label>
  <div class="span2 grid-form" id="entry-sale-fields" hidden>
   <label>售卖媒体<select name="sale_media_id" disabled>${opt('','请选择媒体')}${state.catalog.partners.filter(row=>row.role==='MEDIA').map(row=>opt(row.id,row.name)).join('')}</select></label>
   <label>项目<select name="sale_project" disabled>${opt('','请选择项目')}${opt('KA','KA')}${opt('MEMBER','会员')}</select></label>
   <label class="span2">售卖时间<input type="datetime-local" name="sold_at" disabled><span class="tiny muted">按北京时间填写</span></label>
  </div>
  <label>性别（选填）<select name="gender">${genderOptions()}</select></label>
  ${ycLeadForm()}
  <div class="form-actions span2"><button type="button" data-action="close-modal">取消</button>
   <button type="submit" class="primary">保存线索</button></div>
 </form>`);
 const brandInput=$('#entry-brand');
 const modelInput=$('#entry-model');
 const closeModels=mountEditablePicker($('#entry-model-picker'),()=>{
  const brand=brandInput.value.trim();
  return brand?['全系',...new Set(entryModels.filter(row=>row.brand_name===brand&&row.series_name!=='全系').map(row=>row.series_name))]:[];
 });
 let previousBrand='';
 mountEditablePicker($('#entry-brand-picker'),()=>brands,()=>{
  const brand=brandInput.value.trim();
  if(brand!==previousBrand){
   previousBrand=brand;modelInput.value='';closeModels();
  }
 });
}
function sourceDetails(records){
 if(!records?.length)return '';
 const fields=[['source_record_id','来源编号'],['demand_no','需求编号'],['channel_name','来源渠道名称'],
  ['series_name','车系名称'],['brand_name','品牌名称'],['city','城市'],['phone','手机号'],
  ['name','客户姓名'],['report_status','上报状态'],['callback_status','回调状态'],
  ['lead_status','线索状态'],['lead_code','Code 码'],['reported_at','上报时间'],['callback_at','回调时间']];
 const callbacks={SUCCESS:'成功',FAILED:'失败',NONE:'未回调'};
 const reports={SUCCESS:'上报成功',FAILED:'上报失败',PENDING:'未上报',UNKNOWN:'未提供'};
 return '<h3>来源方 API 原始记录</h3>'+records.map(record=>`<section class="card"><div class="card-body"><dl class="detail-grid">${
  fields.filter(([key])=>Object.hasOwn(record,key)).map(([key,label])=>{
   let value=record[key];
   if(key==='callback_status')value=callbacks[value];
   if(key==='report_status')value=reports[value];
   if(key==='reported_at'||key==='callback_at')value=fmt(value);
   return `<div><dt>${esc(label)}</dt><dd>${esc(value||'—')}</dd></div>`;
  }).join('')}</dl></div></section>`).join('');
}
async function leadDetail(id){const l=await api('leads/'+id);modal((l.lead_no?l.lead_no+' · ':'')+'线索详情',`<dl class="detail-grid">${[['customer','姓名'],['gender','性别'],['phone','电话'],['brand','品牌'],['model','车型'],['city','城市'],['supplier','线索供应商'],['acquisition_channel','获客渠道'],['is_sold','是否售卖'],['sold_media','售卖媒体'],['project','项目'],['sales_count','已匹配媒体'],['sold_at','售卖时间'],['collected_at','采集时间']].filter(([key])=>Object.hasOwn(l,key)).map(([key,label])=>`<div><dt>${label}</dt><dd>${leadCell(l,key)}</dd></div>`).join('')}</dl>
${sourceDetails(l.source_details)}
${Array.isArray(l.ingestions)?`<h3>来源采集记录</h3>${table(['获客渠道','来源记录编号','处理结果','时间'],l.ingestions.map(x=>[esc(channel(x.channel_id)),esc(x.source_record_id),badge(x.status),fmt(x.created_at)]))}`:''}<h3>接收方授权</h3>${table(['接收主体','核验状态','证据引用','是否撤回'],l.consents.map(x=>[esc(partner(x.recipient_id)),badge(x.review_status),esc(x.evidence_ref||'已关联测试凭据'),x.revoked_at?'已撤回':'有效期至 '+fmt(x.expires_at)]))}<h3>易车线索字段</h3>${ycSummary(l.yiche)}<p class="muted smalltext">${l.yiche_optional_present?.length?'已填写：'+l.yiche_optional_present.map(k=>esc(ycLeadNames[k])).join('、'):'未填写易车附加信息；请求 IP 缺省时使用 127.0.0.1。'}</p>${has('write')&&!['BLOCKED','DELETED'].includes(l.status)?`<button class="small" data-action="lead-yiche-edit" data-id="${id}">补充易车字段</button>`:''}${Array.isArray(l.deliveries)?'<h3>每一笔独立分发</h3>'+deliveryTable(l.deliveries):''}<div class="form-actions">${has('write')&&Object.hasOwn(l,'customer')&&l.name!=='已删除'?`<button data-action="lead-title" data-id="${id}" data-title="${esc(l.customer_title)}" data-version="${l.version}">设置性别</button>`:''}${has('reveal')&&Object.hasOwn(l,'customer')?`<button data-action="reveal-form" data-id="${id}">按用途查看明文</button>`:''}${has('consent')&&l.status==='QUARANTINED'?`<button data-action="review" data-id="${id}">核验授权</button>`:''}${has('privacy')&&!['DELETED','BLOCKED'].includes(l.status)?`<button class="danger" data-action="privacy-form" data-id="${id}">撤回或删除</button>`:''}</div>`,true)}
async function deliveryDetail(id){const d=await api('deliveries/'+id+'/timeline');modal('分发链路 · '+d.lead_no,`<div class="badges">${badge(d.send_status)}${badge(d.storage_status)}${badge(d.validity_status)}${badge(d.evidence_level)}</div>${yicheCodeLabel(d)}<dl class="detail-grid"><div><dt>媒体原始需求编号</dt><dd class="mono">${esc(d.demand_no||'—')}</dd></div><div><dt>最终媒体</dt><dd>${esc(partner(d.media_id))}</dd></div><div><dt>首次发送时间</dt><dd>${fmt(d.first_sent_at)}</dd></div><div><dt>结算状态</dt><dd>${badge(d.settlement_status)} ${has('finance')?money(d.price_fen):''}</dd></div></dl><h3>链路节点</h3><div class="timeline">${d.hops.map(h=>`<div class="timeline-item"><p>${h.from_id==='PLATFORM'?'车联汇':esc(partner(h.from_id))} → ${esc(partner(h.to_id))}　${badge(h.acceptance)}</p><small>${fmt(h.accepted_at)}　${esc(h.external_id||h.media_record_id||'等待接收凭据')}</small></div>`).join('')}</div>${has('engineering')?`<div class="callout">模拟操作用于验证业务状态，不会向真实媒体发送请求。乙方接收成功不等于最终媒体入库。</div><div class="actions"><button data-action="mock" data-id="${id}" data-result="STORED" ${!d.first_sent_at?'disabled':''}>模拟媒体入库</button><button data-action="mock" data-id="${id}" data-result="VALID" ${!d.first_sent_at?'disabled':''}>模拟有效</button><button data-action="mock" data-id="${id}" data-result="INVALID" ${!d.first_sent_at?'disabled':''}>模拟无效 / 更正</button>${d.send_status==='UNKNOWN'?`<button data-action="reconcile" data-id="${id}">查询模拟接收结果</button>`:''}</div>`:''}<h3 class="sub">回调事件</h3>${table(['事件','版本','处理结果','时间'],d.callbacks.map(x=>[esc(x.event_type),x.event_version,badge(x.status),fmt(x.received_at)]))}<h3 class="sub">网络发送尝试</h3>${table(['尝试次数','业务幂等编号','结果','时间'],d.attempts.map(x=>[x.attempt_no,esc(x.idempotency_key),badge(x.outcome),fmt(x.created_at)]))}<h3 class="sub">上报内容快照（个人信息已脱敏）</h3><pre class="code">${esc(JSON.stringify(d.outbound_payload,null,2))}</pre><h3 class="sub">配额变更账本</h3>${table(['原因','在途变化','有效变化','时间'],d.ledger.map(x=>[esc(x.reason),x.reserved_delta,x.valid_delta,fmt(x.created_at)]))}`,true)}
async function demandDetail(id){const d=await api('demands/'+id);if(d.external_project_id){const n=demandNumbers(d);modal('原表需求 '+d.demand_no,`<div class="callout">${d.quantity_basis==='DAILY'?'按北京时间每日计算配额':'按累计总量计算配额'} · ${d.target_total} 条。${badge(d.status)} ${urgency(d)}</div><p>交付：${esc(partner(d.receiver_id))}${d.receiver_id!==d.media_id?' → '+esc(partner(d.media_id)):''} · ${d.sale_project==='MEMBER'?'会员':'KA'}</p><p>今日有效 ${n.valid} / 在途 ${n.reserved} / 可用 ${n.remaining}</p>${table(['原表字段','值'],Object.entries(d.source_values||{}).map(([k,v])=>[esc(k),k==='城市&地区'||k==='城市'?regionCell(v):k==='车型'?modelCell(v):esc(v||'—')]))}<p class="smalltext muted">匹配方式由合作主体的会员/KA项目统一设置。</p>`,true);return}modal('需求 '+d.demand_no,`<dl class="detail-grid"><div><dt>媒体业务编号</dt><dd>${esc(d.demand_no||'—')}</dd></div><div><dt>状态与版本</dt><dd>${badge(d.status)} v${d.version}</dd></div><div><dt>最终媒体</dt><dd>${esc(partner(d.media_id))}</dd></div><div><dt>直接接收方</dt><dd>${esc(partner(d.receiver_id))}</dd></div><div><dt>车型 / 城市</dt><dd>${esc(series(d.series_id))} / ${JSON.parse(d.city_codes).map(city).map(esc).join('、')}</dd></div><div><dt>有效 / 在途 / 可用</dt><dd>${d.valid_count} / ${d.reserved_count} / ${d.target_total-d.valid_count-d.reserved_count}</dd></div></dl>${has('demand')?`<form id="demand-edit-form" data-id="${id}" data-version="${d.version}" class="grid-form"><label>需求总量<input type="number" name="target_total" min="1" value="${d.target_total}" required></label><label>每日限额<input type="number" name="daily_limit" min="1" value="${d.daily_limit}" required></label><label>项目<select name="sale_project">${opt('UNKNOWN','未设置',d.sale_project)}${opt('KA','KA',d.sale_project)}${opt('MEMBER','会员',d.sale_project)}</select></label><p>匹配方式由合作主体的会员/KA项目统一设置。</p><div class="span2">${urgency(d)}<p class="smalltext muted">目标达成率＝有效数量÷目标数量；在途占配额，暂不计有效。</p></div>${has('finance')?`<label>单价（元）<input type="number" name="price_yuan" min="0" step="0.01" value="${(d.unit_price_fen/100).toFixed(2)}" required></label>`:''}${d.media_id==='MEDIA_A'?ycDemandForm(d.yiche):''}<div class="form-actions span2"><button class="primary" type="submit">保存新版本</button>${!['CLOSED','COMPLETED','EXPIRED'].includes(d.status)?`<button class="danger" type="button" data-action="demand-close" data-id="${id}" data-version="${d.version}">关闭需求</button>`:''}</div></form>`:''}<div class="callout warning">本地测试允许有权限人员直接调整数量和价格并留审计；生产版仍需补齐变更审批。历史交付保持旧价格和旧需求快照。</div>`)}
function previewResults(p){
 const counts={};for(const r of p.items)if(r.eligible){const k=r.lead_id+'|'+r.media_id;counts[k]=(counts[k]||0)+1;}
 return table(['选择','线索编号','采购主体','最终媒体','需求编号','项目','提示'],p.items.flatMap((x,i)=>{
  if(!x.eligible)return [];
  const group=x.lead_id+'|'+x.media_id;
  return [[`<input type="checkbox" data-preview-pair="${i}" data-pair-group="${esc(group)}" ${counts[group]===1?'checked':''} aria-label="选择需求${esc(x.demand_no)}">`,esc(x.lead_no||'—'),esc(partner(x.buyer_id)),esc(partner(x.media_id)),esc(x.demand_no),x.project==='KA'?'KA':'会员',counts[group]>1?'<span class="badge gold">多项符合，请选择一项</span>':'可分发']];
 }),'暂无可分发结果，请返回调整线索或采购主体');
}
function updatePreviewSelection(){const b=document.querySelector('[data-action="confirm-dispatch"]');if(b){const n=document.querySelectorAll('[data-preview-pair]:checked').length;b.disabled=!n;b.textContent=`确认创建 ${n} 笔分发`}}
document.addEventListener('change',e=>{if(e.target.matches('[data-preview-pair]')){if(e.target.checked)document.querySelectorAll('[data-preview-pair]').forEach(x=>{if(x!==e.target&&x.dataset.pairGroup===e.target.dataset.pairGroup)x.checked=false});updatePreviewSelection()}});

async function showPreview(){
 if(selectedAutoProject())throw new Error('该主体项目已设置自动匹配，请由系统自动分发');
 if(state.selectedBuyers.size&&!state.selectedDemands.size)throw new Error('所选采购主体暂无对应合作项目的进行中需求');
 if(!state.selected.size||!state.selectedDemands.size)throw new Error('请至少选择一条线索和一个采购主体');
 const demands=new Map(state.activeDemands.map(d=>[d.id,d]));
 const projects=Object.fromEntries([...state.selectedDemands].map(id=>{
  const d=demands.get(id),buyer=state.catalog.partners.find(p=>p.id===d?.receiver_id);
  return [id,d?.sale_project&&d.sale_project!=='UNKNOWN'?d.sale_project:buyer?.cooperation_project];
 }));
 if(Object.values(projects).some(p=>!['KA','MEMBER'].includes(p)))throw new Error('采购主体或需求缺少合作项目，请先在线索供应或需求中设置会员或KA');
 const p=await api('matches/preview','POST',{lead_ids:[...state.selected],demand_ids:[...state.selectedDemands],projects});
 for(const row of p.items){
  const d=demands.get(row.demand_id);
  row.project=projects[row.demand_id];row.buyer_id=d?.buyer_id||d?.receiver_id;
 }
 p.projects=projects;state.preview=p;
 const results=p.items.filter(r=>r.eligible),matched=new Set(results.map(r=>r.lead_id)).size;
 modal('匹配预览',`<div class="callout" id="preview-summary">已选 ${state.selected.size} 条线索，其中 ${matched} 条有可分发结果，共 ${results.length} 项可分发结果。${results.length?'请核对后确认创建分发。':'请返回调整选择。'}确认时会再次校验规则及剩余额度。</div>
 <div id="preview-results">${previewResults(p)}</div><div class="form-actions"><button data-action="close-modal">返回修改</button><button class="primary" data-action="confirm-dispatch" ${!results.length?'disabled':''}>确认创建分发</button></div>`,true);updatePreviewSelection();
}
function newDemand(){
 const routes=state.catalog.routes.filter(r=>r.enabled),route=routes[0];
 if(!route)throw new Error('请先配置可用交付路由');
 modal('新建需求',`<form id="demand-form" class="grid-form">
 <label>媒体需求编号<input name="demand_no" required></label><label>合作项目<select name="sale_project" required>${opt('MEMBER','会员','MEMBER')}${opt('KA','KA')}</select></label>
 <label class="span2">交付路由<select name="route_id" data-yc-route required>${routes.map(r=>opt(r.id,partner(r.buyer_id)+' → '+partner(r.media_id),route.id)).join('')}</select></label>
 <label>车型<select name="series_id" required>${state.catalog.vehicles.map(v=>opt(v.series_id,v.series_name)).join('')}</select></label><label>城市<select name="city_code" required>${state.catalog.cities.map(v=>opt(v.code,v.name)).join('')}</select></label>
 <label>需求总量<input name="target_total" type="number" min="1" value="5" required></label><label>每日限额<input name="daily_limit" type="number" min="1" value="5" required></label>
 <label>结算单价（元）<input name="price_yuan" type="number" min="0" step="0.01" value="0" required></label><label>优先级<input name="priority" type="number" min="1" max="100" value="50" required></label>
 ${ycDemandForm()}<p class="span2 smalltext muted">匹配方式继承所选合作主体的会员/KA项目配置，草稿审核通过后参与分发。</p><div class="form-actions span2"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">保存草稿</button></div></form>`,true);
 const fields=$('#demand-form [data-yc-config]');fields.hidden=fields.disabled=route.media_id!=='MEDIA_A';
}
function importModal(p){state.importPreview=p;const fields=[['name','姓名'],['gender','性别'],['phone','电话'],['brand_name','品牌'],['model','车型'],['city','城市'],['acquisition_channel','获客渠道'],['is_sold','是否售卖'],['sale_media','售卖媒体'],['sale_project','项目'],['sold_at','售卖时间'],['source_record_id','来源记录编号'],['collected_at','采集时间'],...Object.entries(ycLeadNames)];modal('表格预检 · '+p.filename,`${p.format_notice?`<div class="callout warning">${esc(p.format_notice)}</div>`:''}<div class="stats">${stat('总行数',p.total,'待确认','▤')}${stat('可新增',p.valid,'保存后可参与分发','✓')}${stat('重复',p.duplicates,'追加来源不增加库存','↻')}${stat('错误',p.errors,'错误行不入库','!')}</div>${state.importFile?`<details><summary class="smalltext">表头不一致？调整字段映射</summary><form id="mapping-form" class="grid-form">${p.headers.map(h=>`<label>${esc(h)}<select name="${esc(h)}"><option value="${esc(h)}">自动识别 / 保留原列名</option>${fields.map(([v,n])=>opt(v,n)).join('')}</select></label>`).join('')}<div class="span2"><button type="submit">按映射重新预检</button></div></form></details>`:''}${table(['行号','来源记录编号','脱敏手机号','结果','原因'],p.rows.slice(0,100).map(x=>[x.row,esc(x.source_record_id),esc(x.phone),badge(x.status),esc(x.errors.join('；')||'—')]))}${p.total>100?'<p class="smalltext muted">仅展示前100行；提交按全部预检结果处理。</p>':''}<div class="form-actions"><a class="btn" href="/api/v1/imports/${p.id}/errors">下载错误行</a><button data-action="commit-import" data-mode="ALL_VALID" ${p.errors?'disabled':''}>全部无错后入库</button><button class="primary" data-action="commit-import" data-mode="VALID_ROWS">仅导入合格行</button></div>`,true)}
async function routeConfig(id){const r=state.catalog.routes.find(x=>x.id===id);modal('模拟路由配置',`<form id="route-form" data-id="${id}" class="grid-form"><label class="span2">接收行为<select name="mode">${opt('ACK','模拟接收成功',r.mode)}${opt('TIMEOUT','模拟网络超时 / 结果未知',r.mode)}</select></label><label>支持业务幂等<select name="supports_idempotency">${opt('1','支持',String(r.supports_idempotency))}${opt('0','不支持',String(r.supports_idempotency))}</select></label><label>支持结果查询<select name="supports_query">${opt('1','支持',String(r.supports_query))}${opt('0','不支持',String(r.supports_query))}</select></label><div class="callout span2">配置只影响后续创建的交付快照。超时且不支持幂等和查询时，会保留在途配额并禁止盲目重发。</div><div class="form-actions span2"><button class="primary" type="submit">保存模拟配置</button></div></form>`)}
function navigate(v){if(state.view===v&&$('#main')?.querySelector('h1'))return;leadFilters={};matchingFilters={};state.view=v;state.page=1;state.q='';state.status='';state.channel='';state.media='';history.replaceState(null,'','#'+v);$('#dialog').close();render()}
async function refresh(){state.catalog=await api('catalogs');await render()}
$('#app').addEventListener('change',async e=>{try{if(e.target.matches('[data-check-lead]')){const id=e.target.dataset.checkLead;if(e.target.checked)state.selected.add(id);else state.selected.delete(id);if($('#selected-note'))$('#selected-note').textContent='已选择 '+state.selected.size+' 条线索';document.querySelectorAll('[data-action=preview]').forEach(b=>b.disabled=!state.selected.size||!state.selectedDemands.size||selectedAutoProject())}if(e.target.matches('[data-check-buyer]')){state.selectedBuyers=new Set(e.target.checked?[e.target.dataset.checkBuyer]:[]);state.selected.clear();state.selectedDemands.clear();matchingFilters={};state.page=1;resolveBuyerSelection();await render()}if(e.target.matches('[data-check-demand]')){if(e.target.checked)state.selectedDemands.add(e.target.dataset.checkDemand);else state.selectedDemands.delete(e.target.dataset.checkDemand)}if(e.target.id==='page-size'){state.size=Number(e.target.value);state.page=1;await render()}}catch(e){toast(e.message,true)}});
document.addEventListener('click',async e=>{const b=e.target.closest('button,[data-nav]');if(!b||b.disabled)return;if(b.dataset.nav){navigate(b.dataset.nav);return}const action=b.dataset.action;if(!action)return;const id=b.dataset.id;try{b.disabled=true;
 if(action==='close-modal')$('#dialog').close();
 else if(action==='logout'){await api('logout','POST',{});state.user=null;state.selected.clear();state.selectedDemands.clear();state.selectedBuyers.clear();login()}
 else if(action==='refresh')await refresh();
 else if(action==='prev'){state.page--;await render()}
 else if(action==='next'){state.page++;await render()}
 else if(action==='matching-all-buyers'){state.selectedBuyers.clear();state.selectedDemands.clear();state.selected.clear();matchingFilters={};state.page=1;await render()}
 else if(action==='matching-select-all'){state.selected=new Set(state.matchingFilteredIds||[]);await render()}
 else if(action==='matching-clear-selection'){state.selected.clear();await render()}
 else if(action==='matching-reset'){matchingFilters={};state.page=1;await render()}
 else if(action==='clear-filters'){leadFilters={};state.q=state.status=state.channel=state.media='';state.page=1;await render()}
 else if(action==='new-lead')await newLead();
 else if(action==='show-regions'||action==='show-models'){const values=JSON.parse(b.dataset.regions);modal((action==='show-models'?'全部车型':'全部地区')+' · '+values.length+' 个',`<div class="region-list">${values.map(v=>`<span class="badge blue">${esc(v)}</span>`).join('')}</div>`,true);}
 else if(action==='source-demand-match'){state.selectedBuyers.clear();state.selectedDemands=new Set([id]);navigate('matching');}
 else if(action==='demand-summary-detail'){
 const g=demandSummaryRows[Number(id)];if(!g)throw new Error('请刷新汇总列表');
 modal('需求汇总',`<p>${esc(g.buyer_name)} → ${esc(g.media_name)} · ${g.project==='MEMBER'?'会员':g.project==='KA'?'KA':'待设置'}</p><p>共 ${g.total_count} 项需求，其中 ${g.active_count} 项在执行。</p>${table(['统计口径','执行中需求（项）','目标线索量（条）','有效（条）','在途（条）','可用（条）'],Object.entries(g.basis).map(([k,b])=>[k==='DAILY'?'每日需求（今日）':'项目总量（累计）',b.active_count,b.target_total,b.valid_count,b.reserved_count,b.available_count]),'暂无执行中需求')}${table(['需求状态','数量'],Object.entries(g.status_counts).map(([s,n])=>[esc(summaryStatus(s)),n]))}<p class="smalltext muted">进度按有效数量 ÷ 目标数量计算；星级沿用达成率规则，不参与分发排序。</p>`,true);
 }
 else if(action==='demand-sync-manage'){modal('来源与归属管理',table(['合作商','来源 / 链接','项目','同步频率','操作'],demandSyncSources.map(s=>[esc(partner(s.partner_id)),`${esc(s.name)}<span class="sub">${s.project_count} 条 · ${esc(s.source_kind==='API_KA'?'KA需求接口':s.source_kind==='EXCEL'?'Excel导入':s.doc_id)}</span>`,`${s.sale_project==='MEMBER'?'会员':'KA'}${s.available===false?'<span class="sub">主体或项目已停用，需求已隐藏</span>':''}`,s.source_kind==='API_KA'?'API · 每天08:00（北京时间）':s.source_kind==='EXCEL'?'Excel导入':s.enabled?s.interval_minutes+' 分钟':'已暂停',`<div class="sync-manage-actions"><button class="small" data-action="demand-sync-runs" data-id="${esc(s.id)}">同步记录</button>${has('demand')?`<button class="small" data-action="demand-sync-edit" data-id="${esc(s.id)}">修改归属 / 设置</button>${s.source_kind==='EXCEL'?'':`<button class="small primary" data-action="demand-sync-run" data-id="${esc(s.id)}" ${s.running?'disabled':''}>${s.source_kind==='API_KA'?'立即拉取':'立即同步'}</button>`}`:''}</div>`])),true);}
 else if(action==='demand-excel-add')await demandSyncForm(null,true);
 else if(action==='demand-excel-commit'){const result=await api('demand-excel/commit','POST',{id});$('#dialog').close();toast(`已导入 ${result.created} 条需求`);demandSyncView.page=1;await refresh();}
 else if(action==='demand-sync-add')modal('选择需求来源',`<div class="actions"><button data-action="demand-qq-add">腾讯文档链接</button><button data-action="demand-api-add">API拉取需求</button><button data-action="demand-excel-add">Excel导入</button></div>`);
 else if(action==='demand-qq-add')await demandSyncForm();
 else if(action==='demand-api-add')await demandSyncForm(null,false,true);
 else if(action==='demand-sync-edit')await demandSyncForm(id);
 else if(action==='demand-sync-run'){
 const syncLabel=b.textContent;const content=$('#dialog-body .modal-content');let feedback=$('#sync-manager-feedback');
 if(!feedback&&content){feedback=document.createElement('div');feedback.id='sync-manager-feedback';feedback.setAttribute('role','status');content.prepend(feedback);}
 if(feedback){feedback.className='callout';feedback.textContent='正在读取需求来源，请稍候…';}b.textContent='同步中…';
 try{const r=await api('demand-sync/sources/'+id+'/sync','POST',{});const message=r.status==='SUCCESS'?`${r.notice?r.notice+'。':''}同步完成：读取 ${r.fetched} 条，新增 ${r.added}、更新 ${r.updated}、未变 ${r.unchanged}、移除 ${r.removed}`:(r.error||'同步失败，请查看同步记录');if(feedback){feedback.className=r.status==='SUCCESS'?'callout':'callout error';feedback.textContent=message;}toast(message,r.status!=='SUCCESS');await render();}
 catch(error){if(feedback){feedback.className='callout error';feedback.textContent=error.message;}throw error;}
 finally{b.textContent=syncLabel;}
 }
 else if(action==='demand-sync-runs'){const runs=await api('demand-sync/sources/'+id+'/runs');modal('项目同步记录',table(['触发','结果','开始时间','获取','新增','更新','未变','移除','说明'],runs.map(r=>[r.trigger_kind==='scheduled'?'定时':'手动',`<span class="badge ${r.status==='SUCCESS'?'green':r.status==='FAILED'?'red':'gold'}">${({SUCCESS:'成功',FAILED:'失败',RUNNING:'同步中'})[r.status]}</span>`,fmt(r.started_at),r.fetched,r.added,r.updated,r.unchanged,r.removed,esc(r.error||'—')])),true);}
 else if(action==='demand-sync-detail'){const row=await api('demand-sync/projects/'+id);modal('原表项目 · '+(row.values['需求编号']||'未填写需求编号'),`<p class="smalltext muted">原表第 ${row.row_number} 行 · 最近读取 ${fmt(row.last_seen_at)}</p>${table(['原表字段','同步值'],Object.entries(row.values).map(([key,value])=>[esc(key),key==='城市&地区'||key==='城市'?regionCell(value):key==='车型'?modelCell(value):esc(value||'—')]))}<details><summary>原始单元格值</summary><pre class="code">${esc(JSON.stringify(row.raw_values,null,2))}</pre></details>${row.history?.length?`<details><summary>历史版本（${row.history.length}）</summary>${row.history.map(h=>`<h4>原表第 ${h.row_number} 行 · ${esc(h.reason)}</h4><p class="smalltext muted">存档时间：${fmt(h.created_at)}</p>${table(['原表字段','历史值'],Object.entries(h.values).map(([k,v])=>[esc(k),esc(v||'—')]))}`).join('')}</details>`:''}`,true);}
 else if(action==='demand-sync-prev'||action==='demand-sync-next'){demandSyncView.page+=action==='demand-sync-next'?1:-1;await render();}
 else if(action==='demand-sync-reset'){demandSyncView.filters={};demandSyncView.page=1;await render();}
 else if(action==='new-demand')newDemand();
 else if(['yiche-code-add','yiche-code-edit','yiche-code-delete'].includes(action)){
  yicheCodeForm(action.slice('yiche-code-'.length),id);
 }
 else if(action==='vehicle-config'){
  const library=(await api('media-vehicles')).find(x=>x.id===vehicleLibrary.id);
  if(!library)return;
  modal('车型库同步配置',`<form id="vehicle-config-form" class="grid-form" data-media="${esc(library.id)}">
   <label>易车 CID<input name="cid" value="${esc(library.cid||'728')}" inputmode="numeric" required></label>
   <label>签名密钥<input name="key" type="password" autocomplete="new-password" ${library.adapter?'placeholder="留空保留已保存密钥"':'required'}></label>
   <label><span>每天北京时间 00:00 同步</span><select name="enabled">${opt('yes','启用',library.enabled===0?'no':'yes')}${opt('no','暂停',library.enabled===0?'no':'yes')}</select></label>
   <div class="form-actions span2"><button class="primary" type="submit">保存配置</button></div>
  </form>`);
 }
 else if(action==='vehicle-sync'){
  await api('media-vehicles/'+encodeURIComponent(vehicleLibrary.id)+'/sync','POST',{});
  toast('已开始同步，点击「刷新状态」查看结果');await render();
 }
 else if(action==='vehicle-prev'||action==='vehicle-next'){
  vehicleLibrary.page+=action==='vehicle-next'?1:-1;await render();
 }
 else if(action==='lead-field-settings'){
  leadFieldSettings=await api('settings/lead-fields');
  showLeadFieldSettings(state.user.id);
 }
 else if(action==='lead-title'){
  modal('设置性别',`<form id="lead-title-form" class="grid-form" data-id="${esc(id)}" data-version="${esc(b.dataset.version)}">
   <label>性别<select name="gender">${genderOptions(b.dataset.title)}</select></label>
   <div class="form-actions span2"><button class="primary" type="submit">保存性别</button></div>
  </form>`);
 }
 else if(action==='lead-detail')await leadDetail(id);
 else if(action==='lead-yiche-edit'){
  const l=await api('leads/'+id);modal('补充易车线索字段',`<form id="lead-yiche-form" data-id="${id}" data-version="${l.version}" class="grid-form">${ycLeadForm(l.yiche,true)}<div class="form-actions span2"><button class="primary" type="submit">保存易车字段</button></div></form>`);
 }
 else if(action==='yiche-demo'){
  for(const [k,v] of Object.entries({platFormId:900001,plat:'Web',cityId:900002,serialId:900003,manufacturerId:900004,clueTypeId:1}))b.form.elements['yc_'+k].value=v;
  toast('已填入模拟编码，只用于本机验收');
 }
 else if(action==='delivery-project'){
  modal('补录售卖项目',`<form id="delivery-project-form" data-id="${esc(id)}" class="grid-form">
   <label class="span2">项目<select name="project" required>${opt('','请选择项目')}${opt('KA','KA')}${opt('MEMBER','会员')}</select></label>
   <div class="form-actions span2"><button class="primary" type="submit">保存项目</button></div>
  </form>`);
 }
 else if(action==='delivery-detail')await deliveryDetail(id);
 else if(action==='demand-detail')await demandDetail(id);
 else if(action.startsWith('demand-')){await api('demands/'+id+'/'+action.slice(7),'POST',{version:Number(b.dataset.version)});$('#dialog').close();toast('需求状态已更新');await refresh()}
 else if(action==='select-five'){const ls=await api('matches/leads?page_size=100');state.selected=new Set(ls.items.filter(x=>/^L00000[1-5]$/.test(x.lead_no)).map(x=>x.id));await render()}
 else if(action==='preview')await showPreview();
 else if(action==='rule-history'){const item=state.ruleAuditItems.find(x=>x.id===id),v=JSON.parse(item.detail);modal('分发规则变更记录',`<p>操作人：${esc(item.actor_id)} · ${fmt(item.created_at)}</p><p>原因：${esc(v.reason)}</p><p>规则版本：v${v.before.version} → v${v.after.version}</p>${table(['规则','修改前','修改后'],Object.entries(ruleNames).map(([k,n])=>[esc(n),v.before[k],v.after[k]]))}`);}
 else if(action==='auto-run'){const r=await api('distribution/auto/run','POST',{});toast(r.paused?'自动分发已暂停，请检查系统设置':`创建 ${r.created} 笔，模拟发送 ${r.sent} 笔，待人工 ${r.waiting} 条`);await refresh();}
 else if(action==='auto-bind-selected'){if(!state.selectedDemands.size||!state.selected.size)throw new Error('请选择线索和有进行中需求的采购主体');const p=await api('matches/preview','POST',{lead_ids:[...state.selected],demand_ids:[...state.selectedDemands],dispatch_mode:'AUTO'});const pairs=[];for(const lead_id of state.selected){const candidates=p.items.filter(r=>r.lead_id===lead_id&&r.eligible);if(candidates.length!==1)throw new Error('存在零项或多项可匹配需求，请在自动分发列表中明确指定目标需求');pairs.push({lead_id,demand_id:candidates[0].demand_id})}await api('distribution/auto/bind','POST',{pairs});toast('已保存自动分发绑定');await refresh();}
 else if(action==='auto-bind-one'){const demand_id=document.querySelector(`[data-auto-demand="${id}"]`).value;await api('distribution/auto/bind','POST',{pairs:[{lead_id:id,demand_id}]});const r=await api('distribution/auto/run','POST',{});toast(r.paused?'绑定已保存，自动分发处于关闭状态':`已创建 ${r.created} 笔自动分发`);await refresh();}
 else if(action==='confirm-dispatch'){
 const projects=state.preview?.projects||{};
 if(Object.values(projects).some(value=>!value))throw new Error('请为每项需求选择项目（KA或会员）');
 const pairs=[...document.querySelectorAll('[data-preview-pair]:checked')].map(el=>state.preview.items[Number(el.dataset.previewPair)]).map(({lead_id,demand_id})=>({lead_id,demand_id}));if(!pairs.length)throw new Error('请在预览中选择要分发的需求');const r=await api('dispatches','POST',{pairs,projects,preview_version:state.preview.preview_version},{'Idempotency-Key':crypto.randomUUID()});modal('分发提交结果',`<div class="callout">已创建 ${r.created} 笔，拦截 ${r.rejected} 笔。新建分发已持久化，等待执行发送任务。</div>${table(['线索','结果','原因'],r.items.map(x=>[esc(state.preview.items.find(i=>i.lead_id===x.lead_id)?.lead_no||x.lead_id),x.delivery_id?badge('QUEUED'):badge('REJECTED'),esc(reasons[x.reason]||x.reason||'已预占需求配额')]))}<div class="form-actions"><button data-nav="deliveries" class="primary">前往分发记录 →</button></div>`);}
 else if(action==='run-worker'){const r=await api('worker/run','POST',{});toast('已执行 '+r.count+' 笔模拟发送');await refresh()}
 else if(action==='mock'){await api('deliveries/'+id+'/mock-callback','POST',{action:b.dataset.result});toast('模拟回调已持久化并处理');await deliveryDetail(id);}
 else if(action==='reconcile'){await api('deliveries/'+id+'/reconcile','POST',{});toast('已查询模拟结果，没有重新发送');await deliveryDetail(id)}
 else if(action==='review'){await api('leads/'+id+'/review','POST',{});$('#dialog').close();toast('授权核验已记录；仍有车型等错误的线索继续隔离');await refresh()}
 else if(action==='reveal-form'){modal('按用途查看明文',`<form id="reveal-form" data-id="${id}"><label>查看用途<input name="purpose" required placeholder="如：核验本人授权证据"></label><div class="form-actions"><button class="primary">确认查看并记录审计</button></div></form>`)}
 else if(action==='privacy-form'){modal('撤回授权或删除线索',`<form id="privacy-form" data-id="${id}" class="grid-form"><label class="span2">处理类型<select name="type"><option value="REVOKE">撤回授权并停止分发</option><option value="DELETE">删除个人信息并停止分发</option></select></label><label class="span2">已核验身份及处理原因<textarea name="reason" required placeholder="请记录本人核验方式与处理依据"></textarea></label><div class="callout warning span2">将立即取消未发送任务；已经披露的接收主体会生成停止处理通知。删除会清除本地客户明文及发送载荷，保留必要的去标识化业务记录。</div><div class="form-actions span2"><button class="danger" type="submit">确认处理</button></div></form>`)}
 else if(action==='commit-import'){const r=await api('imports/'+state.importPreview.id+'/commit','POST',{mode:b.dataset.mode});$('#dialog').close();toast(`入库完成：新增 ${r.created}，重复 ${r.duplicates}，跳过 ${r.skipped}`);await refresh()}
 else if(action==='import-result'){const d=await api('imports?page_size=100');const im=d.items.find(x=>x.id===id);if(!im)throw new Error('请切换分页后查看');const p=JSON.parse(im.preview);if(im.result){const r=JSON.parse(im.result);modal('导入结果 · '+im.filename,`<div class="callout">新增 ${r.created} · 重复 ${r.duplicates} · 跳过 ${r.skipped}</div>${table(['行号','结果','原因'],r.items.map(x=>[x.row,badge(x.status),esc((x.errors||[]).join('；')||'—')]))}`,true)}else{state.importFile=null;importModal(p)}}
 else if(action==='route-config')await routeConfig(id);
 else if(action==='source-api'){
  modal('供应商 API 对接',`<dl class="detail-grid"><div><dt>平台渠道号</dt><dd>${esc(id)}</dd></div>
   <div><dt>接收接口</dt><dd>POST /open/v1/leads</dd></div></dl>
   <p>每次推送须带此渠道号和签名凭证。重新生成凭证后，旧凭证立即失效。</p>
   <div class="form-actions"><button data-action="issue-source-api" data-id="${esc(id)}">生成或重置凭证</button></div>`);
 }
 else if(action==='issue-source-api'){
  const key=await api('channels/'+encodeURIComponent(id)+'/api-key','POST',{});
  modal('保存供应商 API 凭证',`<p>请保存本次密钥，关闭后无法再次查看。</p>
   <dl class="detail-grid"><div><dt>渠道号 channel_id</dt><dd>${esc(key.channel_id)}</dd></div>
    <div><dt>凭证编号 X-Key-Id</dt><dd>${esc(key.key_id)}</dd></div></dl>
   <label>签名密钥<input readonly value="${esc(key.secret)}" aria-label="签名密钥"></label>`);
 }
 else if(action==='partner-edit')partnerEditForm(id);
 else if(action==='partner-project-add'){const p=state.catalog.partners.find(p=>p.id===id);const choices=['MEMBER','KA'].filter(project=>!(p.cooperation_projects||[]).some(v=>v.project===project));modal('添加合作项目 · '+p.name,`<form id="partner-project-add-form" data-id="${esc(id)}"><label>合作项目<select name="project" required>${choices.map(v=>opt(v,v==='MEMBER'?'会员':'KA')).join('')}</select></label><div class="form-actions"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">添加项目</button></div></form>`);}
 else if(action==='partner-project'){const p=state.catalog.partners.find(p=>p.id===id);modal('修改合作项目 · '+p.name,`<form id="partner-project-form" data-id="${esc(id)}"><label>合作项目<select name="cooperation_project" required>${opt('','请选择合作项目',p.cooperation_project||'')}${opt('MEMBER','会员',p.cooperation_project)}${opt('KA','KA',p.cooperation_project)}</select></label><div class="form-actions"><button class="primary" type="submit">保存</button></div></form>`);}
 else if(action==='new-config')configForm(b.dataset.kind);
 else if(action==='toggle-partner-project'){const r=await api('partner-projects/status','PATCH',{partner_id:id,project:b.dataset.project,version:Number(b.dataset.version),enabled:b.dataset.enabled==='0'?1:0});toast(r.enabled?(r.subject_enabled?'该项目已启用':'项目已启用，但主体总开关仍停用，请在编辑主体中恢复'):'该项目已停用，其他项目不受影响');await refresh();}
 else if(action==='toggle-partner'||action==='toggle-channel'){await api((action==='toggle-partner'?'partners/':'channels/')+id,'PATCH',{enabled:b.dataset.enabled==='0'});toast('状态已更新');await refresh()}
 else if(action==='process-inbox'){const r=await api('inbox/run','POST',{});toast('已处理 '+r.processed+' 条事件');await refresh()}
 else if(action==='maintenance'){await api('maintenance','POST',{});toast('超时及一致性检查已执行');await refresh()}
 else if(action==='integrity'){const r=await api('integrity');modal('当前数据库一致性检查',table(['检查项','结果','计数'],r.checks.map(x=>[esc(x.name),badge(x.ok?'APPROVED':'ERROR'),esc(x.actual?JSON.stringify(x.actual):'—')])))}
 else if(action==='backup'){const r=await api('backup','POST',{});toast('加密备份已创建：'+r.file)}
 else if(action==='export-request'){modal('申请导出脱敏线索',`<form id="export-form"><label>导出用途<input name="purpose" required placeholder="如：核对渠道交付质量"></label><div class="callout warning">仅导出脱敏明细。需另一位有审批权限的人员批准，审批后15分钟内单次下载。</div><div class="form-actions"><button class="primary">提交申请</button></div></form>`)}
 else if(action==='approve-export'){await api('exports/'+id+'/approve','POST',{});toast('已批准导出，需申请人下载');await refresh()}
 else if(action==='confirm-notice'){await api('privacy-notices/'+id,'POST',{});toast('已记录模拟接收方确认');await refresh()}
 }catch(err){if(action==='demand-excel-commit')demandExcelFeedback($('#dialog-body'),'导入失败：'+err.message,true);else toast(err.message,true)}finally{b.disabled=false}});
document.addEventListener('submit',async e=>{e.preventDefault();const f=e.target;const data=Object.fromEntries(new FormData(f)),b=f.querySelector('button[type=submit],button:not([type])');if(b)b.disabled=true;try{
 if(f.id==='login-form'){const r=await api('login','POST',{username:data.username,password:data.password});state.user=r;state.view='dashboard';state.page=1;state.selected.clear();state.selectedDemands.clear();state.selectedBuyers.clear();state.preview=null;leadFilters={};matchingFilters={};history.replaceState(null,'','#dashboard');await start();return}
 if(f.id==='yiche-code-form'){
  const mode=f.dataset.mode;
  const payload={previous:f.previousGroup};
  if(mode!=='delete')payload.group={codes:data.codes.split('/').map(code=>code.trim()),status:data.status,description:data.description};
  await api('yiche-codes',({add:'POST',edit:'PATCH',delete:'DELETE'})[mode],payload);
  $('#dialog').close();toast(mode==='delete'?'Code 码已删除':'Code 码已保存');await render();return;
 }
 if(f.id==='lead-fields-form'){
  const fields=e.submitter?.value==='reset'?null:new FormData(f).getAll('fields');
  await api('settings/lead-fields','PATCH',{account_id:data.account_id,fields});
  $('#dialog').close();toast('线索池字段权限已保存');await render();return;
 }
 if(f.id==='lead-title-form'){
  await api('leads/'+encodeURIComponent(f.dataset.id)+'/gender','POST',{gender:data.gender,version:Number(f.dataset.version)});
  $('#dialog').close();toast('性别已保存');await render();return;
 }
 if(f.id==='vehicle-search-form'){
  vehicleLibrary={id:data.media,q:data.q.trim(),page:1};await render();return;
 }
 if(f.id==='vehicle-config-form'){
  await api('media-vehicles/'+encodeURIComponent(f.dataset.media)+'/config','PATCH',{cid:data.cid,key:data.key,enabled:data.enabled==='yes'});
  $('#dialog').close();toast('车型库同步配置已保存');await render();return;
 }
 if(f.id==='partner-edit-form'){data.expected=JSON.parse(f.dataset.expected);data.enabled=Number(data.enabled);await api('partners/'+f.dataset.id,'PATCH',data);$('#dialog').close();toast('合作主体已更新');await refresh();return;}
 if(f.id==='partner-project-add-form'){await api('partner-projects','POST',{partner_id:f.dataset.id,project:data.project});$('#dialog').close();toast('项目已添加，默认为手动匹配');await refresh();return;}
 if(f.id==='partner-project-form'){await api('partners/'+f.dataset.id,'PATCH',{cooperation_project:data.cooperation_project});$('#dialog').close();toast('合作项目已保存');await refresh();return;}
 if(f.id==='synced-demand-control'){await api('demands/'+f.dataset.id,'PATCH',{version:Number(f.dataset.version),auto_enabled:Number(data.auto_enabled)});$('#dialog').close();toast('需求自动接收设置已保存');await render();return;}
 if(f.id==='demand-excel-form'){
  demandExcelFeedback(f,'正在读取文件并预检，请稍候…');
  if(b){b.dataset.originalText=b.textContent;b.textContent='预检中…';}
  const file=f.elements.file.files[0];
  if(!file||file.size>10*1024*1024)throw new Error('请选择不超过10MB的.xlsx文件');
  const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file)});
  const result=await api('demand-excel/preview','POST',{filename:file.name,content,config:{name:data.name,partner_id:data.partner_id,media_id:data.media_id,sale_project:data.sale_project,route_id:data.route_id,quantity_basis:data.quantity_basis}});
  modal('需求导入预检',`<div class="callout ${result.errors.length?'error':''}">共 ${result.total} 条，正确 ${result.valid} 条，错误 ${result.errors.length} 条。上传时间：${fmt(result.uploaded_at)}。请修正全部错误后再提交。</div>${result.errors.length?table(['Excel行号','需求编号','问题'],result.errors.map(row=>[row.row_number,esc(row.demand_no),esc(row.errors.join('；'))])):''}<h3>前20条需求预览</h3>${table(['需求编号','品牌','车型','城市','下单量','下单时间','开始时间','结束时间'],result.preview.map(row=>['需求编号','品牌','车型','城市&地区','下单量','下单日期','开始时间','结束时间'].map(key=>key==='车型'?modelCell(row.values[key]):key==='城市&地区'?regionCell(row.values[key]):esc(row.values[key]||'—'))))}<div class="form-actions"><button data-action="demand-excel-add">重新上传</button><button class="primary" data-action="demand-excel-commit" data-id="${esc(result.id)}" ${result.can_commit?'':'disabled'}>确认导入 ${result.valid} 条</button></div>`,true);return;
 }
 if(f.id==='demand-sync-form'){
  if(f.dataset.sourceKind==='API_KA'){
   const body={source_kind:'API_KA',name:data.name,partner_id:data.partner_id,media_id:data.media_id,sale_project:data.sale_project,route_id:data.route_id,quantity_basis:'DAILY',url:data.url,auth_mode:data.auth_mode,bearer_token:data.bearer_token};
   if(f.dataset.id)body.version=Number(f.dataset.version);
   const saved=await api('demand-sync/sources'+(f.dataset.id?'/'+f.dataset.id:''),f.dataset.id?'PATCH':'POST',body);
   const wasNew=!f.dataset.id;f.dataset.id=saved.id;f.dataset.version=saved.version;f.elements.bearer_token.value='';
   if(wasNew&&saved.auth_mode!=='UNCONFIRMED'){const result=await api('demand-sync/sources/'+saved.id+'/sync','POST',{});if(result.status!=='SUCCESS'){demandExcelFeedback(f,'来源已保存，拉取失败：'+result.error,true);return;}toast(`已拉取 ${result.fetched} 条KA需求`);}else toast(saved.auth_mode==='UNCONFIRMED'?'来源已保存，确认甲方鉴权后可手动拉取':'API来源设置已保存');
   $('#dialog').close();await refresh();return;
  }
  const body={name:data.name,partner_id:data.partner_id,media_id:data.media_id,sale_project:data.sale_project,route_id:data.route_id,quantity_basis:data.quantity_basis};
  if(f.dataset.sourceKind!=='EXCEL'){body.url=data.url;body.interval_minutes=Number(data.interval_minutes);body.enabled=Number(data.enabled);}
  if(f.dataset.id){body.version=Number(f.dataset.version);const saved=await api('demand-sync/sources/'+f.dataset.id,'PATCH',body);if(saved.url_changed)demandSyncView.page=1;toast(saved.url_changed?`新链接已生效，已读取 ${saved.project_count} 条需求`:'归属与设置已保存，相关需求已更新');}
  else{body.url=data.url;const source=await api('demand-sync/sources','POST',body);demandSyncView.source=source.id;demandSyncView.page=1;const r=await api('demand-sync/sources/'+source.id+'/sync','POST',{});toast(r.status==='SUCCESS'?`首次同步完成：${r.fetched} 个项目`:r.error,r.status!=='SUCCESS');}
  $('#dialog').close();await refresh();return;
 }
 if(f.id==='demand-sync-search'){demandSyncView.filters=Object.fromEntries(Object.entries(data).filter(([,v])=>v));demandSyncView.page=1;await render();return;}
 if(f.id==='matching-search-form'){
  matchingFilters=Object.fromEntries(Object.entries(data).filter(([,value])=>value));state.page=1;await render();return;
 }
 if(f.id==='lead-search-form'){
  if(data.from&&data.to&&data.from>data.to){toast('采集开始日期不能晚于结束日期',true);return}
  leadFilters=Object.fromEntries(Object.entries(data).filter(([,value])=>value));
  state.page=1;await render();return;
 }
 if(f.id==='search-form'){state.q=$('#search').value;state.status=$('#status-filter')?.value||'';state.channel=$('#channel-filter')?.value||'';state.media=$('#media-filter')?.value||'';state.page=1;await render();return}
 if(f.id==='delivery-project-form'){
  await api('deliveries/'+encodeURIComponent(f.dataset.id)+'/project','POST',{project:data.project});
  $('#dialog').close();toast('项目已补录');await refresh();return;
 }
 if(f.id==='lead-form'){
  data.yiche=takeYc(data);
  await api('leads','POST',data);toast('线索已保存，可参与分发');
 }
 else if(f.id==='lead-yiche-form'){await api('leads/'+f.dataset.id,'PATCH',{version:Number(f.dataset.version),yiche:takeYc(data)});toast('易车线索字段已保存')}
 else if(f.id==='demand-form'){data.yiche=takeYc(data);data.city_codes=[data.city_code];delete data.city_code;for(const k of ['target_total','daily_limit','priority'])data[k]=Number(data[k]);data.unit_price_fen=Math.round(Number(data.price_yuan)*100);delete data.price_yuan;await api('demands','POST',data);toast('草稿已保存，请提交后由另一账号审核')}
 else if(f.id==='demand-edit-form'){const body={target_total:Number(data.target_total),daily_limit:Number(data.daily_limit),sale_project:data.sale_project,version:Number(f.dataset.version)};if(f.querySelector('[data-yc-config]'))body.yiche=takeYc(data);if(data.price_yuan!==undefined)body.unit_price_fen=Math.round(Number(data.price_yuan)*100);await api('demands/'+f.dataset.id,'PATCH',body);toast('需求新版本已保存，历史快照保持不变')}
 else if(f.id==='settings-form'){await api('settings/distribution','PATCH',{max_media_count:Number(data.max_media_count),dedup_window_days:Number(data.dedup_window_days),ka_max_count:Number(data.ka_max_count),forbid_same_media:Number(data.forbid_same_media),auto_dispatch_enabled:Number(data.auto_dispatch_enabled),dispatch_enabled:Number(data.dispatch_enabled),reason:data.reason,version:state.catalog.settings.version});toast('分发策略已保存');await refresh();return}
 else if(f.id==='reveal-form'){const r=await api('leads/'+f.dataset.id+'/reveal','POST',data);modal('明文核验 · 已记录审计',`<dl class="detail-grid"><div><dt>客户姓名</dt><dd>${esc(r.name)}</dd></div><div><dt>手机号</dt><dd>${esc(r.phone)}</dd></div></dl><div class="callout">请核验后关闭窗口，勿复制到外部渠道。</div>`);return}
 else if(f.id==='privacy-form'){await api('privacy_requests','POST',{...data,lead_id:f.dataset.id});toast('已立即停止分发，并生成必要的接收方通知')}
 else if(f.id==='config-form'){await api(f.dataset.kind,'POST',data);toast('已创建并记录审计')}
 else if(f.id==='route-form'){await api('routes/'+f.dataset.id,'PATCH',{mode:data.mode,supports_query:Number(data.supports_query),supports_idempotency:Number(data.supports_idempotency)});toast('模拟配置已保存')}
 else if(f.id==='export-form'){await api('exports','POST',data);toast('已提交导出申请，等待另一人审批')}
 else if(f.id==='import-form'){const file=f.querySelector('[name=file]').files[0];if(file.size>10*1024*1024)throw new Error('文件不得超过10MB');const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file)});state.importFile={filename:file.name,content,supplier_id:data.supplier_id,sheet:data.sheet||undefined};const r=await api('imports','POST',state.importFile);importModal(r);return}
 else if(f.id==='mapping-form'){const r=await api('imports','POST',{...state.importFile,column_mapping:data});importModal(r);return}
 else return;
 $('#dialog').close();await refresh();
 }catch(err){if(f.id==='login-form'){$('#login-error').textContent=err.message}else if(f.id==='demand-excel-form'){demandExcelFeedback(f,'预检失败：'+err.message,true)}else if(['demand-sync-form','partner-edit-form','lead-form'].includes(f.id)){demandExcelFeedback(f,err.message,true)}else toast(err.message,true)}finally{if(b){b.disabled=false;if(b.dataset.originalText){b.textContent=b.dataset.originalText;delete b.dataset.originalText;}}}});
function login(){state.user=null;const demoMode=document.querySelector('meta[name=demo-mode]')?.content==='true';const publicTest=document.querySelector('meta[name=public-test]')?.content==='true';$('#app').innerHTML=`<div class="login"><section class="login-info"><div class="brand"><span class="brand-logo">⌁</span><div><strong>车联汇</strong><small>AUTO LEAD WORKSPACE</small></div></div><div><span class="test-label">${demoMode?'LOCAL TEST WORKSPACE':'LEAD WORKSPACE'}</span><h1>让线索流转有序，<br>让每次交付可见。</h1><p>从多渠道采集，到需求匹配与媒体交付。把每条线索的来源、去向与结果连接起来。</p></div></section><section class="login-main"><form class="login-form" id="login-form"><div class="eyebrow">WELCOME TO YOUR WORKSPACE</div><h2>${demoMode?'进入测试工作台':'登录工作台'}</h2><p class="muted smalltext">${demoMode?'使用虚构数据，体验完整业务流程。':'请使用管理员分配的账号和密码。'}</p><label>账号${demoMode?`<select name="username">${Object.entries(roles).map(([v,t])=>opt(v,t+'（'+v+'）')).join('')}</select>`:'<input name="username" required autocomplete="username">'}</label><label>密码<input type="password" name="password" value="${demoMode&&!publicTest?'Demo2026!':''}" required autocomplete="current-password"></label><div class="error-msg" id="login-error" role="alert"></div><button class="primary" type="submit">进入工作台 →</button><div class="hint">${demoMode&&!publicTest?'共用测试密码：Demo2026!':'请使用管理员分配的独立密码。'}<br>运营负责录入与分发，商务审核需求，合规核验授权及审批导出。管理员可调整分发策略。</div><p class="tiny muted" style="margin-top:24px">${demoMode?'本机演示环境':'汽车线索管理平台'}</p></form></section></div>`}
async function start(){try{state.user=await api('me');state.catalog=await api('catalogs');await render()}catch(e){if(e.status===401)login();else{$('#app').innerHTML=`<div class="loading">${esc(e.message)}<br><button data-action="refresh">重试</button></div>`}}}
window.addEventListener('hashchange',()=>{const v=location.hash.slice(1);if(state.user&&v!==state.view)navigate(v)});
const ycLeadNames={cityId:'易车城市 ID',serialId:'易车车系 ID',manufacturerId:'易车厂商 ID',carId:'易车车款 ID',requestIp:'请求 IP',recordPath:'录音地址',ptitle:'页面标题',thirdDeliveryUrl:'投流链接',longitude:'经度',latitude:'纬度',roomId:'直播间 ID',settleAccountNo:'直播账号',dvid:'设备 ID'};
const ycDemandNames={platFormId:'易车分配的平台 ID · 必填',plat:'客户端平台（如 iOS、Android、Web） · 必填',clueTypeId:'线索类型',cityId:'易车城市 ID · 必填',serialId:'易车主车系 ID · 必填',manufacturerId:'易车厂商 ID（factoryId） · 必填',brandId:'易车品牌 ID（内部核对）',carId:'易车车款 ID',csId2:'易车副车系 ID',dealerIds:'经销商 ID（逗号分隔）',crId:'易车需求 ID（类型 83 必填）'};
function ycSummary(v={}){return ['cityId','serialId','manufacturerId','carId'].some(k=>v[k])?`<span class="sub">${['cityId','serialId','manufacturerId','carId'].filter(k=>v[k]).map(k=>esc(ycLeadNames[k]+' '+v[k])).join(' · ')}</span>`:'<span class="sub">未填写易车编码</span>'}
function ycInput(k,n,v=''){return `<label>${esc(n)}<input name="yc_${k}" value="${esc(v)}" ${['cityId','serialId','manufacturerId','carId','platFormId','brandId','csId2'].includes(k)?'inputmode="numeric"':''}></label>`}
function ycLeadForm(v={},open=false){return `<details class="span2" ${open?'open':''}><summary>易车线索字段（选填）</summary><p class="smalltext muted">编码可随线索填入校验；未填时按易车需求映射上报。姓名为空使用“询价客户”，请求 IP 缺省使用 127.0.0.1。IP、设备 ID、链接和经纬度加密存储；补充时留空保留原值。</p><div class="grid-form">${Object.entries(ycLeadNames).map(([k,n])=>ycInput(k,n,v[k])).join('')}</div></details>`}
function ycDemandForm(v={},hidden=false){return `<fieldset data-yc-config class="span2 grid-form" ${hidden?'hidden disabled':''}><legend>易车上报配置</legend><p class="smalltext muted span2">请填易车分配的平台 ID 和官方字典编码。城市取 cityCode；车系、厂商分别取 seriesId、factoryId。回调地址由系统生成。草稿可先保存，提交前必须补齐。</p>${Object.entries(ycDemandNames).map(([k,n])=>k==='clueTypeId'?`<label>线索类型<select name="yc_clueTypeId">${[['1','1 · 询价'],['2','2 · 贷款'],['83','83 · 需填写需求 ID']].map(([val,title])=>opt(val,title,String(v[k]||1))).join('')}</select></label>`:ycInput(k,n,v[k])).join('')}<div class="span2"><button type="button" class="small" data-action="yiche-demo">填入模拟编码</button></div></fieldset>`}
function takeYc(data){const v={};for(const k of Object.keys(data))if(k.startsWith('yc_')){if(data[k]!=='')v[k.slice(3)]=data[k];delete data[k]}return v}
document.addEventListener('change',e=>{if(!e.target.matches('[data-yc-route]'))return;const el=e.target.form.querySelector('[data-yc-config]');el.hidden=el.disabled=state.catalog.routes.find(r=>r.id===e.target.value)?.media_id!=='MEDIA_A'});
document.addEventListener('change',async e=>{
 if(e.target.id==='entry-is-sold'){
  const enabled=e.target.value==='是';
  const section=$('#entry-sale-fields');
  section.hidden=!enabled;
  section.querySelectorAll('input,select').forEach(field=>{
   field.disabled=!enabled;field.required=enabled;
   if(!enabled)field.value='';
  });return;
 }
 if(e.target.id==='lead-fields-account'){showLeadFieldSettings(e.target.value);return;}
 if(e.target.id!=='vehicle-media')return;
 vehicleLibrary={id:e.target.value,q:'',page:1};await render();
});
await start();

function partnerEditForm(id){
 const p=state.catalog.partners.find(row=>row.id===id);
 const expected=Object.fromEntries(['name','cooperation_project','cooperation_price_fen','cooperation_note','enabled'].map(key=>[key,p[key]]));
 modal('编辑合作主体',`<form id="partner-edit-form" data-id="${esc(id)}" data-expected="${esc(JSON.stringify(expected))}" class="grid-form">
 <label>主体名称<input name="name" value="${esc(p.name)}" maxlength="100" required></label>
 <label>合作角色<input value="${p.role==='MEDIA'?'媒体':'乙方'}" readonly></label>
 <label>主体总开关<select name="enabled">${opt('1','启用整个主体',String(p.enabled))}${opt('0','停用整个主体（影响全部项目）',String(p.enabled))}</select></label>
 <label>默认合作项目<select name="cooperation_project" required>${(p.cooperation_projects||[]).map(row=>opt(row.project,row.project==='MEMBER'?'会员':'KA',p.cooperation_project)).join('')}</select></label>
 <label>合作价格（元）<input name="cooperation_price_yuan" type="number" min="0" step="0.01" value="${p.cooperation_price_fen==null?'':(p.cooperation_price_fen/100).toFixed(2)}"></label>
 <label class="span2">备注说明<textarea name="cooperation_note" maxlength="1000">${esc(p.cooperation_note||'')}</textarea></label>
 ${p.role==='AGENCY'?'<label class="span2">增加最终媒体（选填）<input name="final_media" placeholder="输入已有媒体名称或新媒体名称，已有交付关联保留"></label>':''}
 <p class="callout span2">主体总开关影响全部项目；各项目请在对应行独立启停。主体资料修改不会更换已有需求归属。链接绑定错误请到需求池的“修改归属 / 设置”中调整；新增项目请使用“添加项目”。</p>
 <div class="form-actions span2"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">保存修改</button></div></form>`,true);
}
function configForm(kind){
 let html='';
 const price=`<label>合作价格（元）<input name="cooperation_price_yuan" type="number" min="0" step="0.01"></label>`;
 const note=`<label class="span2">备注说明<textarea name="cooperation_note" maxlength="1000" rows="3"></textarea></label>`;
 if(kind==='partners')html=`<label>主体名称<input name="name" required></label>
  <label>合作角色<select name="role"><option value="MEDIA">媒体</option><option value="AGENCY">乙方</option></select></label><div id="agency-final-media-field" class="span2" hidden>${entryPicker('agency-final-media','final_media','最终媒体',100)}</div><label>合作项目<select name="cooperation_project" required><option value="">请选择合作项目</option><option value="MEMBER">会员</option><option value="KA">KA</option></select></label>${price}${note}`;
 if(kind==='channels')html=`<label>供应商名称<input name="name" required></label>
  <div class="lead-brand-picker" id="acquisition-picker">
   <label for="acquisition-input">获客方式</label>
   <div class="lead-brand-control">
    <input id="acquisition-input" name="type" role="combobox" aria-autocomplete="list"
     aria-expanded="false" aria-controls="acquisition-options" autocomplete="off"
     placeholder="输入新方式或选择已有方式" maxlength="50" required>
    <button type="button" aria-label="展开获客方式" tabindex="-1">▾</button>
   </div>
   <div id="acquisition-options" role="listbox" aria-label="获客方式选项" hidden></div>
  </div>${price}${note}`;
 if(kind==='routes')html=`<label>直接接收方<select name="receiver_id">${state.catalog.partners.filter(p=>['MEDIA','AGENCY'].includes(p.role)).map(p=>opt(p.id,p.name)).join('')}</select></label><label>最终媒体<select name="media_id">${state.catalog.partners.filter(p=>p.role==='MEDIA').map(p=>opt(p.id,p.name)).join('')}</select></label><div class="callout span2">直连时两者一致；转发时接收方选择乙方。新路由仅连接本地模拟器。</div>`;
 modal('新增'+({partners:'合作主体',channels:'供应商',routes:'交付路由'}[kind]),`<form id="config-form" data-kind="${kind}" class="grid-form">${html}<div class="form-actions span2"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">保存</button></div></form>`)
 if(kind==='channels')mountAcquisitionPicker();
 if(kind==='partners'){const field=$('#agency-final-media-field input');field.required=false;field.disabled=true;mountEditablePicker($('#agency-final-media-picker'),()=>[...new Set(['自有媒体',...state.catalog.partners.filter(p=>p.role==='MEDIA'&&p.enabled).map(p=>p.name)])]);}
}

function mountAcquisitionPicker(){
 mountEditablePicker($('#acquisition-picker'),()=>acquisitionMethods().map(acquisitionLabel));
}
function mountEditablePicker(picker,options,changed=()=>{}){
 const input=picker.querySelector('[role="combobox"]');
 const list=picker.querySelector('[role="listbox"]');
 const toggle=picker.querySelector('.lead-brand-control button');
 let active=-1;

 function close(){
  list.hidden=true;
  input.setAttribute('aria-expanded','false');
  input.removeAttribute('aria-activedescendant');
 }
 function open(query=''){
  const search=query.trim().toLowerCase();
  const matches=options().filter(option=>option.toLowerCase().includes(search));
  if(!matches.length){close();return;}
  active=-1;
  list.innerHTML=matches.map((option,index)=>`<button type="button" role="option" tabindex="-1"
   id="${list.id}-option-${index}" aria-selected="false">${esc(option)}</button>`).join('');
  list.hidden=false;
  input.setAttribute('aria-expanded','true');
  input.removeAttribute('aria-activedescendant');
 }
 function choose(option){
  input.value=option.textContent;
  close();
  changed();
 }

 toggle.addEventListener('mousedown',event=>event.preventDefault());
 toggle.addEventListener('click',()=>{
  if(!list.hidden){close();return;}
  input.focus();
  open();
 });
 input.addEventListener('click',()=>{if(list.hidden)open(input.value);});
 input.addEventListener('input',()=>{changed();open(input.value);});
 input.addEventListener('change',changed);
 picker.addEventListener('focusout',event=>{if(!picker.contains(event.relatedTarget))close();});
 list.addEventListener('mousedown',event=>event.preventDefault());
 list.addEventListener('click',event=>{
  const option=event.target.closest('[role="option"]');
  if(option)choose(option);
 });
 input.addEventListener('keydown',event=>{
  if(event.isComposing||event.keyCode===229)return;
  if(event.key==='Escape'&&!list.hidden){
   event.preventDefault();
   close();
  }else if(event.key==='ArrowDown'||event.key==='ArrowUp'){
   if(list.hidden)open(input.value);
   if(list.hidden)return;
   event.preventDefault();
   const items=[...list.children];
   const step=event.key==='ArrowDown'?1:-1;
   active=active<0?(step===1?0:items.length-1):(active+step+items.length)%items.length;
   items.forEach((item,index)=>item.setAttribute('aria-selected',String(index===active)));
   input.setAttribute('aria-activedescendant',items[active].id);
   items[active].scrollIntoView({block:'nearest'});
  }else if(event.key==='Enter'&&!list.hidden&&active>=0){
   event.preventDefault();
   choose(list.children[active]);
  }
 });
 return close;
}

document.addEventListener('change',async e=>{if(e.target.id==='demand-sync-page-size'){demandSyncView.size=Number(e.target.value);demandSyncView.page=1;await render();}});

document.addEventListener('input',e=>{if(e.target.id==='matching-demand-search'){const q=e.target.value.trim().toLocaleLowerCase();document.querySelectorAll('[data-demand-search]').forEach(row=>row.hidden=!row.dataset.demandSearch.toLocaleLowerCase().includes(q));}});


function mountDemandFilterPickers(){
 const form=$('#demand-sync-search');if(!form)return;
 for(const key of ['brand','model','city'])mountEditablePicker($('#demand-filter-'+key+'-picker'),()=>{
  let options=demandSyncView.options?.[key]||[];
  if(key==='model'&&form.elements.brand.value.trim())options=options.filter(o=>o.brands.some(b=>b.toLowerCase().includes(form.elements.brand.value.trim().toLowerCase())));
  return options.map(o=>o.value);
 },()=>{if(key==='brand')form.elements.model.value='';});
}

document.addEventListener('change',async e=>{
 if(!e.target.matches('[data-project-mode]'))return;
 const field=e.target;field.disabled=true;
 try{await api('partner-projects/mode','PATCH',{partner_id:field.dataset.partner,project:field.dataset.project,matching_mode:field.value,version:Number(field.dataset.version)});toast(field.value==='AUTO'?'已设为自动匹配，系统每30秒扫描该项目需求':'已设为手动匹配，该项目不再自动创建分发');await refresh()}catch(error){toast(error.message,true);await refresh()}
});

document.addEventListener('change',e=>{
 if(!e.target.matches('#config-form[data-kind="partners"] [name="role"]'))return;
 const agency=e.target.value==='AGENCY',section=$('#agency-final-media-field'),input=section.querySelector('input');section.hidden=!agency;input.disabled=!agency;input.required=agency;if(!agency)input.value='';
});
