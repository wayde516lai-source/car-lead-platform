"""Read tabular XLSX values independently of optional workbook formatting."""
import csv,io,posixpath,re,zipfile
from xml.etree import ElementTree as ET
class ExcelIntakeError(Exception):
 def __init__(self,code,message):self.code=code;self.message=message

def read_xml_values(content,sheet=None):
 if content.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):raise ExcelIntakeError('EXCEL_PROTECTED_OR_XLS','该文件是旧版.xls或加密Excel，请取消文件密码并另存为.xlsx，不能只修改扩展名')
 if not zipfile.is_zipfile(io.BytesIO(content)):
  try:
   text=content.decode('utf-8-sig')
   if '\x00' in text:raise ValueError()
   dialect=csv.Sniffer().sniff(text[:8192],delimiters=',\t;')
   rows=list(csv.reader(io.StringIO(text),dialect,strict=True));headers=set(rows[0]) if rows else set()
   if not headers.intersection({'手机号','电话'}) or not headers.intersection({'姓名','客户姓名'}):raise ValueError()
   if len(rows)>50001:raise ExcelIntakeError('TOO_MANY_ROWS','最多50000行数据')
   return rows
  except ExcelIntakeError:raise
  except Exception:raise ExcelIntakeError('EXCEL_FORMAT_INVALID','文件内容不是有效的.xlsx，请用Excel或WPS另存为.xlsx，或导出UTF-8 CSV') from None
 try:z=zipfile.ZipFile(io.BytesIO(content))
 except zipfile.BadZipFile:raise ExcelIntakeError('EXCEL_FORMAT_INVALID','文件内容不是有效的.xlsx，请用Excel或WPS打开后另存为.xlsx，或导出UTF-8 CSV') from None
 with z:
  if sum(i.file_size for i in z.infolist())>50*1024*1024:raise ExcelIntakeError('FILE_TOO_LARGE','表格解压后不得超过50MB')
  names=set(z.namelist())
  if any(i.flag_bits&1 for i in z.infolist()):raise ExcelIntakeError('EXCEL_PROTECTED','请取消Excel文件密码后重新上传')
  if 'xl/workbook.xml' not in names:raise ExcelIntakeError('EXCEL_STRUCTURE_INVALID','文件缺少工作簿结构，请重新另存为.xlsx')
  def parse(name):
   try:return ET.fromstring(z.read(name))
   except Exception:raise ExcelIntakeError('EXCEL_XML_INVALID','Excel内部数据不完整或损坏，请重新另存为.xlsx') from None
  def local(tag):return tag.rsplit('}',1)[-1]
  workbook=parse('xl/workbook.xml');sheets=[e for e in workbook.iter() if local(e.tag)=='sheet']
  if not sheets:raise ExcelIntakeError('EXCEL_NO_SHEET','工作簿中没有可读取的工作表')
  target=next((s for s in sheets if s.get('name')==sheet),None) if sheet else sheets[0]
  if target is None:raise ExcelIntakeError('EXCEL_SHEET_NOT_FOUND','工作表名称不存在，可用工作表：'+'、'.join(s.get('name','') for s in sheets))
  relid=next((v for k,v in target.attrib.items() if local(k)=='id'),None);rels=parse('xl/_rels/workbook.xml.rels');rel=next((r for r in rels if r.get('Id')==relid),None)
  if rel is None or rel.get('TargetMode')=='External':raise ExcelIntakeError('EXCEL_STRUCTURE_INVALID','工作表引用无效，请重新另存为.xlsx')
  path=rel.get('Target','');path=posixpath.normpath(path.lstrip('/') if path.startswith('/') else posixpath.join('xl',path))
  if not path.startswith('xl/') or path not in names:raise ExcelIntakeError('EXCEL_STRUCTURE_INVALID','工作表数据缺失，请重新另存为.xlsx')
  strings=[]
  if 'xl/sharedStrings.xml' in names:
   for item in parse('xl/sharedStrings.xml'):
    strings.append(''.join(t.text or '' for t in item.iter() if local(t.tag)=='t'))
  rows=[]
  for row in parse(path).iter():
   if local(row.tag)!='row':continue
   values={};next_col=0
   for cell in row:
    if local(cell.tag)!='c':continue
    ref=re.match(r'([A-Z]+)',cell.get('r',''));col=0
    if ref:
     for letter in ref[1]:col=col*26+ord(letter)-64
     col-=1
    else:col=next_col
    if col>=16384:raise ExcelIntakeError('EXCEL_STRUCTURE_INVALID','Excel列位置异常')
    next_col=col+1;children={local(e.tag):e for e in cell};typ=cell.get('t','');v=children.get('v');value=v.text or '' if v is not None else ''
    if 'f' in children:value='=FORMULA_REJECTED'
    elif typ=='s':
     try:value=strings[int(value)]
     except Exception:raise ExcelIntakeError('EXCEL_XML_INVALID','Excel文本索引损坏，请重新另存为.xlsx') from None
    elif typ=='inlineStr':value=''.join(t.text or '' for t in cell.iter() if local(t.tag)=='t')
    elif typ=='b':value='是' if value=='1' else '否'
    elif typ in ('','n') and re.fullmatch(r'[0-9]+\.0+',value):value=value.split('.')[0]
    values[col]=value
   if not any(str(v).strip() for v in values.values()):continue
   rows.append([values.get(i,'') for i in range(max(values,default=-1)+1)])
   if len(rows)>50001:raise ExcelIntakeError('TOO_MANY_ROWS','最多50000行数据')
  return rows
