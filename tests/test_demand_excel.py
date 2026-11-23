import io
import unittest
from datetime import datetime
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from demand_excel import DemandExcel, template_bytes
from demand_sync import DemandSync
from domain import Fault, Platform

ACTOR = {'id': 'u_admin', 'org_id': 'demo', 'role': 'admin'}
CONFIG = {'name': 'Excel会员需求', 'partner_id': 'MEDIA_A', 'media_id': 'MEDIA_A',
          'sale_project': 'MEMBER', 'quantity_basis': 'DAILY'}
HEADERS = ['需求编号', '品牌', '车型', '城市', '下单量']


def workbook_bytes(rows):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class DemandExcelTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.platform = Platform(self.temp.name)
        self.platform.seed()
        self.sync = DemandSync(self.platform, lambda url: self.fail('Excel不允许发起网络同步'))
        self.importer = DemandExcel(self.platform, self.sync)
        self.content = workbook_bytes([HEADERS, ['001-XLS', '宝马', '全系', '上海市,北京市', 3]])

    def tearDown(self):
        self.temp.cleanup()

    def preview(self, content=None, config=None, actor=None):
        return self.importer.preview(actor or ACTOR, '需求.xlsx', content or self.content, config or CONFIG)

    def test_missing_times_use_upload_instant_and_commit_is_idempotent(self):
        with patch('demand_excel.now', return_value='2026-11-18T03:04:05+00:00'):
            result = self.preview()
        self.assertTrue(result['can_commit'])
        row = result['preview'][0]['values']
        self.assertEqual(row['下单日期'], '2026-11-18T11:04:05+08:00')
        self.assertEqual(row['开始时间'], row['下单日期'])
        self.assertEqual(row['结束时间'], '待定')
        committed = self.importer.commit(ACTOR, result['id'])
        self.assertEqual(self.importer.commit(ACTOR, result['id']), committed)
        source = next(source for source in self.sync.sources(ACTOR) if source['id'] == committed['source_id'])
        self.assertEqual(source['source_kind'], 'EXCEL')
        self.assertEqual(source['enabled'], 0)
        demand = self.sync.projects(ACTOR, source['id'])['items'][0]['demand']
        self.assertEqual(demand['demand_no'], '001-XLS')
        self.assertEqual(demand['start_at'], '2026-11-18T03:04:05+00:00')
        self.assertEqual(demand['target_total'], 3)
        self.assertEqual(demand['status'], 'ACTIVE')
        self.sync.tick()
        with self.assertRaises(Fault):
            self.sync.run(ACTOR, source['id'])

    def test_dates_and_total_basis_preserved(self):
        content = workbook_bytes([HEADERS + ['下单日期', '开始时间', '结束时间'],
                                  ['TIME', '宝马', '宝马 X5', '上海市', 5,
                                   datetime(2026, 11, 16), datetime(2026, 11, 17), datetime(2027, 1, 1)]])
        result = self.preview(content, dict(CONFIG, quantity_basis='TOTAL'))
        self.assertEqual(result['preview'][0]['values']['开始时间'], '2026-11-17 00:00:00')
        self.assertTrue(result['can_commit'])
        saved = self.importer.commit(ACTOR, result['id'])
        demand = self.sync.projects(ACTOR, saved['source_id'])['items'][0]['demand']
        self.assertEqual(demand['quantity_basis'], 'TOTAL')
        self.assertFalse(demand['open_ended'])

    def test_bad_rows_duplicate_and_formula_block_import(self):
        for rows in [[HEADERS, ['A', '', '全系', '上海市', 0]],
                     [HEADERS, ['A', '宝马', '全系', '上海市', 1], ['A', '宝马', '全系', '上海市', 1]],
                     [HEADERS + ['开始时间'], ['A', '宝马', '全系', '上海市', 1, '错误日期']]]:
            result = self.preview(workbook_bytes(rows))
            self.assertFalse(result['can_commit'])
            with self.assertRaises(Fault):
                self.importer.commit(ACTOR, result['id'])
        with self.assertRaises(Fault):
            self.preview(workbook_bytes([HEADERS, ['=1+1', '宝马', '全系', '上海市', 1]]))

    def test_shared_required_config_permissions_and_tenant_scope(self):
        for key in ('partner_id', 'media_id', 'sale_project', 'name'):
            config = dict(CONFIG)
            config.pop(key)
            with self.subTest(key=key), self.assertRaises(Fault):
                self.preview(config=config)
        with self.assertRaises(Fault):
            self.preview(actor={'id': 'u_operator', 'role': 'operator', 'org_id': 'demo'})
        result = self.preview()
        with self.assertRaises(Fault):
            self.importer.commit({'id': 'u_other', 'role': 'admin', 'org_id': 'other'}, result['id'])

    def test_conflict_after_preview_rolls_back_whole_batch(self):
        first = self.preview()
        second = self.preview()
        self.importer.commit(ACTOR, first['id'])
        with self.assertRaises(Fault):
            self.importer.commit(ACTOR, second['id'])
        self.assertFalse(self.preview()['can_commit'])
        self.assertEqual(sum(source['source_kind'] == 'EXCEL' for source in self.sync.sources(ACTOR)), 1)

    def test_import_enters_common_matching_pool(self):
        committed = self.importer.commit(ACTOR, self.preview()['id'])
        lead = self.platform.create_lead(ACTOR, {'supplier_id':'SOURCE_A','name': '虚构客户', 'phone': '19900009991',
                                               'brand_name': '宝马', 'model': '宝马 X5',
                                               'city': '上海市', 'is_sold': '否'})
        demand = self.sync.projects(ACTOR, committed['source_id'])['items'][0]['demand']
        self.assertTrue(self.platform.preview(ACTOR, [lead['lead_id']], [demand['id']])['items'][0]['eligible'])

    def test_template_is_readable_and_header_errors_are_clear(self):
        workbook = load_workbook(io.BytesIO(template_bytes()))
        self.assertEqual(workbook.active['A1'].value, '需求编号')
        workbook.close()
        with self.assertRaises(Fault):
            self.preview(workbook_bytes([['品牌'], ['宝马']]))

    def test_quantity_alias_and_optional_number(self):
        no_number = workbook_bytes([['品牌', '车系', '城市', '数量'], ['宝马', '全系', '上海市', 1]])
        result = self.preview(no_number)
        self.assertTrue(result['can_commit'])
        self.assertEqual(result['preview'][0]['values']['需求编号'], '')
        self.assertEqual(result['preview'][0]['values']['下单量'], '1')

    def test_multiple_blank_numbers_are_distinct_and_never_sent_as_internal_ids(self):
        content = workbook_bytes([['品牌', '车型', '城市', '下单量', '需求编号'],
                                  ['宝马', '全系', '上海市', 3, ''],
                                  ['宝马', '宝马 X5', '北京市', 2, None]])
        preview = self.preview(content)
        self.assertEqual(preview['valid'], 2)
        self.assertNotEqual(preview['preview'][0]['key'], preview['preview'][1]['key'])
        saved = self.importer.commit(ACTOR, preview['id'])
        self.assertEqual(saved['created'], 2)
        demands = self.sync.projects(ACTOR, saved['source_id'])['items']
        self.assertTrue(all(row['demand']['demand_no'] == '' for row in demands))
        lead = self.platform.create_lead(ACTOR, {'supplier_id':'SOURCE_A','name': '虚构客户', 'phone': '19900009992',
                                               'brand_name': '宝马', 'model': '宝马 X5',
                                               'city': '上海市', 'is_sold': '否'})
        demand = demands[0]['demand']
        matched = self.platform.preview(ACTOR, [lead['lead_id']], [demand['id']])
        self.assertEqual(matched['items'][0]['demand_no'], '')
        result = self.platform.dispatch(ACTOR, [(lead['lead_id'], demand['id'])], 'blank-no-test')
        delivery = self.platform.detail(ACTOR, 'deliveries', result['items'][0]['delivery_id'])
        self.assertEqual(delivery['demand_no'], '')
        self.assertEqual(delivery['outbound_payload']['demand_no'], '')
        self.assertEqual(delivery['outbound_payload']['source_project']['demand_no'], '')
        self.assertEqual(delivery['demand_snapshot_public']['demand_no'], '')
        self.assertEqual(self.importer.commit(ACTOR, preview['id']), saved)

    def test_multi_models_and_cities_split_and_match_any_member(self):
        import json
        content = workbook_bytes([['品牌', '车型', '城市', '数量'],
                                  ['宝马', '宝马 3系， 宝马 X5;宝马 X5｜宝马 X3',
                                   '北京市／上海市\n上海市', 10]])
        result = self.preview(content)
        self.assertTrue(result['can_commit'])
        self.assertEqual(result['preview'][0]['values']['车型'], '宝马 3系、宝马 X5、宝马 X3')
        self.assertIn('，', result['preview'][0]['raw_values']['车型'])
        saved = self.importer.commit(ACTOR, result['id'])
        demand = self.sync.projects(ACTOR, saved['source_id'])['items'][0]['demand']
        self.assertEqual(len(json.loads(demand['match_models'])), 3)
        self.assertEqual(len(json.loads(demand['match_regions'])), 2)
        for number, model, city, expected in [
            ('19900009981', '宝马 X5', '上海市', True),
            ('19900009982', '宝马 X3', '北京市', True),
            ('19900009983', '宝马 X1', '上海市', False),
            ('19900009984', '宝马 X5', '深圳市', False),
        ]:
            lead = self.platform.create_lead(ACTOR, {'supplier_id':'SOURCE_A','name': '虚构测试客户', 'phone': number,
                                                   'brand_name': '宝马', 'model': model,
                                                   'city': city, 'is_sold': '否'})
            candidate = self.platform.preview(ACTOR, [lead['lead_id']], [demand['id']])['items'][0]
            self.assertEqual(candidate['eligible'], expected, (model, city))
        rows = self.sync.all_projects(ACTOR, filters={'model': '宝马 X5', 'city': '上海市'})
        self.assertTrue(any(row['source_id'] == saved['source_id'] for row in rows['items']))
