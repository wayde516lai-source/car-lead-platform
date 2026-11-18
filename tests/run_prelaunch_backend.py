"""Run the full unittest suite with per-case evidence and loopback-only networking."""
import sys,json,time,unittest,socket,datetime,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
OUT=ROOT/'docs/prelaunch-2026-11-13';OUT.mkdir(parents=True,exist_ok=True)
blocked=[];original_socket=socket.socket
class LocalSocket(original_socket):
 def connect(self,address):
  if self.family in (socket.AF_INET,socket.AF_INET6) and address[0] not in ('127.0.0.1','localhost','::1'):
   blocked.append(str(address));raise RuntimeError('PRELAUNCH_EXTERNAL_NETWORK_BLOCKED')
  return super().connect(address)
socket.socket=LocalSocket
class Evidence(unittest.TextTestResult):
 def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.cases=[];self.started={}
 def startTest(self,test):self.started[test.id()]=time.perf_counter();super().startTest(test)
 def record(self,test,status,detail=''):
  self.cases.append({'id':test.id(),'module':test.__class__.__module__,'name':test._testMethodName,'description':test.shortDescription() or '', 'status':status,'seconds':round(time.perf_counter()-self.started[test.id()],4),'detail':detail})
 def addSuccess(self,test):self.record(test,'PASS');super().addSuccess(test)
 def addFailure(self,test,err):self.record(test,'FAIL',self._exc_info_to_string(err,test));super().addFailure(test,err)
 def addError(self,test,err):self.record(test,'ERROR',self._exc_info_to_string(err,test));super().addError(test,err)
 def addSkip(self,test,reason):self.record(test,'SKIP',reason);super().addSkip(test,reason)
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py');begin=time.perf_counter();result=unittest.TextTestRunner(verbosity=2,resultclass=Evidence).run(suite)
payload={'scope':'isolated temporary databases; mocked media; loopback networking only','finished_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'seconds':round(time.perf_counter()-begin,3),'total':result.testsRun,'passed':sum(c['status']=='PASS' for c in result.cases),'failed':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'blocked_external_attempts':blocked,'cases':result.cases}
(OUT/'backend-cases.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n');sys.exit(not result.wasSuccessful())
