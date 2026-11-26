import unittest
from tempfile import TemporaryDirectory
from domain import Platform,later
A={'id':'u_admin','org_id':'demo','role':'admin'}
class DemandSummaryTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.p=Platform(self.temp.name);self.p.seed()
  with self.p.tx() as c:
   c.execute("UPDATE demands SET status='CLOSED'")
   self.ids=[r[0] for r in c.execute('SELECT id FROM demands ORDER BY id LIMIT 2')]
   c.execute("UPDATE deliveries SET day='2000-01-01'")
   for i,id in enumerate(self.ids):
    c.execute("UPDATE demands SET buyer_id='MEDIA_A',media_id='MEDIA_A',sale_project='MEMBER',status='ACTIVE',start_at=?,end_at=?,target_total=10,reserved_count=2,valid_count=3,quantity_basis=? WHERE id=?",(later(days=-1),later(days=10),'DAILY' if i else 'TOTAL',id))
 def tearDown(self):self.temp.cleanup()
 def group(self):return next(g for g in self.p.demand_summary(A)['items'] if g['buyer_id']=='MEDIA_A' and g['media_id']=='MEDIA_A' and g['project']=='MEMBER')
 def test_group_and_weighted_active_progress(self):
  g=self.group();self.assertEqual(g['active_count'],2);self.assertEqual(g['target_total'],20);self.assertEqual((g['valid_count'],g['reserved_count'],g['available_count']),(3,2,15));self.assertEqual(g['attainment_rate'],15);self.assertEqual(g['urgency_stars'],5)
 def test_daily_ignores_cumulative_and_excludes_closed(self):
  g=self.group();self.assertEqual(g['basis']['DAILY']['valid_count'],0)
  with self.p.tx() as c:c.execute("UPDATE demands SET status='CLOSED' WHERE id=?",(self.ids[0],))
  g=self.group();self.assertEqual(g['active_count'],1);self.assertEqual(g['valid_count'],0);self.assertEqual(g['available_count'],10)
 def test_time_bounds_and_empty_progress(self):
  with self.p.tx() as c:
   c.execute('UPDATE demands SET end_at=? WHERE id=?',(later(hours=-1),self.ids[0]));c.execute('UPDATE demands SET start_at=? WHERE id=?',(later(days=1),self.ids[1]))
  g=self.group();self.assertEqual(g['active_count'],0);self.assertEqual(g['target_total'],0);self.assertEqual(g['status'],'WAITING');self.assertEqual(g['urgency_stars'],0)
 def test_filters_pagination_and_tenant(self):
  g=self.group();self.assertTrue(self.p.demand_summary(A,q=g['buyer_name'],status='ACTIVE',media='MEDIA_A')['total']>=1)
  r=self.p.demand_summary(A,page=2);self.assertEqual(r['items'],[]);self.assertGreater(r['total'],0)
  self.assertEqual(self.p.demand_summary({'id':'other','role':'admin','org_id':'other'})['total'],0)
