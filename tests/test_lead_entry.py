import csv
import io
import unittest
from tempfile import TemporaryDirectory

from domain import Platform, Fault, now
from security import canonical

ADMIN={'id':'u_admin','org_id':'demo','role':'admin'}

class LeadEntry(unittest.TestCase):
    def setUp(self):
        self.root=TemporaryDirectory()
        self.platform=Platform(self.root.name)
        self.platform.seed()
        self.row={
            'supplier_id':'SOURCE_A','name':'测试客户','phone':'13900008771','brand_name':'宝马',
            'series_id':'BMW_X5','city':'上海市','is_sold':'否',
        }

    def tearDown(self):
        self.root.cleanup()

    def create(self,**changes):
        return self.platform.create_lead(ADMIN,dict(self.row,**changes))

    def test_required_fields_and_conditional_sale_fields(self):
        for field in ('name','phone','brand_name','series_id','city','is_sold'):
            row=self.row.copy()
            row.pop(field)
            with self.subTest(field=field), self.assertRaises(Fault):
                self.platform.create_lead(ADMIN,row)
        for missing in ('sale_media_id','sale_project','sold_at'):
            row=dict(self.row,is_sold='是',sale_media_id='MEDIA_A',sale_project='KA',sold_at=now())
            row.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(Fault):
                self.platform.create_lead(ADMIN,row)
        with self.assertRaises(Fault):
            self.create(is_sold='是',sale_media_id='AGENCY_A',sale_project='KA',sold_at=now())
        with self.assertRaises(Fault):
            self.create(is_sold='是',sale_media_id='MEDIA_A',sale_project='其他',sold_at=now())

    def test_unsold_ready_without_review_and_false_clears_sale(self):
        result=self.create(sale_media_id='MEDIA_A',sale_project='wrong',sold_at='wrong')
        detail=self.platform.detail(ADMIN,'leads',result['lead_id'])
        self.assertEqual(detail['status'],'READY')
        self.assertEqual((detail['is_sold'],detail['sales_count'],detail['project']),('否',0,''))
        c=self.platform.connect()
        try:
            lead=self.platform.one(c,'leads',result['lead_id'],ADMIN)
            demand=dict(c.execute("SELECT * FROM demands WHERE demand_no='AUTOHOME-0098'").fetchone())
            self.assertIsNone(self.platform._block(c,ADMIN,lead,demand))
        finally:
            c.close()

    def test_history_count_retry_project_and_duplicate_block(self):
        sold=dict(self.row,source_record_id='sold-1',is_sold='是',
                  sale_media_id='MEDIA_A',sale_project='会员',sold_at=now())
        result=self.platform.create_lead(ADMIN,sold)
        self.platform.create_lead(ADMIN,sold)
        detail=self.platform.detail(ADMIN,'leads',result['lead_id'])
        self.assertEqual((detail['is_sold'],detail['sales_count'],detail['project']),('是',1,'会员'))
        self.assertEqual(len(detail['sale_history']),1)
        with self.platform.tx() as c:
            lead=self.platform.one(c,'leads',result['lead_id'],ADMIN)
            demand=dict(c.execute("SELECT * FROM demands WHERE demand_no='000123'").fetchone())
            self.assertEqual(self.platform._block(c,ADMIN,lead,demand),'FINAL_MEDIA_DUPLICATE')
            self.assertEqual(self.platform._matched_media(c,result['lead_id']),{'MEDIA_A'})
        reopened=Platform(self.root.name)
        self.assertEqual(reopened.detail(ADMIN,'leads',result['lead_id'])['sales_count'],1)

    def test_import_shared_validation_and_repeated_commit(self):
        text=io.StringIO()
        writer=csv.writer(text)
        writer.writerow(['姓名','电话','品牌','车型','城市','是否售卖','售卖媒体','项目','售卖时间'])
        writer.writerow(['未售客户','13900008772','宝马','宝马 X5','上海市','否','','',''])
        writer.writerow(['已售客户','13900008773','宝马','宝马 X5','上海市','是','MEDIA_A','KA',now()])
        writer.writerow(['缺少项目','13900008774','宝马','宝马 X5','上海市','是','MEDIA_A','',now()])
        preview=self.platform.import_preview(ADMIN,'entry.csv',text.getvalue().encode(),supplier_id='SOURCE_A')
        self.assertEqual((preview['valid'],preview['errors']),(2,1))
        result=self.platform.import_commit(ADMIN,preview['id'],'VALID_ROWS')
        self.assertEqual((result['created'],result['skipped']),(2,1))
        self.assertEqual(self.platform.import_commit(ADMIN,preview['id'],'VALID_ROWS'),result)
        sold=self.platform.detail(ADMIN,'leads',result['items'][1]['lead_id'])
        self.assertEqual((sold['sales_count'],sold['project']),(1,'KA'))
        unsold=self.platform.detail(ADMIN,'leads',result['items'][0]['lead_id'])
        self.assertEqual((unsold['status'],unsold['is_sold']),('READY','否'))

    def test_columns_order_and_sale_field_permissions(self):
        lead_id=self.create(is_sold='是',sale_media_id='MEDIA_A',sale_project='KA',sold_at=now())['lead_id']
        listing=self.platform.list(ADMIN,'leads')
        fields=[col['key'] for col in listing['columns']]
        self.assertEqual(fields[fields.index('sold_media')+1],'project')
        self.assertEqual(fields[fields.index('collected_at')-1],'sold_at')
        self.assertTrue({'phone','is_sold','sold_media','sold_at','project'}.issubset(fields))
        self.assertFalse({'channel','status','source_status'}.intersection(fields))
        with self.platform.tx() as c:
            c.execute('INSERT INTO lead_field_permissions VALUES(?,?)',('u_operator',canonical(['lead_no'])))
        actor={'id':'u_operator','org_id':'demo','role':'operator'}
        restricted=self.platform.detail(actor,'leads',lead_id)
        for key in ('phone','customer','is_sold','sold_media','sold_at','project'):
            self.assertNotIn(key,restricted)
        self.assertEqual(restricted['sale_history'],[])

    def test_strict_telephone_brand_and_model_entry(self):
        for phone in ('打发打发','1390000877','139000087711','23900008771','+8613900008771','13３00008771'):
            with self.subTest(phone=phone),self.assertRaises(Fault):
                self.create(phone=phone)
        row=dict(self.row)
        row.pop('series_id')
        row.update(brand_name='手动品牌',model='手动车型')
        lead_id=self.platform.create_lead(ADMIN,row)['lead_id']
        detail=self.platform.detail(ADMIN,'leads',lead_id)
        self.assertEqual((detail['brand'],detail['model']),('手动品牌','手动车型'))
        options=self.platform.lead_entry_options(ADMIN)['models']
        self.assertTrue(any(r['brand_name']=='手动品牌' and r['series_name']=='手动车型' for r in options))
        other=dict(ADMIN,org_id='other')
        self.assertFalse(any(r['brand_name']=='手动品牌' for r in self.platform.lead_entry_options(other)['models']))

    def test_all_series_and_gender(self):
        row=dict(self.row)
        row.pop('series_id')
        row.update(model='全系',gender='男')
        lead_id=self.platform.create_lead(ADMIN,row)['lead_id']
        detail=self.platform.detail(ADMIN,'leads',lead_id)
        self.assertEqual((detail['model'],detail['gender']),('全系','男'))
        c=self.platform.connect()
        try:
            lead=self.platform.one(c,'leads',lead_id,ADMIN)
            demand=dict(c.execute("SELECT * FROM demands WHERE demand_no='AUTOHOME-0098'").fetchone())
            for series_id in ('BMW_X5','BMW_3'):
                self.assertIsNone(self.platform._block(c,ADMIN,lead,dict(demand,series_id=series_id)))
            self.assertEqual(self.platform._block(c,ADMIN,lead,dict(demand,series_id='AUDI_A6')),'MODEL_MISMATCH')
        finally:
            c.close()
        self.platform.edit_lead_gender(ADMIN,lead_id,'FEMALE',detail['version'])
        detail=self.platform.detail(ADMIN,'leads',lead_id)
        self.assertEqual((detail['gender'],detail['customer']),('女','测女士'))

    def test_title_only_names_display_without_losing_original_value(self):
        from domain import customer_label
        for name, title, expected in [('先生','','先生'), (' 女士 ','','女士'),
                                       ('先生','女士','先生'), ('张三','先生','张先生'),
                                       ('欧阳测试','女士','欧阳女士'), ('李女士','','李女士'),
                                       ('','先生','')]:
            with self.subTest(name=name, title=title):
                self.assertEqual(customer_label(name,title),expected)

    def test_title_only_excel_names_survive_import_and_field_permissions(self):
        from openpyxl import Workbook
        book=Workbook()
        self.addCleanup(book.close)
        book.active.append(['姓名','电话','品牌','车型','城市','是否售卖'])
        for index,name in enumerate(('先生','女士')):
            book.active.append([name,f'1990000750{index}','宝马','宝马 X5','上海市','否'])
        content=io.BytesIO()
        book.save(content)
        preview=self.platform.import_preview(ADMIN,'title-only.xlsx',content.getvalue(),supplier_id='SOURCE_A')
        self.assertEqual((preview['valid'],preview['errors']),(2,0))
        result=self.platform.import_commit(ADMIN,preview['id'],'ALL_VALID')
        ids=[row['lead_id'] for row in result['items']]
        listing=self.platform.list(ADMIN,'leads',page_size=100)
        displayed={row['id']:row['customer'] for row in listing['items'] if row['id'] in ids}
        for lead_id,expected in zip(ids,('先生','女士')):
            self.assertEqual(displayed[lead_id],expected)
            self.assertEqual(self.platform.detail(ADMIN,'leads',lead_id)['customer'],expected)
            conn=self.platform.connect()
            try:
                cipher=conn.execute('SELECT name_cipher FROM leads WHERE id=?',(lead_id,)).fetchone()[0]
                self.assertEqual(self.platform.vault.decrypt(cipher),expected)
            finally:
                conn.close()
        with self.platform.tx() as conn:
            conn.execute('INSERT INTO lead_field_permissions VALUES(?,?)',('u_operator',canonical(['lead_no'])))
        restricted=self.platform.detail({'id':'u_operator','org_id':'demo','role':'operator'},'leads',ids[0])
        self.assertNotIn('customer',restricted)
        self.assertNotIn('name',restricted)

    def test_acquisition_channel_imports_in_both_formats(self):
        from openpyxl import Workbook
        headers=['姓名','电话','品牌','车型','城市','获客渠道','是否售卖','售卖媒体','项目','售卖时间','性别']
        for index,extension in enumerate(('csv','xlsx')):
            values=['渠道测试',f'1390000899{index}','宝马','宝马 X5','上海市','自定义投放渠道','否','','','','未登记']
            if extension=='csv':
                stream=io.StringIO()
                writer=csv.writer(stream)
                writer.writerows((headers,values))
                content=stream.getvalue().encode()
            else:
                book=Workbook()
                book.active.append(headers)
                book.active.append(values)
                stream=io.BytesIO()
                book.save(stream)
                content=stream.getvalue()
            preview=self.platform.import_preview(ADMIN,'channel.'+extension,content,supplier_id='SOURCE_A')
            self.assertEqual((preview['valid'],preview['errors']),(1,0))
            result=self.platform.import_commit(ADMIN,preview['id'],'ALL_VALID')
            detail=self.platform.detail(ADMIN,'leads',result['items'][0]['lead_id'])
            self.assertEqual(detail['acquisition_channel'],'自定义投放渠道')

    def test_sale_filters_pair_media_with_its_project(self):
        sold=dict(self.row,source_record_id='filter-sale-1',is_sold='是',sale_media_id='MEDIA_A',
                  sale_project='KA',sold_at=now(),acquisition_channel='渠道筛选测试')
        lead_id=self.platform.create_lead(ADMIN,sold)['lead_id']
        self.platform.create_lead(ADMIN,dict(sold,source_record_id='filter-sale-2',sale_media_id='MEDIA_B',sale_project='会员'))
        filtered=self.platform.list(ADMIN,'leads',lead_filters={'sold_media':'MEDIA_A','project':'KA'})
        self.assertIn(lead_id,[row['id'] for row in filtered['items']])
        self.assertEqual(filtered['items'][0]['acquisition_channel'],'渠道筛选测试')
        wrong=self.platform.list(ADMIN,'leads',lead_filters={'sold_media':'MEDIA_A','project':'MEMBER'})
        self.assertNotIn(lead_id,[row['id'] for row in wrong['items']])
        self.assertTrue(any(row['value']=='MEDIA_A' for row in filtered['filter_options']['sold_media']))
        with self.platform.tx() as c:
            c.execute('INSERT INTO lead_field_permissions VALUES(?,?)',('u_operator',canonical(['lead_no'])))
        actor={'id':'u_operator','org_id':'demo','role':'operator'}
        restricted=self.platform.list(actor,'leads',lead_filters={'sold_media':'MEDIA_A','project':'KA'})
        self.assertNotIn('sold_media',restricted['filter_options'])
        self.assertNotIn('project',restricted['filter_options'])

    def test_new_dispatch_records_project(self):
        lead_id=self.create()['lead_id']
        c=self.platform.connect()
        try:
            demand=dict(c.execute("SELECT * FROM demands WHERE demand_no='AUTOHOME-0098'").fetchone())
        finally:c.close()
        with self.assertRaises(Fault):
            self.platform.dispatch(ADMIN,[(lead_id,demand['id'])],'missing-project',projects={})
        result=self.platform.dispatch(ADMIN,[(lead_id,demand['id'])],'with-project',projects={demand['id']:'KA'})
        self.assertEqual(result['created'],1)
        self.platform.worker(ADMIN)
        detail=self.platform.detail(ADMIN,'leads',lead_id)
        self.assertEqual((detail['is_sold'],detail['project']),('是','KA'))
        filtered=self.platform.list(ADMIN,'leads',lead_filters={'sold_media':'MEDIA_B','project':'KA'})
        self.assertIn(lead_id,[row['id'] for row in filtered['items']])

if __name__=='__main__':
    unittest.main()
