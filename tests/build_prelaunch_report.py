from pathlib import Path
import json,ast,datetime,collections,html
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/prelaunch-2026-11-13'
backend=json.loads((OUT/'backend-cases.json').read_text());flow=json.loads((OUT/'flow-cases.json').read_text());sched=json.loads((OUT/'scheduler-cases.json').read_text());state=json.loads((OUT/'current-state-readonly.json').read_text());manifest=json.loads((OUT/'manifest.json').read_text());stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
assert backend['passed']==backend['total'] and flow['passed'] and sched['passed']
module_names={'test_acceptance':'核心业务与事务验收','test_agency_final_media':'乙方最终媒体与项目开通','test_demand_project_filters':'需求组合筛选','test_demand_summary':'需求汇总及每日/累计口径','test_demand_sync':'腾讯文档同步逻辑','test_distribution':'分发规则与自动分配','test_excel_intake':'Excel/CSV导入','test_lead_entry':'线索录入与车型输入','test_lead_expiry':'有效期与展示一致性','test_matching_pool':'候选线索池及字段筛选','test_media_vehicles':'媒体车型库','test_partner_project_modes':'会员/KA与匹配方式','test_security':'权限、安全及继承业务验收','test_source_api':'供应商API及来源记录','test_synced_demands':'原表需求匹配及配额','test_yiche':'易车协议与回调逻辑','test_yiche_push':'易车适配器离线模拟'}
notes={};ac_names=[]
for file in (ROOT/'tests').glob('test_*.py'):
 tree=ast.parse(file.read_text())
 if file.stem=='test_acceptance':
  for node in tree.body:
   if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='NAMES' for t in node.targets):ac_names=ast.literal_eval(node.value)
 for node in ast.walk(tree):
  if isinstance(node,ast.FunctionDef) and node.name.startswith('test_'):
   desc=ast.get_docstring(node)
   for sub in ast.walk(node):
    if isinstance(sub,ast.Call) and isinstance(sub.func,ast.Attribute) and sub.func.attr=='note' and sub.args and isinstance(sub.args[0],ast.Constant) and isinstance(sub.args[0].value,str):desc=sub.args[0].value;break
   notes[(file.stem,node.name)]=desc or node.name.removeprefix('test_').replace('_',' ')
unique={}
for case in backend['cases']:
 key=case['id'];item=unique.setdefault(key,{**case,'executions':0});item['executions']+=1
 if case['status']!='PASS':item['status']=case['status']
 for n,title in enumerate(ac_names,1):
  if case['name']==f'test_AC{n:02}':item['label']=title
 item.setdefault('label',notes.get((case['module'],case['name']),case['name']))
expected={
'WEB01':'登录成功后进入工作台，清除旧账号选择状态','WEB02':'14个模块均可读取并渲染，无页面错误','CFG01':'只保存所选会员项目，不隐式开通KA','CFG02':'主动添加KA后两项目分别存在','CFG03':'乙方未填最终媒体不能保存，选择已有媒体后关联正确','CFG04':'新供应商与自定义获客方式可持久化','DMD01':'新增需求入口仍为链接表单，链接/主体/媒体/项目必填','DMD02':'同步一项会员需求，目标2条且正确汇总','DMD03':'重复同步按原编号更新，不重复新增','DMD04':'KA和乙方需求分别属于正确项目与接收方','LEAD01':'非法手机号被拒绝，已售卖时补齐媒体/项目/时间','LEAD02':'手工填写必填字段后形成库存','MAN01':'可见线索匹配正确需求，只创建一笔待发送任务','SEND01':'执行任务后变为接收成功，详情可查看链路','CB01':'仅入库不计有效，保持一条在途占用','CB02':'有效反馈后在途转有效，进度和可用量正确','IMP01':'Excel正确行与错误行分开，只导入正确行','IMP02':'CSV正确、重复、错误分别统计，确认正确行入库','API01':'签名有效才入库，同nonce重放被拒绝','AUTO01':'会员切自动不影响KA手动配置','AUTO02':'唯一候选自动创建并发送，目标2条不得超发','AUTO03':'无效释放额度后可补发，切手动后模式保存','RELAY01':'直接接收乙方与最终媒体独立且形成多跳链路','FILTER01':'品牌联动车型，线索池已无旧批量匹配入口','PERM01':'运营仅见授权字段，越权改规则被403拒绝','EXPORT01':'另一角色审批，导出脱敏且单次下载','CODE01':'同原因多Code可新增、修改、删除','GOV01':'撤回取消未发送任务并保留处理记录','DB01':'配额、交付、账本和关联检查通过','BACKUP01':'生成加密备份文件'}
issues=[
 {'id':'QA-01','severity':'P2','status':'已修复并复测','title':'切换账号后停留在无权访问的页面','evidence':'完整流程GOV01首次复现：从匹配分发切到合规账号后显示FORBIDDEN。修复登录成功后统一进入工作台、清空上次选择及预览；最终30项页面流程全通过。','action':'已完成；原失败记录保存在flow-cases-before-login-fix.json。'},
 {'id':'REL-01','severity':'P1','status':'待处理／阻塞正式上线','title':'当前真实腾讯文档来源仍显示同步失败','evidence':'本机现有来源只读状态：'+('；'.join(s['last_error'] for s in state['source_status'] if s['last_error']) or '未观察到错误')+'。本轮未重新访问外部腾讯文档，缓存模拟同步通过不能证明真实来源已恢复。','action':'修复真实同步错误后，另行确认定时更新、字段一致、结束/移除项目处理及最近成功时间。'},
 {'id':'REL-02','severity':'P1','status':'未验收／阻塞正式上线','title':'真实媒体发送、入库及回调尚未验收','evidence':'按既有要求未联调易车，未调用真实车型API；本轮媒体反馈与文档响应均使用本地模拟或缓存。','action':'取得真实联调授权与条件后，单独验收媒体端接收、实际入库、回调可达性、签名、业务编号和有效性结果。'},
 {'id':'REL-03','severity':'P1','status':'发布前配置未完成','title':'仍为本地测试部署与身份配置','evidence':'登录页仍展示测试账号共用密码；服务为本机HTTP，保留模拟接口和测试凭证逻辑，密钥为本机测试Vault。基础越权/签名测试通过，不等于正式安全部署验收。','action':'生产部署前更换账号凭证、移除测试入口/凭证，完成生产密钥、域名与HTTPS配置及对应验收；本轮未执行正式渗透或依赖漏洞审计。'},
 {'id':'REL-04','severity':'P2','status':'未完成','title':'真实浏览器排版与兼容性验收未完成','evidence':'尝试打开隔离浏览器时连接超时；未取得浏览器截图。已通过的页面检查是jsdom DOM事件与本地HTTP集成。','action':'补做桌面浏览器及目标分辨率下的布局、滚动、弹窗、下拉、键盘与兼容性检查。'},
 {'id':'REL-05','severity':'P2','status':'待维护','title':'部分历史验收脚本和向导仍引用旧流程','evidence':'tests/test_ui.cjs仍引用已删除的select-five；test_distribution_ui.cjs仍引用已删除的逐需求项目选择框。验收向导也保留旧版“示例前5条”文案。它们未计入本轮通过数量。','action':'按当前交互更新或归档旧脚本与文案，后续以本轮可复现的新全流程脚本为基线。'}]
for item in flow['cases']:item['expected']=expected[item['id']]
all_items=[{'layer':'页面全流程',**c} for c in flow['cases']]+[{'layer':'后台定时任务',**c} for c in sched['cases']]+[{'layer':'后台回归',**c} for c in unique.values()]
(OUT/'all-test-items.json').write_text(json.dumps(all_items,ensure_ascii=False,indent=2)+'\n');(OUT/'findings.json').write_text(json.dumps(issues,ensure_ascii=False,indent=2)+'\n')
md=lambda x:str(x).replace('|','／').replace('\n',' ')
modules=collections.Counter(c['module'] for c in backend['cases']);unique_modules=collections.Counter(c['module'] for c in unique.values())
lines=[f'# 上线前测试报告\n\n报告时间：{stamp}（北京时间）\n\n对象：本机汽车销售线索平台，数据库结构版本{state["schema_version"]}。\n',
'## 一、结论\n\n**本地模拟业务流程验收通过；当前不建议直接正式生产上线。**\n\n正式上线仍需解决真实需求同步错误、完成真实媒体端到端验收及生产配置，并补做真实浏览器与容量验收。不能把本轮模拟通过解释为“已经能向真实易车稳定入库并回调”。\n',
'## 二、执行结果\n\n| 测试层 | 执行结果 | 说明 |\n|---|---:|---|',
f'| 后台全量回归 | {backend["passed"]}／{backend["total"]}通过 | {len(unique)}个唯一测试标识；包含继承、复用执行，不等于211个独立业务场景 |',
f'| 页面全流程 | {sum(c["status"]=="PASS" for c in flow["cases"])}／{len(flow["cases"])}通过 | DOM事件＋真实隔离HTTP服务，覆盖14个模块 |',
f'| 后台真实定时线程 | {len(sched["cases"])}／{len(sched["cases"])}通过 | 实际启动应用调度线程；外部响应使用虚构样本 |',
'| 浏览器截图、排版与兼容 | 未完成 | 浏览器连接超时，未伪记通过 |',
'| 真实腾讯文档／媒体联调 | 未执行 | 遵守不联调易车的约束；真实来源状态仅只读核查 |\n',
'后台最终失败0、错误0、跳过0；页面最终未捕获错误0。首次流程中发现1项账号切换产品问题，修复后从新隔离库完整复测。另修正了测试脚本等待新表单的时序与治理API路径，未将测试脚本错误算作产品故障。\n',
'## 三、已跑通的业务链路\n\n登录 → 14模块读取 → 新增媒体仅开通会员 → 主动添加KA → 新增乙方及最终媒体 → 新增供应商 → 链接需求同步及重复同步 → 手工录入 → 手动匹配预览 → 创建任务 → 执行发送 → 入库反馈 → 有效反馈 → Excel/CSV导入 → 签名API接入 → 自动匹配与补发 → 乙方转发 → 字段权限 → 脱敏导出审批 → Code维护 → 授权撤回 → 账本一致性 → 加密备份。\n',
'## 四、页面全流程测试项\n\n| 编号 | 模块 | 测试项／预期 | 实际结果 | 状态 |\n|---|---|---|---|---|']
for c in flow['cases']:lines.append('| '+' | '.join(md(v) for v in (c['id'],c['module'],expected[c['id']],c['evidence'],'通过' if c['status']=='PASS' else c['status']))+' |')
lines+=['\n## 五、定时任务测试项\n\n| 编号 | 测试项 | 实际结果 |\n|---|---|---|']
for c in sched['cases']:lines.append(f'| {c["id"]} | {c["name"]} | 通过'+(f'，启动后约{c["observed_seconds"]}秒观察到结果' if 'observed_seconds' in c else '')+' |')
lines+=['\n这两项使用应用真实server.run线程，不是手工点击“立即扫描”。需求同步的文档内容是虚构响应，验证的是调度和入库逻辑，不是腾讯线上接口。\n','## 六、后台回归覆盖\n\n| 范围 | 执行次数 | 唯一测试标识 | 结果 |\n|---|---:|---:|---|']
for mod,n in modules.items():lines.append(f'| {module_names.get(mod,mod)} | {n} | {unique_modules[mod]} | 全部通过 |')
lines+=['\n关键异常与边界已覆盖：剩余1条额度下20并发最多成功1条、重复请求幂等、回调重放与乱序、更正冲正、未知结果保持额度、跨组织访问、CSRF、字段权限、导出审批及单次下载、删除后恢复时重放删除清单、重启后outbox恢复、项目独立模式、同媒体去重、KA累计上限、每日配额与累计总量口径。完整标识见《测试项明细》。\n','## 七、发现的问题、未验收项和上线阻塞\n\n| 编号 | 等级 | 状态 | 事项 | 证据与处理 |\n|---|---|---|---|---|']
for i in issues:lines.append('| '+' | '.join(md(v) for v in (i['id'],i['severity'],i['status'],i['title'],i['evidence']+' '+i['action']))+' |')
lines+=['\nP1表示正式上线准入条件未满足；并非上述事项都属于本轮新发现的代码缺陷。QA-01是本轮复现并修复的产品缺陷，修复内容仅涉及登录后的页面和选择状态。\n','## 八、范围与限制\n\n- 使用独立临时数据库；页面服务8876、定时线程服务8877，未向现有8765业务库执行创建、删除、分发或迁移。现有库只读取脱敏配置与状态元数据。\n- 后台测试有外部网络拦截，页面请求限定隔离服务；未访问易车、车型API或真实腾讯文档。后台记录到的外部连接尝试为0。\n- 本轮没有执行高并发容量压测、24小时以上稳定性、网络故障注入实网演练、多实例部署、生产备份恢复、正式渗透与依赖漏洞扫描。单个配额20并发测试不等于容量压测。\n- 真实浏览器连接失败，DOM检查不替代渲染、像素排版、跨浏览器、移动端和辅助技术验收。\n- 测试使用虚构姓名、号码、需求和媒体；不在报告输出真实手机号、姓名、供应商密钥或本地Vault密钥。\n','## 九、上线前待完成清单\n\n1. 修复并重新验收真实需求同步，确认最近成功时间持续更新。\n2. 在另行获得授权后完成真实媒体发送、入库和回调验收；当前继续禁止自动调用真实易车。\n3. 完成生产账号、凭证、密钥与HTTPS部署，移除可公开使用的测试入口。\n4. 完成真实浏览器人工/自动验收及目标容量、长稳、生产恢复演练。\n5. 更新旧版脚本和验收向导文案，保留本轮报告作为后续回归基线。\n','## 十、证据与复跑\n\n- 后台逐项结果：backend-cases.json；原始日志：backend-run.log。\n- 页面逐项结果：flow-cases.json；原始日志：flow-run.log；首次产品问题：flow-cases-before-login-fix.json。\n- 定时器结果：scheduler-cases.json；定时服务日志：scheduler-server.log。\n- 当前来源只读状态：current-state-readonly.json；代码摘要：manifest.json。\n- 测试脚本：tests/run_prelaunch_backend.py、tests/prelaunch_fixture.py、tests/test_prelaunch_flow.cjs、tests/run_prelaunch_scheduler.py。只应按隔离模式复跑，不能把页面脚本的测试地址指向正式业务库。\n']
report='\n'.join(lines)+'\n';(OUT/'上线前测试报告_2026-11-13.md').write_text(report)
details=['# 上线前测试项明细\n',f'生成时间：{stamp}（北京时间）。后台{backend["total"]}次执行，去重后{len(unique)}个标识；页面30项，后台定时器3项。\n','## 页面全流程与定时器\n\n| 编号 | 测试项 | 预期／实际证据 | 状态 |\n|---|---|---|---|']
for c in flow['cases']+sched['cases']:details.append('| '+' | '.join(md(v) for v in (c['id'],c['name'],c.get('evidence',str(c.get('observed_seconds','账本一致性通过'))),c['status']))+' |')
details+=['\n## 后台逐项标识（含复用次数）\n\n测试项名称优先使用原用例的中文业务标签；其余保留英文方法名，供技术核对，具体断言在对应测试文件。\n\n| 模块 | 测试项 | 自动化标识 | 执行次数 | 状态 |\n|---|---|---|---:|---|']
for c in unique.values():details.append('| '+' | '.join(md(v) for v in (module_names.get(c['module'],c['module']),c['label'],c['id'],c['executions'],c['status']))+' |')
(OUT/'测试项明细_2026-11-13.md').write_text('\n'.join(details)+'\n')
esc=html.escape
rows=[]
for c in flow['cases']+sched['cases']:rows.append({'layer':'页面全流程' if c in flow['cases'] else '真实后台定时器','id':c['id'],'name':c['name'],'status':c['status'],'detail':c.get('evidence',str(c.get('observed_seconds','一致性通过')))})
for c in unique.values():rows.append({'layer':module_names.get(c['module'],c['module']),'id':c['id'],'name':c['label'],'status':c['status'],'detail':f"执行{c['executions']}次；{c['seconds']}秒（最近执行）"})
header=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>上线前测试报告</title><style>body{{font:15px/1.7 system-ui,sans-serif;max-width:1200px;margin:30px auto;padding:0 24px;color:#20372d;background:#f7faf8}}h1{{font-size:28px}}section{{background:white;padding:22px;margin:18px 0;border:1px solid #d7e2dc;border-radius:12px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #e0e8e3;text-align:left;vertical-align:top}}.bad{{color:#9a3e20}}.ok{{color:#277243}}input{{padding:12px;width:95%;border:1px solid #b9cdc0;border-radius:6px}}small{{color:#64796d}}.scroll{{overflow:auto}}@media print{{input{{display:none}}section{{break-inside:auto}}}}</style><h1>汽车销售线索平台 · 上线前测试报告</h1><small>{stamp} 北京时间 · 结构版本{state['schema_version']}</small><section><h2>结论</h2><p><b>本地模拟业务验收通过；当前不建议直接正式生产上线。</b></p><p>后台{backend['passed']}/{backend['total']}次执行通过（{len(unique)}个唯一标识，含继承复用）；页面30/30项通过；真实后台定时器3/3项通过。</p><p class="bad">正式需求同步仍报错；真实媒体入库回调、生产配置及真实浏览器验收尚未完成。模拟通过不等于正式媒体已接通。</p><p>发现并修复：切换角色后落到无权限页面。完整页面流程已从新隔离库复测通过。</p></section><section><h2>问题及上线准入事项</h2><div class="scroll"><table><tr><th>编号</th><th>等级/状态</th><th>事项及证据</th><th>处理</th></tr>'''
header+=''.join(f"<tr><td>{i['id']}</td><td>{i['severity']}<br>{esc(i['status'])}</td><td><b>{esc(i['title'])}</b><br>{esc(i['evidence'])}</td><td>{esc(i['action'])}</td></tr>" for i in issues)
header+='''</table></div></section><section><h2>全部测试项</h2><p>可按模块、编号或名称搜索。后台按唯一标识列出，复用执行次数另行注明。</p><input id="q" aria-label="搜索测试项" placeholder="例如：自动、回调、权限、Excel、MAN01"><div class="scroll"><table id="items"><thead><tr><th>模块</th><th>编号</th><th>测试项</th><th>结果</th><th>实际证据</th></tr></thead><tbody>'''
header+=''.join('<tr>'+''.join('<td>'+esc(str(c[k]))+'</td>' for k in ('layer','id','name','status','detail'))+'</tr>' for c in rows)
header+='''</tbody></table></div></section><section><h2>范围说明</h2><p>所有写入在临时隔离数据库完成，服务端口8876/8877；未联调易车、未调用真实车型库或腾讯文档。现有业务库仅只读核查来源状态。真实浏览器连接超时，未取得截图；未做生产压测、长稳或正式安全审计。模拟任务会改变隔离库额度及历史，但不改变现有业务数据。</p><p>详细报告、逐项JSON、原始日志及代码SHA256摘要与本文件同目录保存。</p></section><script>document.getElementById('q').addEventListener('input',e=>{let q=e.target.value.toLowerCase();document.querySelectorAll('#items tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))})</script></html>'''
(OUT/'上线前测试报告_2026-11-13.html').write_text(header)
summary={'generated_at':stamp,'decision':'LOCAL_SIMULATION_PASS_PRODUCTION_NOT_READY','backend_executions':backend['total'],'backend_unique_ids':len(unique),'flow_passed':30,'scheduler_passed':3,'fixed_product_defects':['QA-01'],'open_release_conditions':[i['id'] for i in issues if i['id']!='QA-01'],'browser_visual':'NOT_COMPLETED','real_media_integration':'NOT_RUN_BY_INSTRUCTION'}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False))
