"""Isolated HTTP fixture. No scheduler, no external network, no business database."""
import sys,tempfile,json,socket,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
import server
from domain import Platform
from demand_sync import DemandSync
from media_vehicles import MediaVehicles
from test_synced_demands import record
parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8876);parser.add_argument('--profile',choices=['flow','cached','seed'],default='flow');args=parser.parse_args()
temp=tempfile.TemporaryDirectory(prefix='prelaunch-only-');p=Platform(temp.name);p.seed()
if args.profile=='flow':
 with p.tx() as c:c.execute("UPDATE demands SET status='PAUSED'");c.execute("UPDATE leads SET status='BLOCKED'")
def fetch(url):
 if args.profile=='cached':return json.loads(Path('/private/tmp/qq-demand-parsed.json').read_text())
 rows=[record(key='QA-KA-001',brand='奥迪',model='奥迪 A6L',regions='北京市',qty='2')] if 'DQA_KA' in url else [record(key='QA-RELAY-001',model='宝马 X5',regions='北京市',qty='2')] if 'DQA_RELAY' in url else [record(key='QA-MEMBER-001',model='宝马 X5',regions='上海市',qty='2')]
 return {'title':'上线前隔离需求','headers':list(rows[0]['values']),'records':rows}
sync=DemandSync(p,fetch)
if args.profile=='cached':
 a={'id':'u_admin','role':'admin','org_id':'demo'};s=sync.save(a,{'name':'易车会员需求','url':'https://docs.qq.com/sheet/DVFNzZWxTWkNZV3RQ?tab=BB08J2','partner_id':'MEDIA_A','media_id':'MEDIA_A','sale_project':'MEMBER','enabled':0});assert sync.run(a,s['id'])['status']=='SUCCESS'
server.P=p;server.DEMAND_SYNC=sync;server.VEHICLES=MediaVehicles(p)
original=socket.socket
class LocalSocket(original):
 def connect(self,address):
  if self.family in (socket.AF_INET,socket.AF_INET6) and address[0] not in ('127.0.0.1','localhost','::1'):raise RuntimeError('PRELAUNCH_EXTERNAL_NETWORK_BLOCKED')
  return super().connect(address)
socket.socket=LocalSocket
print(json.dumps({'ready':True,'profile':args.profile,'port':args.port,'external_sending':False,'temporary_database':True}),flush=True)
try:server.ThreadingHTTPServer(('127.0.0.1',args.port),server.Handler).serve_forever()
finally:temp.cleanup()
