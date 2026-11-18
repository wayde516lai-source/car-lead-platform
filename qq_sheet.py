"""Read anonymous Tencent Docs sheets without cookies or remote writes.

The public page embeds its opendoc URL. Values use the page's protobuf cell
format: numbers 0..128 are inline, larger references index the numeric pool.
Unknown formats fail explicitly rather than silently importing incorrect data.
"""
import base64
import hashlib
import html
import json
import math
import re
import struct
import time
import zlib
from datetime import datetime,timedelta
from urllib.parse import parse_qs,urlencode,urlsplit,urlunsplit,urljoin
from urllib.request import HTTPRedirectHandler,Request,build_opener

class QQSheetError(Exception):pass
class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):return None

def sheet_url(url):
 p=urlsplit(str(url).strip())
 if p.scheme!='https' or p.netloc!='docs.qq.com' or not re.fullmatch(r'/sheet/[A-Za-z0-9_-]{6,100}',p.path):
  raise QQSheetError('请填写 https://docs.qq.com/sheet/ 开头的腾讯表格链接')
 qs=parse_qs(p.query);tab=qs.get('tab',[''])[0]
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',tab):raise QQSheetError('链接必须包含工作表 tab 参数')
 return urlunsplit(('https','docs.qq.com',p.path,urlencode({'tab':tab}),'')),p.path.rsplit('/',1)[-1],tab

def get_bytes(url,referer):
 p=urlsplit(url)
 if p.scheme!='https' or p.netloc!='docs.qq.com' or not (p.path.startswith('/sheet/') or p.path=='/dop-api/opendoc'):
  raise QQSheetError('文档返回了不支持的数据地址')
 try:
  with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':referer}),timeout=20) as response:
   body=response.read(10_000_001)
 except Exception:raise QQSheetError('腾讯文档读取失败，请检查分享权限或稍后重试') from None
 if len(body)>10_000_000:raise QQSheetError('文档响应过大，停止同步')
 return body

def _varint(data,pos):
 value=0
 for shift in range(0,70,7):
  if pos>=len(data):raise QQSheetError('文档二进制数据不完整')
  b=data[pos];pos+=1;value|=(b&127)<<shift
  if b<128:return value,pos
 raise QQSheetError('文档整数编码异常')
def fields(data):
 result=[];pos=0
 while pos<len(data):
  key,pos=_varint(data,pos);number,wire=key>>3,key&7
  if not 0<number<100000:raise QQSheetError('文档字段编码异常')
  if wire==0:value,pos=_varint(data,pos)
  elif wire in (1,5):
   size=8 if wire==1 else 4;value=data[pos:pos+size];pos+=size
  elif wire==2:
   size,pos=_varint(data,pos);value=data[pos:pos+size];pos+=size
  else:raise QQSheetError('文档编码版本暂不支持')
  if pos>len(data):raise QQSheetError('文档二进制数据被截断')
  result.append((number,wire,value))
 return result

def one(data,key,default=None):return next((v for n,w,v in fields(data) if n==key),default)
def many(data,key):return [v for n,w,v in fields(data) if n==key]
def unpack(value):
 try:
  compressed=base64.b64decode(value,validate=True);decoder=zlib.decompressobj();raw=decoder.decompress(compressed,20_000_001)
  if len(raw)>20_000_000 or decoder.unconsumed_tail or not decoder.eof:raise ValueError()
  return raw
 except Exception:raise QQSheetError('文档压缩数据损坏或超出大小限制') from None

def decode_blocks(payload,tab):
 try:
  cv=payload['clientVars'];priv=cv.get('privilegeAttribute',{})
  if priv.get('can_read')!=1:raise QQSheetError('腾讯文档未开放读取权限，请调整分享权限')
  collab=cv['collab_client_vars']
  if collab.get('padSubId')!=tab:raise QQSheetError('腾讯文档返回的工作表与链接不一致')
  texts=collab['initialAttributedText']['text'];grid={};coverage=[]
  for text in texts:
   for block in text.get('block_datas',[]):
    coverage.append(int(block['end_row_index']))
    raw=unpack(block['related_sheet']);envelope=one(raw,1,b'')
    for command in many(envelope,5):
     if one(command,1)!=18:continue
     sheet=one(command,19,b'');header=one(sheet,3,b'')
     if one(header,1,b'').decode()!=tab:continue
     pool=one(sheet,5,b'');strings=[one(v,1,b'').decode('utf-8') for v in many(pool,1)]
     numbers=[struct.unpack('<d',one(v,1,b''))[0] for v in many(pool,3)]
     for cell in many(sheet,6):
      row,col=one(cell,1,0),one(cell,2,0)
      if not 0<=row<20000 or not 0<=col<256:raise QQSheetError('表格超过支持的行列范围')
      value=one(cell,3,b'');typ=one(value,1,0);ref=one(one(value,2,b''),1,0)
      if typ==0:item=''
      elif typ==4:item=strings[ref]
      elif typ==2:item=ref if ref<=128 else numbers[ref-129]
      elif typ==1 and ref in (0,1):item=bool(ref)
      else:raise QQSheetError('文档包含暂不支持的单元格类型，请改用普通文本或数字')
      if isinstance(item,float) and not math.isfinite(item):raise QQSheetError('文档包含非法数值')
      grid[row,col]=item
  if not coverage or not grid:raise QQSheetError('腾讯文档没有返回可读取的表格数据')
  return grid,int(collab.get('maxRow',0)),str(cv.get('initialTitle') or payload.get('bodyData',{}).get('initialTitle') or ''),coverage
 except QQSheetError:raise
 except Exception:raise QQSheetError('腾讯文档表格结构发生变化，已停止同步以保护已有数据') from None

def parse_payload(body):
 try:
  text=body.decode('utf-8');match=re.fullmatch(r'\s*clientVarsCallback\((.*)\)\s*;?\s*',text,re.S)
  return json.loads(match.group(1) if match else text)
 except Exception:raise QQSheetError('腾讯文档返回内容无法解析，可能需要登录或分享权限已变化') from None

def scalar(value):
 if isinstance(value,float) and value.is_integer():return str(int(value))
 return str(value).strip() if value is not None else ''

def normalize_grid(grid,title,url,tab,duplicate_rules=None):
 header=sorted((col,scalar(value)) for (row,col),value in grid.items() if row==0 and scalar(value))
 if not header or '需求编号' not in [v for _,v in header]:raise QQSheetError('首行缺少“需求编号”表头，已保留上次同步数据')
 names=[name for _,name in header]
 if len(set(names))!=len(names):raise QQSheetError('表头存在重复字段，请使用唯一字段名称')
 records=[];seen={};superseded=[];duplicate_rules=duplicate_rules or {};date_fields={'下单日期','开始时间','结束时间','结束时间（渠道编辑）'}
 for row in sorted({r for r,c in grid if r>0}):
  raw={name:grid.get((row,col),'') for col,name in header}
  if not any(scalar(v) for v in raw.values()):continue
  key=scalar(raw.get('需求编号'))
  if not key:raise QQSheetError(f'第{row+1}行缺少需求编号，本次同步未更新已有项目')
  if key in seen and duplicate_rules.get(key)!='LAST_ROW_WINS':raise QQSheetError(f'需求编号{key}在第{records[seen[key]]["row_number"]}行和第{row+1}行重复，本次未更新已有需求；请确认原表是否重复或属于同一需求的新批次')
  values={}
  for name,value in raw.items():
   if name in date_fields and isinstance(value,(int,float)) and not isinstance(value,bool):
    if not 1<=value<100000:raise QQSheetError(f'第{row+1}行日期数值异常')
    date=datetime(1899,12,30)+timedelta(days=value);value=date.isoformat(sep=' ',timespec='minutes') if date.time().isoformat()!='00:00:00' else date.date().isoformat()
   values[name]=scalar(value)
  record={'key':key,'row_number':row+1,'values':values,'raw_values':raw}
  if key in seen:
   superseded.append(records[seen[key]]);records[seen[key]]=record
  else:seen[key]=len(records);records.append(record)
 if not records:raise QQSheetError('没有识别到项目数据，本次同步未清空已有项目')
 return {'title':title,'url':url,'tab':tab,'headers':names,'records':records,'count':len(records),'superseded_records':superseded}

def resolve_tab(payload,requested):
 cv=payload.get('clientVars',{});collab=cv.get('collab_client_vars',{});actual=collab.get('padSubId')
 if actual==requested:return requested,''
 if cv.get('privilegeAttribute',{}).get('can_read')!=1:raise QQSheetError('腾讯文档未开放读取权限，请调整分享权限')
 sheets=[]
 for entry in collab.get('header',[]):
  if isinstance(entry,dict) and entry.get('type')=='ms':sheets.extend(v for v in entry.get('d',[]) if isinstance(v,dict) and v.get('type')=='grid')
 visible=[v for v in sheets if not v.get('hidden')]
 if any(v.get('id')==requested for v in sheets) or len(visible)!=1 or visible[0].get('id')!=actual or not isinstance(actual,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',actual):
  raise QQSheetError('腾讯文档返回的工作表与链接不一致；请确认当前工作表链接，已有需求未更改')
 return actual,'原链接工作表编号已失效，已定位同一文档唯一可见工作表：'+str(visible[0].get('name') or actual)

def read_qq_sheet(url,getter=get_bytes,duplicate_rules=None):
 url,doc,tab=sheet_url(url);started=time.monotonic();page=getter(url,url).decode('utf-8')
 scripts=[html.unescape(src) for src in re.findall(r'<script[^>]*src=[\"\']([^\"\']+)',page,re.I) if '/dop-api/opendoc?' in src]
 if not scripts:raise QQSheetError('未找到公开表格数据，请确认该链接允许访问且不是登录页')
 # The bootstrap URL may refer to the document's current default sheet even
 # when the share URL explicitly selects another tab. Pin the requested tab;
 # still verify both the document ID here and the returned padSubId below.
 selected=None
 for candidate in scripts:
  candidate=urljoin(url,candidate);parts=urlsplit(candidate);query=parse_qs(parts.query)
  if parts.scheme=='https' and parts.netloc=='docs.qq.com' and parts.path=='/dop-api/opendoc' and query.get('id')==[doc]:
   selected=(parts,query);break
 if selected is None:raise QQSheetError('文档数据地址与分享链接不一致')
 p,qs=selected;qs['tab']=[tab]
 api=urlunsplit((p.scheme,p.netloc,p.path,urlencode(qs,doseq=True),''))
 payload=parse_payload(getter(api,url));requested_tab=tab;tab,notice=resolve_tab(payload,tab);grid,total,title,coverage=decode_blocks(payload,tab)
 qs['tab']=[tab]
 if not 1<=total<=20000:raise QQSheetError('文档行数异常或超过20000行限制')
 last=max(coverage)
 while last<total-1:
  if time.monotonic()-started>120:raise QQSheetError('读取完整表格超时，已有项目保持不变')
  start=last+1;params={k:v[-1] for k,v in qs.items()};params.update(startrow=str(start),endrow=str(min(start+255,total-1)),block_start_row=str(start),block_end_row=str(min(start+255,total-1)))
  next_url=urlunsplit((p.scheme,p.netloc,p.path,urlencode(params),''));other,other_total,other_title,ranges=decode_blocks(parse_payload(getter(next_url,url)),tab)
  if other_total!=total or other_title!=title or max(ranges)<=last:raise QQSheetError('读取过程中表格发生变化或分页不完整，请重试')
  for key,value in other.items():
   if key in grid and grid[key]!=value:raise QQSheetError('读取过程中表格内容变化，请重试')
   grid[key]=value
  last=max(ranges)
 result=normalize_grid(grid,title,url,tab,duplicate_rules)
 if notice:
  names=set(result['headers'])
  if not {'需求编号','品牌','车型','下单量'}<=names or not names&{'城市&地区','城市'}:raise QQSheetError('唯一工作表字段与需求表不符，已保留原需求；请重新选择链接')
  result.update(requested_tab=requested_tab,notice=notice,url=sheet_url('https://docs.qq.com/sheet/'+doc+'?'+urlencode({'tab':tab}))[0])
 result['remote_rows']=total;return result
