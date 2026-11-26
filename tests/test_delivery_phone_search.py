"""分发记录完整手机号检索，只使用临时库中的虚构数据。"""
import unittest

import test_distribution as fixtures

A = fixtures.A


class DeliveryPhoneSearchTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.DistributionTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.p = fixture.p
        self.lead_id = fixture.l
        fixture.send('MEDIA_A')
        fixture.send('MEDIA_B')

    def test_complete_phone_and_country_prefix_find_all_delivery_states(self):
        for query in ('13900008765', '+8613900008765', '139 0000 8765', '１３９００００８７６５'):
            with self.subTest(format='normalized'):
                result = self.p.list(A, 'deliveries', q=query)
                self.assertEqual(result['total'], 2)
                self.assertEqual({row['lead_id'] for row in result['items']}, {self.lead_id})
                self.assertFalse(any('phone_hmac' in row or 'phone_cipher' in row for row in result['items']))
        filtered = self.p.list(A, 'deliveries', q='13900008765', media='MEDIA_A', status='QUEUED')
        self.assertEqual(filtered['total'], 1)
        self.assertFalse(filtered['has_more'])
        self.assertEqual(self.p.list(A, 'deliveries', q='13900008764')['total'], 0)

    def test_phone_field_permission_applies_to_search(self):
        self.p.update_lead_fields(A, 'u_operator', ['lead_no'])
        operator = {'id': 'u_operator', 'org_id': 'demo', 'role': 'operator'}
        self.assertGreater(self.p.list(operator, 'deliveries')['total'], 0)
        self.assertEqual(self.p.list(operator, 'deliveries', q='13900008765')['total'], 0)

    def test_phone_search_never_uses_other_org_leads(self):
        other = {'id': 'fixture-other', 'org_id': 'other', 'role': 'admin'}
        self.assertEqual(self.p.list(other, 'deliveries', q='13900008765')['total'], 0)
        with self.p.tx() as conn:
            conn.execute("UPDATE leads SET org_id='other' WHERE id=?", (self.lead_id,))
        self.assertEqual(self.p.list(A, 'deliveries', q='13900008765')['total'], 0)

    def test_existing_record_search_remains_available(self):
        first = self.p.list(A, 'deliveries')['items'][0]
        self.assertEqual(self.p.list(A, 'deliveries', q=first['id'])['total'], 1)
