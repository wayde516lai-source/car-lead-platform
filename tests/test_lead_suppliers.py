"""供应商必选、来源归属、组合筛选及字段权限，均使用临时数据库。"""
import csv
import io
import tempfile
import unittest

from domain import Fault, Platform
from security import canonical

ACTOR = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}


class LeadSupplierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = Platform(self.temp.name)
        self.p.seed()
        self.row = {'supplier_id': 'SOURCE_A', 'name': '供应商隔离测试', 'phone': '19900007401',
                    'brand_name': '宝马', 'model': '宝马 X5', 'city': '上海市', 'is_sold': '否',
                    'acquisition_channel': '供应商测试投放'}

    def content(self, phone='19900007402', channel='供应商测试搜索'):
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerows([['姓名','电话','品牌','车型','城市','是否售卖','获客渠道'],
                          ['女士',phone,'宝马','宝马 X5','上海市','否',channel]])
        return stream.getvalue().encode()

    def test_manual_and_import_require_valid_supplier(self):
        row = dict(self.row)
        row.pop('supplier_id')
        with self.assertRaises(Fault) as caught:
            self.p.create_lead(ACTOR, row)
        self.assertEqual(caught.exception.code, 'SUPPLIER_REQUIRED')
        with self.assertRaises(Fault) as caught:
            self.p.import_preview(ACTOR, 'sample.csv', self.content())
        self.assertEqual(caught.exception.code, 'SUPPLIER_REQUIRED')
        for supplier in ('MEDIA_A', 'INTAKE_SOURCE:demo', 'missing'):
            with self.subTest(supplier=supplier), self.assertRaises(Fault):
                self.p.create_lead(ACTOR, {**self.row, 'supplier_id': supplier})
        with self.p.tx() as conn:
            conn.execute("UPDATE partners SET enabled=0 WHERE id='SOURCE_A'")
        with self.assertRaises(Fault):
            self.p.create_lead(ACTOR, self.row)
        with self.assertRaises(Fault):
            self.p.import_preview(ACTOR, 'sample.csv', self.content(), supplier_id='SOURCE_A')

    def test_batch_supplier_persisted_and_both_filters_combine(self):
        manual = self.p.create_lead(ACTOR, self.row)['lead_id']
        preview = self.p.import_preview(ACTOR, 'sample.csv', self.content(), supplier_id='SOURCE_B')
        self.assertEqual(preview['valid'], 1)
        saved = self.p.import_commit(ACTOR, preview['id'], 'ALL_VALID')['items'][0]['lead_id']
        manual_detail = self.p.detail(ACTOR, 'leads', manual)
        imported_detail = self.p.detail(ACTOR, 'leads', saved)
        self.assertEqual(manual_detail['supplier_id'], 'SOURCE_A')
        self.assertEqual(imported_detail['supplier_id'], 'SOURCE_B')
        self.assertEqual(imported_detail['acquisition_channel'], '供应商测试搜索')
        result = self.p.list(ACTOR, 'leads', lead_filters={'supplier': 'SOURCE_B', 'acquisition_channel': '供应商测试搜索'})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['id'], saved)
        self.assertEqual(result['items'][0]['supplier_id'], 'SOURCE_B')
        self.assertEqual(self.p.list(ACTOR, 'leads', lead_filters={'supplier': 'SOURCE_B', 'acquisition_channel': '供应商测试投放'})['total'], 0)
        self.assertIn('供应商测试搜索', [row['value'] for row in result['filter_options']['acquisition_channel']])

    def test_permissions_org_and_changed_supplier_at_commit(self):
        self.p.create_lead(ACTOR, self.row)
        with self.p.tx() as conn:
            conn.execute('INSERT INTO lead_field_permissions VALUES(?,?)', ('u_operator', canonical(['lead_no'])))
        operator = {'id': 'u_operator', 'org_id': 'demo', 'role': 'operator'}
        plain = self.p.list(operator, 'leads')
        hidden = self.p.list(operator, 'leads', lead_filters={'supplier': 'missing', 'acquisition_channel': 'missing'})
        self.assertEqual(plain['total'], hidden['total'])
        self.assertNotIn('supplier', hidden['filter_options'])
        self.assertNotIn('acquisition_channel', hidden['filter_options'])
        detail = self.p.detail(operator, 'leads', plain['items'][0]['id'])
        self.assertNotIn('supplier', detail)
        self.assertNotIn('supplier_id', detail)
        self.assertNotIn('acquisition_channel', detail)
        other = {**ACTOR, 'org_id': 'other'}
        self.assertEqual(self.p.lead_entry_options(other)['suppliers'], [])
        with self.assertRaises(Fault):
            self.p.create_lead(other, self.row)
        preview = self.p.import_preview(ACTOR, 'sample.csv', self.content(), supplier_id='SOURCE_B')
        with self.p.tx() as conn:
            conn.execute("UPDATE partners SET enabled=0 WHERE id='SOURCE_B'")
        with self.assertRaises(Fault):
            self.p.import_commit(ACTOR, preview['id'], 'ALL_VALID')
        self.assertEqual(self.p.list(ACTOR, 'leads', lead_filters={'acquisition_channel': '供应商测试搜索'})['total'], 0)

    def test_supplier_options_and_channel_conflict(self):
        options = self.p.lead_entry_options(ACTOR)['suppliers']
        self.assertEqual({row['id'] for row in options}, {'SOURCE_A','SOURCE_B','SOURCE_C'})
        with self.assertRaises(Fault) as caught:
            self.p.import_preview(ACTOR, 'sample.csv', self.content(), channel='PURCHASE', supplier_id='SOURCE_A')
        self.assertEqual(caught.exception.code, 'SUPPLIER_CHANNEL_CONFLICT')


if __name__ == '__main__':
    unittest.main()
