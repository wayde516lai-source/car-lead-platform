"""KA需求拉取响应的纯解析边界。

依据用户提供的pullKaCrid文档转换多城市、多品牌车型集合，保留双方字典ID。
纯解析函数不执行网络和写库，受控transport只读取固定需求接口。
用户确认全量返回、只有状态0、月度量仅展示、每日量限制当天提交。
"""
import json
from datetime import datetime


class DemandProtocolError(ValueError):
    """外部响应不符合已确认结构；消息不包含原始响应或凭证。"""


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise DemandProtocolError(label + '缺失或无效')
    return value.strip()


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise DemandProtocolError(label + '必须是非负整数')
    return value


def _identifier(value, label):
    if type(value) is int and value >= 0:
        return str(value)
    return _text(value, label)


def _collection(value, label):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            raise DemandProtocolError(label + 'JSON格式无效') from None
    if not isinstance(value, list) or not value or len(value) > 10000:
        raise DemandProtocolError(label + '须为非空集合，且不超过10000项')
    if any(not isinstance(item, dict) for item in value):
        raise DemandProtocolError(label + '集合项格式无效')
    return value


def _timestamp(value, label):
    text = _text(value, label)
    try:
        result = datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        raise DemandProtocolError(label + '须为YYYY-MM-DD HH:MM:SS') from None
    # 文档无时区字段；这里保留原值，正式入库前仍需确认对方业务时区。
    return text, result


def parse_response(payload):
    """校验整批响应并返回标准字段；任何坏行使整批失败，避免部分覆盖。

    原需求编号保持字符串，城市/车型按ID去重；车型保留品牌及厂商归属，
    不把多品牌的所有车型与所有品牌交叉组合。空结果不表示旧需求删除。
    """
    if not isinstance(payload, dict) or str(payload.get('errorCode')) != '1':
        raise DemandProtocolError('KA需求接口未返回已知成功码')
    message = payload.get('message')
    rows = message.get('data') if isinstance(message, dict) else None
    if not isinstance(rows, list) or len(rows) > 5000:
        raise DemandProtocolError('KA需求数据须为数组，且不超过5000条')
    records, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise DemandProtocolError('KA需求行格式无效')
        demand_no = _text(row.get('crid'), '需求编号crid')
        if demand_no in seen:
            raise DemandProtocolError('同批KA需求编号重复，未覆盖已有数据')
        seen.add(demand_no)
        begin_text, begin = _timestamp(row.get('BeginTime'), '开始时间')
        end_text, end = _timestamp(row.get('EndTime'), '结束时间')
        updated_text, _ = _timestamp(row.get('UpdateTime'), '更新时间')
        if end < begin:
            raise DemandProtocolError('结束时间早于开始时间')
        day = str(_integer(row.get('day'), '需求日期day'))
        try:
            datetime.strptime(day, '%Y%m%d')
        except ValueError:
            raise DemandProtocolError('需求日期day格式无效') from None
        if len(day) != 8:
            raise DemandProtocolError('需求日期day格式无效')
        cities, vehicles = {}, {}
        for province in _collection(row.get('ListPro'), '城市集合'):
            for city in _collection(province.get('ListCity'), '城市列表'):
                city_id = _identifier(city.get('CityId'), '城市ID')
                name = _text(city.get('CityName'), '城市名称')
                if city_id in cities and cities[city_id]['name'] != name:
                    raise DemandProtocolError('同一城市ID对应多个名称')
                cities[city_id] = {'id': city_id, 'name': name}
        for brand in _collection(row.get('ListCmb'), '车型集合'):
            brand_id = _identifier(brand.get('CmbId'), '品牌ID')
            brand_name = _text(brand.get('CmbName'), '品牌名称')
            for make in _collection(brand.get('ListCmk'), '厂商列表'):
                make_id = _identifier(make.get('CmkId'), '厂商ID')
                make_name = _text(make.get('CmkName'), '厂商名称')
                for model in _collection(make.get('ListCmd'), '车型列表'):
                    series_id = _identifier(model.get('CmdId'), '车型ID')
                    vehicle = {'series_id': series_id, 'model': _text(model.get('CmdName'), '车型名称'),
                               'brand_id': brand_id, 'brand': brand_name,
                               'make_id': make_id, 'make': make_name}
                    if series_id in vehicles and vehicles[series_id] != vehicle:
                        raise DemandProtocolError('同一车型ID对应多个品牌或车型')
                    vehicles[series_id] = vehicle
        if row.get('status') != 0 or type(row.get('status')) is not int:
            raise DemandProtocolError('未知KA需求状态，未更新已有数据')
        records.append({'demand_no': demand_no, 'external_id': _identifier(row.get('id'), '记录ID'),
                        'monthly_limit': _integer(row.get('limit'), '月度总量'),
                        'daily_limit': _integer(row.get('LimitOfDay'), '每日量级'),
                        'cities': list(cities.values()), 'vehicles': list(vehicles.values()),
                        'activity_id': _identifier(row.get('CRDID'), '活动号'),
                        'begin_time': begin_text, 'end_time': end_text, 'updated_time': updated_text,
                        'status': _integer(row.get('status'), '需求状态'), 'day': day})
    return records


API_URL = 'http://yiche.asoapple.cn/api/v2/pullKaCrid'


def read_demands(url, auth_mode, token=''):
    """只对文档指定接口执行一次GET，无重定向、有限超时及响应大小。

    不使用文档示例凭证。Bearer只通过HTTPS传输，错误不包含请求凭证或响应原文。
    """
    from urllib.request import Request, build_opener, HTTPRedirectHandler

    if url not in (API_URL, API_URL.replace('http:', 'https:')):
        raise DemandProtocolError('需求接口地址不在允许范围内')
    if auth_mode not in ('NONE', 'BEARER'):
        raise DemandProtocolError('请先确认甲方鉴权要求，再配置无需Token或专属Token')
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    if auth_mode == 'BEARER':
        if not url.startswith('https://'):
            raise DemandProtocolError('专属Token须使用甲方确认的HTTPS地址')
        if not isinstance(token, str) or not token.strip() or any(ord(char) < 32 for char in token):
            raise DemandProtocolError('请配置有效的专属Token')
        headers['Authorization'] = 'Bearer ' + token.strip()

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=20) as response:
            body = response.read(10_000_001)
        if len(body) > 10_000_000:
            raise DemandProtocolError('需求接口响应超过10MB限制')
        payload = json.loads(body)
    except DemandProtocolError:
        raise
    except Exception:
        raise DemandProtocolError('需求接口读取失败，请检查网络、鉴权及甲方接口状态') from None
    return parse_response(payload)
