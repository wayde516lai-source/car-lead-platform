import io,zipfile,unittest
from excel_intake import read_xml_values,ExcelIntakeError

def workbook(sheet='名单',style=''):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w') as z:
  z.writestr('xl/workbook.xml','<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="'+sheet+'" r:id="rId1"/></sheets></workbook>')
  z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
  z.writestr('xl/worksheets/sheet1.xml','<worksheet><sheetData><row><c r="A1" t="inlineStr"><is><t>姓名</t></is></c><c r="B1" t="inlineStr"><is><t>电话</t></is></c></row><row><c r="A2" t="inlineStr"><is><t>测试</t></is></c><c r="B2"><v>13900008888.0</v></c><c r="C2"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>')
  z.writestr('xl/styles.xml',style or 'malformed style')
 return out.getvalue()
class ExcelIntakeTests(unittest.TestCase):
 def test_reads_values_without_styles_or_optional_metadata(self):self.assertEqual(read_xml_values(workbook())[1],['测试','13900008888','=FORMULA_REJECTED'])
 def test_csv_content_named_xlsx_is_detected(self):
  rows=read_xml_values(('\ufeff姓名,电话,品牌,车型,城市,是否售卖\n测试,13900008888,宝马,宝马 X5,上海市,否\n').encode('utf-8'))
  self.assertEqual(rows[0][0],'姓名');self.assertEqual(rows[1][1],'13900008888');self.assertEqual(rows[1][-1],'否')
 def test_missing_sheet_has_explicit_message(self):
  with self.assertRaises(ExcelIntakeError) as cm:read_xml_values(workbook(),'Sheet1')
  self.assertEqual(cm.exception.code,'EXCEL_SHEET_NOT_FOUND');self.assertIn('名单',cm.exception.message)
 def test_old_or_encrypted_file_and_invalid_container(self):
  for value,code in [(bytes.fromhex('d0cf11e0a1b11ae1'),'EXCEL_PROTECTED_OR_XLS'),(b'not an xlsx','EXCEL_FORMAT_INVALID')]:
   with self.assertRaises(ExcelIntakeError) as cm:read_xml_values(value)
   self.assertEqual(cm.exception.code,code)
