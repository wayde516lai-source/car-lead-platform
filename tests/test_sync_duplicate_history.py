import copy,unittest
from tempfile import TemporaryDirectory
from domain import Platform,Fault
from demand_sync import DemandSync
from qq_sheet import normalize_grid,QQSheetError
A={'id':'u_admin','role':'admin','org_id':'demo'}
URL='https://docs.qq.com/sheet/DTESTSYNC?tab=TEST'
HEAD=['需求编号','品牌','车型','城市&地区','下单量','开始时间','结束时间','需求执行状态（渠道维护）']
OLD=['103171','东风风行','星海V6','全国',5,'2026-11-01','2026-11-05','结束']
NEW=['103171','东风风行','星海V6','全国',10,'2026-11-13','待定','']
def parsed(rows,rules=None):
 return normalize_grid({(r,c):v for r,row in enumerate([HEAD]+rows) for c,v in enumerate(row)},'需求表',URL,'TEST',rules)
class DuplicateHistoryTests(unittest.TestCase):
 def test_only_explicit_keys_resolve_and_last_row_wins(self):
  with self.assertRaises(QQSheetError):parsed([OLD,NEW])
  result=parsed([OLD,NEW],{'103171':'LAST_ROW_WINS'})
  self.assertEqual(result['count'],1);self.assertEqual(result['records'][0]['values']['下单量'],'10');self.assertEqual(result['superseded_records'][0]['values']['下单量'],'5')
  other=copy.deepcopy(OLD);other[0]='999'
  with self.assertRaises(QQSheetError):parsed([OLD,NEW,other,other],{'103171':'LAST_ROW_WINS'})
 def test_same_demand_reopened_history_retained_idempotent_and_tenant_scoped(self):
  with TemporaryDirectory() as tmp:
   p=Platform(tmp);p.seed();data=parsed([OLD]);s=DemandSync(p,lambda url:copy.deepcopy(data))
   source=s.save(A,{'name':'测试','url':URL,'partner_id':'MEDIA_A','media_id':'MEDIA_A','sale_project':'MEMBER','quantity_basis':'DAILY'})['id']
   self.assertEqual(s.run(A,source)['status'],'SUCCESS');row=s.projects(A,source)['items'][0];did=row['demand']['id'];pid=row['id'];self.assertEqual(row['demand']['status'],'CLOSED')
   data=parsed([OLD,NEW],{'103171':'LAST_ROW_WINS'})
   self.assertEqual(s.run(A,source)['status'],'FAILED');self.assertEqual(s.detail(A,pid)['history'],[])
   s.approve_duplicate(A,source,'103171');result=s.run(A,source);self.assertEqual(result['status'],'SUCCESS')
   current=s.detail(A,pid);self.assertEqual(current['demand']['id'],did);self.assertEqual(current['demand']['status'],'ACTIVE');self.assertEqual(current['demand']['daily_limit'],10);self.assertEqual(len(current['history']),1);self.assertEqual(current['history'][0]['values']['下单量'],'5')
   self.assertEqual(s.run(A,source)['updated'],0);self.assertEqual(len(s.detail(A,pid)['history']),1)
   c=p.connect()
   try:self.assertGreaterEqual(c.execute('SELECT count(*) FROM demand_versions WHERE demand_id=?',(did,)).fetchone()[0],2)
   finally:c.close()
   other={'id':'u_other','role':'admin','org_id':'other'}
   with self.assertRaises(Fault):s.approve_duplicate(other,source,'103171')
   with self.assertRaises(Fault):s.detail(other,pid)
