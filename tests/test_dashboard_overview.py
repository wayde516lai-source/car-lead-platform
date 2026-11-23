import unittest
from tempfile import TemporaryDirectory
from domain import Platform
A={'id':'u_admin','org_id':'demo','role':'admin'}
class DashboardOverviewTests(unittest.TestCase):
 def test_counts_use_buyer_role_distinct_brands_and_current_demands(self):
  with TemporaryDirectory() as tmp:
   p=Platform(tmp);p.seed()
   with p.tx() as c:
    # Keep finished demand in totals; exclude source removals.
    ids=[r[0] for r in c.execute("SELECT id FROM demands WHERE org_id='demo' ORDER BY id")]
    c.execute("UPDATE demands SET status='CLOSED',match_brand=' 宝马 ' WHERE id=?",(ids[0],))
    c.execute("UPDATE demands SET buyer_id='AGENCY_A',match_brand='宝马' WHERE id=?",(ids[1],))
    c.execute("UPDATE demands SET sync_state='REMOVED' WHERE id=?",(ids[2],))
    rows=c.execute("SELECT d.*,p.role,cat.brand_name FROM demands d JOIN partners p ON p.id=d.buyer_id LEFT JOIN catalog cat ON cat.series_id=d.series_id WHERE d.org_id='demo' AND COALESCE(d.sync_state,'')!='REMOVED'").fetchall()
    media=c.execute("SELECT count(*) FROM partners WHERE org_id='demo' AND role='MEDIA'").fetchone()[0]
    agencies=c.execute("SELECT count(*) FROM partners WHERE org_id='demo' AND role='AGENCY'").fetchone()[0]
    expected_brands={str(r['match_brand'] or r['brand_name']).strip().casefold() for r in rows}
   o=p.reports(A)['overview'];self.assertEqual(o['total_demands'],len(rows));self.assertEqual(o['media_demands']+o['agency_demands'],len(rows));self.assertEqual(o['media_count'],media);self.assertEqual(o['agency_count'],agencies);self.assertEqual(o['brand_count'],len(expected_brands));self.assertEqual(o['active_demands'],sum(r['status']=='ACTIVE' for r in rows))
   other=p.reports({'id':'u_other','org_id':'other','role':'admin'})['overview'];self.assertEqual(other['total_demands'],0);self.assertEqual(other['media_count'],0);self.assertEqual(other['brand_count'],0)

 def test_matchable_count_matches_pool_and_changes_with_rules(self):
  from test_synced_demands import SyncedDemandTests
  fixture=SyncedDemandTests();fixture.setUp()
  try:
   p=fixture.p;good=fixture.lead();fixture.lead(city='广州市')
   def check(expected):
    self.assertEqual(p.reports(A)['matchable'],expected)
    self.assertEqual(p.matching_leads(A)['total'],expected)
   check(1)
   with p.tx() as c:c.execute("UPDATE routes SET enabled=0 WHERE id=?",(fixture.d['route_id'],))
   check(0)
   with p.tx() as c:c.execute("UPDATE routes SET enabled=1 WHERE id=?",(fixture.d['route_id'],))
   check(1)
   fixture.send(good);check(0) # Daily quota reserved; same-media duplicate also blocked.
   with p.tx() as c:c.execute("UPDATE demands SET status='PAUSED' WHERE org_id='demo'")
   check(0)
  finally:fixture.tearDown()

 def test_order_quantities_include_finished_exclude_removed_and_keep_units(self):
  with TemporaryDirectory() as tmp:
   p=Platform(tmp);p.seed()
   with p.tx() as c:
    ids=[r[0] for r in c.execute("SELECT id FROM demands WHERE org_id='demo' ORDER BY id")]
    c.execute("UPDATE demands SET sync_state='REMOVED' WHERE org_id='demo'")
    c.execute("UPDATE demands SET sync_state='ACTIVE',quantity_basis='DAILY',target_total=3 WHERE id=?",(ids[0],))
    c.execute("UPDATE demands SET sync_state='ENDED',status='CLOSED',quantity_basis='TOTAL',target_total=7 WHERE id=?",(ids[1],))
   summary=p.reports(A)['overview']
   self.assertEqual(summary['order_quantities'],{'daily':3,'total':7,'invalid_rows':0})
   self.assertEqual(summary['total_demands'],2)
   other=p.reports({'id':'u_other','org_id':'other','role':'admin'})['overview']
   self.assertEqual(other['order_quantities'],{'daily':0,'total':0,'invalid_rows':0})


class MatchMetricSplitTests(unittest.TestCase):
 def setUp(self):
  from test_synced_demands import SyncedDemandTests
  self.fixture=SyncedDemandTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
  self.p=self.fixture.p

 def counts(self):
  result=self.p.reports(A)
  self.assertEqual(result['dispatchable'],result['matchable'])
  self.assertEqual(result['dispatchable'],self.p.matching_leads(A)['total'])
  return result['business_matchable'],result['dispatchable']

 def test_execution_blocks_do_not_erase_business_candidates(self):
  self.fixture.lead();self.fixture.lead(city='广州市')
  self.assertEqual(self.counts(),(1,1))
  with self.p.tx() as conn:conn.execute("UPDATE settings SET dispatch_enabled=0 WHERE org_id='demo'")
  self.assertEqual(self.counts(),(1,0))
  with self.p.tx() as conn:
   conn.execute("UPDATE settings SET dispatch_enabled=1 WHERE org_id='demo'")
   conn.execute('UPDATE routes SET enabled=0 WHERE id=?',(self.fixture.d['route_id'],))
  self.assertEqual(self.counts(),(1,0))
  with self.p.tx() as conn:
   conn.execute('UPDATE routes SET enabled=1 WHERE id=?',(self.fixture.d['route_id'],))
   conn.execute("UPDATE partners SET enabled=0 WHERE id='MEDIA_A'")
  self.assertEqual(self.counts(),(1,0))
  other=self.p.reports({'id':'u_other','org_id':'other','role':'admin'})
  self.assertEqual((other['business_matchable'],other['dispatchable']),(0,0))

 def test_sale_or_quota_block_preserves_basic_count(self):
  lead=self.fixture.lead()
  self.assertEqual(self.counts(),(1,1))
  self.fixture.send(lead)
  self.assertEqual(self.counts(),(1,0))

 def test_expired_and_inactive_demand_not_counted(self):
  self.fixture.lead()
  self.assertEqual(self.counts(),(1,1))
  with self.p.tx() as conn:conn.execute("UPDATE leads SET expires_at='2000-01-01T00:00:00+00:00'")
  self.assertEqual(self.counts(),(0,0))
  with self.p.tx() as conn:
   conn.execute("UPDATE leads SET expires_at='2099-01-01T00:00:00+00:00'")
   conn.execute("UPDATE demands SET status='PAUSED'")
  self.assertEqual(self.counts(),(0,0))
