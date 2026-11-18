"""Yiche saveClue protocol. Transport is only used for explicitly enabled direct sends."""
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yiche

SAVE_URL = 'https://mapi.yiche.com/open_api/api/v1/openapi/clue/saveClue'
CALLBACK_PATH = '/open/v1/yiche/callback'
# 易车《会员线索对接文档0704》回调 code 码枚举。
CALLBACK_CODES = {
    '2001': ('VALID', '有效线索'),
    '-14': ('INVALID', '重复或无效线索'),
    '1': ('INVALID', '重复或无效线索'),
    **dict.fromkeys(('-8', '-17', '-10', '-9'), ('INVALID', '无效用户')),
    '-19': ('INVALID', '无经销商售卖'),
    '-12': ('INVALID', '姓名无效'),
    '-16': ('INVALID', '无效手机号'),
    '-23': ('INVALID', '订单车型或车款从属关系错误'),
    '-24': ('INVALID', '订单无车型无车款'),
    '-7': ('INVALID', 'IP 为空或黑名单限制'),
    '1000': ('INVALID', '主车型需求溢出'),
    '1001': ('INVALID', '副车型需求溢出'),
    '1002': ('INVALID', '无效手机号'),
    '1003': ('INVALID', '重复订单'),
    '1004': ('INVALID', '质检不合格'),
    '1005': ('INVALID', '不单独接收副车型'),
    '1006': ('INVALID', '无经销商售卖'),
    '1007': ('INVALID', '主车型黑名单'),
    '1008': ('INVALID', '副车型黑名单'),
    '2000': ('INVALID', '其他原因'),
}


def callback_codes(codes=CALLBACK_CODES):
    return [{'code': code, 'status': status, 'description': description}
            for code, (status, description) in codes.items()]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Config:
    cid: str
    signing_key: str = field(repr=False)
    aes_key: bytes = field(repr=False)
    callback_url: str
    uid: str = ''


def from_environment():
    if os.environ.get('YICHE_ENABLE_LIVE_PUSH') != '1':
        return None
    cid = os.environ.get('YICHE_CID', '')
    key = os.environ.get('YICHE_SIGN_KEY', '')
    aes = os.environ.get('YICHE_AES_KEY', '').encode('utf-8')
    callback = os.environ.get('YICHE_CALLBACK_URL', '')
    parts = urlsplit(callback)
    if not re.fullmatch(r'[0-9]{1,20}', cid) or not key.strip() or not 0 < len(aes) <= 16:
        raise ValueError('启用易车推送需要配置 CID、签名密钥和 AES 密钥')
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path != CALLBACK_PATH):
        raise ValueError('易车回调地址必须是公开的 HTTPS 地址，路径为 ' + CALLBACK_PATH)
    return Config(cid, key, aes, callback, os.environ.get('YICHE_UID', ''))


def prepare(config, demand, extra, customer, order_id):
    token = secrets.token_urlsafe(32)
    body = yiche.build_request(demand, extra, customer['phone'], customer['name'], order_id,
                               key=config.aes_key, callback=config.callback_url,
                               callback_token=token)
    return body, hashlib.sha256(token.encode()).hexdigest()


def signed_request(config, body, timestamp=None):
    param = json.dumps(body['param'], ensure_ascii=False, separators=(',', ':'))
    stamp = str(timestamp if timestamp is not None else time.time_ns() // 1_000_000)
    version = '1.0.0'
    fields = [('cid', config.cid), ('param', param), ('uid', config.uid), ('ver', version), ('t', stamp)]
    canonical = ';'.join(f'{k}={v}' for k, v in fields if v) + ';' + config.signing_key
    headers = {'Content-Type': 'application/json; charset=utf-8', 'cid': config.cid,
               'ver': version, 't': stamp, 'sign': hashlib.md5(canonical.encode('utf-8')).hexdigest()}
    if config.uid:
        headers['uid'] = config.uid
    return Request(SAVE_URL, ('{"param":' + param + '}').encode('utf-8'), headers, method='POST')


def send(config, body):
    request = signed_request(config, body)
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            http_status = response.status
            data = response.read(1_000_001)
    except HTTPError as error:
        error.close()
        return 'UNKNOWN', error.code, 'YICHE_HTTP_ERROR'
    except (URLError, OSError):
        return 'UNKNOWN', None, 'YICHE_TRANSPORT_ERROR'
    if len(data) > 1_000_000 or not 200 <= http_status < 300:
        return 'UNKNOWN', http_status, 'YICHE_RESPONSE_INVALID'
    try:
        reply = json.loads(data)
    except (ValueError, UnicodeError):
        return 'UNKNOWN', http_status, 'YICHE_RESPONSE_INVALID'
    status = reply.get('status') if isinstance(reply, dict) else None
    if type(status) not in (str, int) or not re.fullmatch(r'-?[0-9]{1,12}', str(status)):
        return 'UNKNOWN', http_status, 'YICHE_RESPONSE_INVALID'
    if str(status) == '1':
        return 'ACKED', http_status, ''
    code = str(reply.get('ercd') or status)
    code = code if re.fullmatch(r'[A-Za-z0-9_-]{1,40}', code) else 'UNKNOWN'
    return 'FAILED', http_status, 'YICHE_REJECTED_' + code


def callback_result(result, code, codes=CALLBACK_CODES):
    if result not in ('0', '1') or (code and not re.fullmatch(r'-?[0-9]{1,12}', code)):
        raise ValueError('易车回调结果格式错误')
    if code:
        return codes.get(code, ('CONFLICT', ''))[0]
    return 'VALID' if result == '0' else 'INVALID'
