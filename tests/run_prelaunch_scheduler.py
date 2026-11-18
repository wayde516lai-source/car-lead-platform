"""Real application scheduler threads, isolated DB and mocked sheet fetch only."""
import sys,json,tempfile,subprocess,time,os,signal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from domain import Platform
from demand_sync import DemandSync
A={'id':'u_admin','org_id':'demo','role':'admin'};OUT=ROOT/'docs/prelaunch-2026-11-13'
result={'cases':[],'scope':'real server.run timers; no manual scan request; mocked document response; isolated database'}
with tempfile.TemporaryDirectory(prefix='prelaunch-scheduler-') as folder:
 p=Platform(folder);p.seed()
 with p.tx() as c:
  c.execute("UPDATE leads SET status='BLOCKED'");c.execute("UPDATE demands SET status='PAUSED'");d=dict(c.execute("SELECT * FROM demands WHERE route_id='DIRECT_B'").fetchone())
  c.execute("UPDATE demands SET status='ACTIVE',sale_project='MEMBER',target_total=2,daily_limit=2 WHERE id=?",(d['id'],))
 cfg=next(r for r in p.catalogs(A)['partners'] if r['id']=='MEDIA_B')['cooperation_projects'][0]
 p.project_mode_update(A,'MEDIA_B','MEMBER',{'matching_mode':'AUTO','version':cfg['version']})
 lead=p.create_lead(A,{'name':'定时器虚构客户','phone':'13900009201','brand_name':'宝马','model':'宝马 X5','city':'上海市','is_sold':'否'})
 source=DemandSync(p).save(A,{'name':'定时器隔离需求','url':'https://docs.qq.com/sheet/DQA_SCHED?tab=BB08J2','partner_id':'MEDIA_C','media_id':'MEDIA_C','sale_project':'MEMBER','enabled':1,'interval_minutes':1})
 wrapper="""import sys,socket
sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[1]+'/tests')
import server
from demand_sync import DemandSync
from test_synced_demands import record
old=socket.socket
class Guard(old):
 def connect(self,address):
  if self.family in (socket.AF_INET,socket.AF_INET6) and address[0] not in ('127.0.0.1','localhost','::1'):raise RuntimeError('EXTERNAL_NETWORK_BLOCKED')
  return super().connect(address)
socket.socket=Guard
class LocalDemandSync(DemandSync):
 def __init__(self,p):
  row=record(key='QA-SCHEDULED',brand='奥迪',model='奥迪 A6L',regions='北京市',qty='2')
  super().__init__(p,lambda url:{'title':'定时隔离样本','headers':list(row['values']),'records':[row]})
server.DemandSync=LocalDemandSync
folder=sys.argv[2];sys.argv=['server.py','--port','8877','--data',folder,'--no-vehicle-sync'];server.run()
"""
 start=time.monotonic()
 with (OUT/'scheduler-server.log').open('wb') as log:
  proc=subprocess.Popen([sys.executable,'-c',wrapper,str(ROOT),folder],stdout=log,stderr=log,stdin=subprocess.DEVNULL,env={**os.environ,'YICHE_ENABLE_LIVE_PUSH':'0'})
  sync_seen=auto_seen=None
  try:
   for _ in range(180):
    if proc.poll() is not None:raise RuntimeError('Scheduler server exited')
    c=p.connect();synced=c.execute('SELECT last_success_at FROM demand_sync_sources WHERE id=?',(source['id'],)).fetchone()[0];sent=c.execute("SELECT count(*) FROM deliveries WHERE lead_id=? AND dispatch_mode='AUTO' AND send_status='ACKED'",(lead['lead_id'],)).fetchone()[0];c.close()
    elapsed=time.monotonic()-start
    if synced and sync_seen is None:sync_seen=elapsed
    if sent and auto_seen is None:auto_seen=elapsed
    if sync_seen is not None and auto_seen is not None:break
    time.sleep(.25)
   assert sync_seen is not None and 7<=sync_seen<20,('scheduled sync',sync_seen)
   assert auto_seen is not None and 25<=auto_seen<45,('auto scheduler',auto_seen)
   c=p.connect();count=c.execute('SELECT count(*) FROM deliveries WHERE lead_id=?',(lead['lead_id'],)).fetchone()[0];c.close();assert count==1
   assert p.integrity(A)['passed']
   result['cases']=[{'id':'SCHED01','name':'真实10秒检查线程触发到期需求同步','status':'PASS','observed_seconds':round(sync_seen,2)},{'id':'SCHED02','name':'真实30秒自动扫描创建并模拟发送，未手动调用扫描','status':'PASS','observed_seconds':round(auto_seen,2)},{'id':'SCHED03','name':'自动任务仅一笔，配额与账本一致','status':'PASS'}];result['passed']=True
  except Exception as e:result.update(passed=False,error=repr(e))
  finally:proc.terminate();proc.wait(timeout=5)
(OUT/'scheduler-cases.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False));sys.exit(not result.get('passed'))
