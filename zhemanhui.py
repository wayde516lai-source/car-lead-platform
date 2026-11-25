"""喆满汇/软米会员接口协议；不复用易车直连接口状态码。"""
import ipaddress
import json
import re
from dataclasses import dataclass,field
from urllib.parse import urlencode,urlsplit,parse_qs
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError

TEST_URL='http://yiche.asoapple.cn/api-test/v1/saveClue'
PRODUCTION_URL='http://yiche.asoapple.cn/api/v1/saveClue'
CALLBACK_PATH='/open/v1/zhemanhui/callback'

@dataclass(frozen=True)
class Config:
    callback_url:str
    bearer_token:str=field(default='',repr=False)
    supid:str='6'
    environment:str='TEST'

class ProtocolError(ValueError):pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def prepare(config,*,demand_no,city_id,series_id,name,mobile,delivery_id,callback_token,plat='安卓',request_ip='127.0.0.1'):
    """All IDs must come from an explicit counterpart mapping, never names or guesses."""
    p=urlsplit(config.callback_url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment or p.query or p.path!=CALLBACK_PATH:
        raise ProtocolError('请配置专用HTTPS回调地址')
    if config.supid!='6':raise ProtocolError('文档明确要求渠道号supid=6')
    def required(value,label):
        v=str(value).strip()
        if not v or len(v)>128 or any(ord(c)<32 for c in v):raise ProtocolError(label+'缺失或无效')
        return v
    demand_no=required(demand_no,'需求编号');name=required(name,'姓名')
    mobile=str(mobile).strip()
    if mobile.startswith('+86'):mobile=mobile[3:]
    if not re.fullmatch(r'1[3-9][0-9]{9}',mobile):raise ProtocolError('电话必须是11位手机号')
    for value,label in [(city_id,'乙方城市ID'),(series_id,'乙方车系ID')]:
        if not re.fullmatch(r'[1-9][0-9]*',str(value)):raise ProtocolError(label+'必须来自已确认的字典映射')
    if plat not in ('安卓','IOS'):raise ProtocolError('机型须为安卓或IOS')
    try:ipaddress.ip_address(request_ip)
    except ValueError:raise ProtocolError('请求IP无效') from None
    delivery_id=required(delivery_id,'分发编号')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',callback_token):raise ProtocolError('回调令牌无效')
    callback=config.callback_url+'?'+urlencode({'delivery_id':delivery_id,'callback_token':callback_token})
    fields={'supid':'6','ptitle':demand_no,'cityId':str(city_id),'serialId':str(series_id),'name':name,'mobile':mobile,'plat':plat,'requestIp':request_ip,'sourceUrl':callback}
    headers={'Accept':'application/json','Content-Type':'application/json'}
    if config.bearer_token:headers['Authorization']='Bearer '+config.bearer_token
    if config.environment not in ('TEST','PRODUCTION'):raise ProtocolError('接口环境无效')
    endpoint=PRODUCTION_URL if config.environment=='PRODUCTION' else TEST_URL
    return Request(endpoint+'?'+urlencode(fields),headers=headers,method='GET')

def submission_result(http_status,body):
    """Only supplier acceptance is established, never final media validity."""
    try:
        value=json.loads(body)
        if not isinstance(value,dict):raise ValueError()
    except (ValueError,TypeError):return {'status':'UNKNOWN','error':'RESPONSE_INVALID'}
    code=str(value.get('errorCode',''))
    if 200<=http_status<300 and code=='1':return {'status':'ACKED','supplier_storage':'STORED','validity':'PENDING','code':code}
    if code=='-1':return {'status':'FAILED','error':'SUPPLIER_UNAVAILABLE','code':code}
    return {'status':'UNKNOWN','error':'UNCONFIRMED_RESPONSE','code':code}

def send_test(request,*,enabled=False,opener=None):
    """Opt-in test endpoint only; no redirects/retries (idempotency unspecified)."""
    return send_request(request,environment='TEST',enabled=enabled,opener=opener)

def send_request(request,*,environment,enabled=False,opener=None):
    """按明确环境仅向固定地址发送一次，不跟随重定向或自动重试。"""
    if not enabled:raise ProtocolError('乙方推送未启用')
    expected={'TEST':TEST_URL,'PRODUCTION':PRODUCTION_URL}.get(environment)
    p=urlsplit(request.full_url)
    if not expected or p.fragment or request.get_method()!='GET' or request.full_url.split('?',1)[0]!=expected:raise ProtocolError('接口地址与发送环境不一致')
    opener=opener or build_opener(NoRedirect())
    try:
        with opener.open(request,timeout=15) as response:
            body=response.read(1048577)
            if len(body)>1048576:return {'status':'UNKNOWN','error':'RESPONSE_TOO_LARGE'}
            return submission_result(response.status,body)
    except HTTPError as e:return submission_result(e.code,e.read(1048576))
    except (OSError,TimeoutError):return {'status':'UNKNOWN','error':'TRANSPORT_UNKNOWN'}

def callback_fields(query):
    """Do not guess resultCode/code meanings; retain unknown outcomes for review."""
    values=parse_qs(query,keep_blank_values=True,max_num_fields=12)
    required={'delivery_id','callback_token','resultCode','code'}
    if set(values)!=required or any(len(v)!=1 or not v[0] or len(v[0])>128 for v in values.values()):raise ProtocolError('回调参数缺失或重复')
    return {k:v[0] for k,v in values.items()}

def dictionary_rows(payload,kind):
    """Parser only: actual dictionary refresh uses a separately authorized transport."""
    if kind not in ('cities','cars'):raise ProtocolError('字典类型无效')
    if not isinstance(payload,dict) or str(payload.get('errorCode'))!='1':raise ProtocolError('字典返回失败')
    message=payload.get('message');rows=message.get('data') if isinstance(message,dict) else None
    if not isinstance(rows,list) or not rows:raise ProtocolError('字典数据为空或格式异常')
    keys={'cityCode','cityName'} if kind=='cities' else {'carId','seriesId','seriesName','brandId','brandName'}
    if any(not isinstance(row,dict) or not keys<=row.keys() for row in rows):raise ProtocolError('字典字段缺失')
    return rows
