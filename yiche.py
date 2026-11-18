"""易车 saveClue fields. Local serialization only; no network client."""
import base64, ipaddress, re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DEMAND_IDS=('platFormId','clueTypeId','cityId','serialId','manufacturerId','brandId','carId','csId2')
LEAD_IDS=('cityId','serialId','manufacturerId','carId')
LEAD_TEXT=('requestIp','recordPath','ptitle','thirdDeliveryUrl','longitude','latitude','roomId','settleAccountNo','dvid')
ENCRYPTED={'mobile','name','requestIp','sourceUrl','recordPath','thirdDeliveryUrl','longitude','latitude','dvid'}
LEAD_HEADERS={'易车城市ID':'cityId','易车车系ID':'serialId','易车厂商ID':'manufacturerId','易车车款ID':'carId','请求IP':'requestIp','录音地址':'recordPath','页面标题':'ptitle','投流链接':'thirdDeliveryUrl','经度':'longitude','纬度':'latitude','直播间ID':'roomId','直播账号':'settleAccountNo','设备ID':'dvid'}
MOCK_KEY=b'LOCAL-DEMO'
MOCK_CALLBACK='https://callback.example.invalid/yiche/result'

def integer(v,k):
 if type(v) not in (int,str) or not re.fullmatch(r'[0-9]{1,10}',str(v)) or not 0<int(v)<=2147483647:raise ValueError(f'{k} 必须是正整数')
 return int(v)

def fields(raw,ids,strings):
 if not isinstance(raw,dict):raise ValueError('易车字段必须是对象')
 if set(raw)-set(ids)-set(strings):raise ValueError('存在不支持的易车字段')
 out={}
 for k,v in raw.items():
  if v is None or v=='':continue
  if k in ids:out[k]=integer(v,k)
  elif not isinstance(v,str) or len(v)>2048:raise ValueError(f'{k} 必须是文本，最长 2048 字符')
  elif v.strip():out[k]=v.strip()
 return out

def lead_fields(raw):
 out=fields(raw,LEAD_IDS,LEAD_TEXT)
 if 'requestIp' in out:
  try:ipaddress.ip_address(out['requestIp'])
  except ValueError:raise ValueError('请求 IP 格式错误') from None
 if ('longitude' in out)!=('latitude' in out):raise ValueError('经度、纬度需一起填写')
 for k,limit in [('longitude',180),('latitude',90)]:
  if k not in out:continue
  try:
   v=Decimal(out[k])
   if not v.is_finite() or abs(v)>limit:raise ValueError()
  except (InvalidOperation,ValueError):raise ValueError(f'{k} 超出有效范围') from None
 return out

def demand_fields(raw,complete=False):
 out=fields(raw,DEMAND_IDS,('plat','dealerIds','crId'))
 if out.get('clueTypeId',1) not in (1,2,83):raise ValueError('线索类型仅支持文档中的 1、2、83')
 if 'dealerIds' in out:
  out['dealerIds']=','.join(x.strip() for x in out['dealerIds'].split(','))
  for x in out['dealerIds'].split(','):integer(x,'经销商 ID')
 if complete:
  missing=[k for k in ('platFormId','plat','cityId','serialId','manufacturerId') if k not in out]
  if out.get('clueTypeId')==83 and not out.get('crId'):missing.append('crId')
  if missing:raise ValueError('缺少易车必填字段：'+'、'.join(missing))
 return out

def encrypt(value,key):
 if not 0<len(key)<=16:raise ValueError('AES 密钥长度错误')
 pad=padding.PKCS7(128).padder()
 data=pad.update(value.encode('utf-8'))+pad.finalize()
 enc=Cipher(algorithms.AES(key.ljust(16,b'\0')),modes.ECB()).encryptor()
 return base64.urlsafe_b64encode(enc.update(data)+enc.finalize()).decode('ascii')

def build_request(demand,lead,mobile,name,order_id,*,key=MOCK_KEY,callback=MOCK_CALLBACK,callback_token=None):
 d=demand_fields(demand,complete=True);l=lead_fields(lead)
 if urlsplit(callback).query or urlsplit(callback).fragment:raise ValueError('回调基础地址不能含查询参数或片段')
 for k in LEAD_IDS:
  if k in l and k in d and l[k]!=d[k]:raise ValueError(f'线索与需求的易车 {k} 不一致')
 p={k:v for k,v in d.items() if k!='brandId'}
 p.setdefault('clueTypeId',1)
 for k,v in l.items():
  if k in LEAD_TEXT or k=='carId':p[k]=v
 phone=mobile.removeprefix('+86')
 if not re.fullmatch(r'1[3-9]\d{9}',phone):raise ValueError('易车手机号格式错误')
 callback_params={'platFormId':d['platFormId'],'orderId':order_id}
 if callback_token:callback_params['callbackToken']=callback_token
 p.update(mobile=phone,name=name.strip() or '询价客户',
          requestIp=l.get('requestIp','127.0.0.1'),
          sourceUrl=callback+'?'+urlencode(callback_params))
 return {'param':{k:encrypt(v,key) if k in ENCRYPTED else v for k,v in p.items()}}
