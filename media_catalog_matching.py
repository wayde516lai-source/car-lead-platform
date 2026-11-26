"""从最终媒体字典唯一解析城市和车系，不使用乙方私有映射或其他媒体编号。

业务需求使用共享车型名称及地区规则。这里负责可发送性及上报编码，
不修改线索原始信息，也不把需求的“全系/全国”当成客户实际意向。
"""
import json

from domain import Fault
from synced_demands import city_name, model_name, norm


def requires_media_catalogs(conn, org_id: str, route: dict, *, strict=False) -> bool:
    """真实软米交付与已配置媒体字典的路径共用字典校验，旧隔离模拟保持兼容。"""
    return strict or route['adapter_version'] == 'zhemanhui-v1' or bool(conn.execute(
        'SELECT 1 FROM media_dictionary_sources WHERE org_id=? AND media_id=?',
        (org_id, route['media_id']),
    ).fetchone())


def required_vehicle_fields(adapter_version):
    """按已实现协议列出车型必填编码，软米会员只上报车系，易车直连还需厂商。"""
    if adapter_version == 'zhemanhui-v1':
        return ('series_id',)
    return ('series_id', 'factory_id')


def resolve_media_codes(conn, org_id: str, media_id: str, lead: dict, context=None, *, adapter_version=None) -> dict:
    """由车型在目标媒体库解析协议所需编码；必填编码歧义时不猜测。

    品牌和厂商取自媒体库，名称不一致不再阻止唯一车型匹配。
    同名车型的协议必填编号不唯一时拒绝创建，不再以原品牌/厂商名称筛选。
    context只缓存当前调用读取的字典，发送前另行重验。
    """
    media = conn.execute("SELECT 1 FROM partners WHERE org_id=? AND id=? AND role='MEDIA'",
                         (org_id, media_id)).fetchone()
    if not media:
        raise Fault('CATALOG_MEDIA_REQUIRED', '最终媒体不存在或不属于当前组织')
    cache = context.setdefault('media_catalogs', {}) if context is not None else {}
    names = context.setdefault('media_lead_names', {}) if context is not None else {}
    local_key = (org_id, lead['series_id'])
    if local_key not in names:
        names[local_key] = conn.execute('''SELECT brand_name,series_name FROM catalog WHERE series_id=?
            AND series_id NOT IN (SELECT series_id FROM manual_vehicle_entries WHERE org_id!=?)''',
            (lead['series_id'], org_id)).fetchone()
    local = names[local_key]
    if not local or norm(local['series_name']) in ('全系', '全车型', '不限'):
        raise Fault('MEDIA_VEHICLE_REQUIRED', '请填写客户实际意向车系，不能用全系上报')
    key = (org_id, media_id)
    if key not in cache:
        vehicles = [dict(row) for row in conn.execute('''SELECT car_id,car_name,series_id,series_name,
            factory_id,factory_name,brand_id,brand_name FROM media_vehicle_items WHERE org_id=? AND media_id=?''',
            (org_id, media_id))]
        cities = [dict(row) for row in conn.execute('SELECT * FROM media_city_items WHERE org_id=? AND media_id=?',
                                                  (org_id, media_id))]
        vehicle_index, city_index = {}, {}
        for row in vehicles:
            for model_label in (row['series_name'], row['car_name']):
                for brand_label in ('', row['brand_name'], row['factory_name']):
                    match_key = model_name(model_label, brand_label)
                    vehicle_index.setdefault(match_key, {})[row['car_id']] = row
        for row in cities:
            for label in (row['city_name'], row['parent_name'] + row['city_name']):
                city_index.setdefault(city_name(label), {})[row['city_code']] = row
        cache[key] = vehicle_index, city_index
    vehicle_index, city_index = cache[key]
    if not vehicle_index:
        raise Fault('MEDIA_VEHICLE_MISSING', '该最终媒体车型库为空，请先同步')
    if not city_index:
        raise Fault('MEDIA_CITY_MISSING', '该最终媒体城市库为空，请先同步')
    model = model_name(local['series_name'], local['brand_name'])
    candidates = list(vehicle_index.get(model, {}).values())
    if not candidates:
        raise Fault('MEDIA_VEHICLE_UNMATCHED', '客户车型在该最终媒体车型库中未找到')
    fields = required_vehicle_fields(adapter_version)
    signatures = lambda rows: {tuple(str(row[field]) for field in fields) for row in rows}
    if len(signatures(candidates)) != 1:
        raise Fault('MEDIA_VEHICLE_AMBIGUOUS', '该媒体存在同名车系，请核对厂商与车型')
    vehicle = candidates[0].copy()
    # 非必填编码有多个值时留空，不把任意车款的厂商/品牌当成确定映射。
    for field in ('factory_id', 'brand_id', 'factory_name', 'brand_name'):
        if len({str(row[field]) for row in candidates}) > 1:
            vehicle[field] = ''
    if any(not str(vehicle[field]).strip() for field in fields):
        raise Fault('MEDIA_VEHICLE_UNMATCHED', '媒体车型缺少当前接口必填编号')
    target_city = city_name(lead['city_code'])
    matched_cities = city_index.get(target_city, {})
    if not matched_cities:
        raise Fault('MEDIA_CITY_UNMATCHED', '客户城市在该最终媒体城市库中未找到，请填写城市名称')
    if len(matched_cities) != 1:
        raise Fault('MEDIA_CITY_AMBIGUOUS', '该媒体存在同名城市，请补充省份核对')
    city = next(iter(matched_cities.values()))
    return {'media_id': media_id, 'city_id': city['city_code'], 'city_name': city['city_name'],
            'series_id': vehicle['series_id'], 'series_name': vehicle['series_name'],
            'factory_id': vehicle['factory_id'], 'brand_id': vehicle['brand_id'],
            'factory_name': vehicle['factory_name'], 'brand_name': vehicle['brand_name']}


def delivery_media_codes(conn, org_id: str, delivery: dict, lead: dict, payload: dict) -> dict:
    """发送前重验字典；创建时已有编码快照则禁止静默改用新编号。"""
    snapshot = json.loads(delivery['route_snapshot'])
    media_id = snapshot['media_id']
    if media_id != delivery['media_id']:
        raise Fault('MEDIA_CATALOG_CHANGED', '交付最终媒体与快照不一致')
    resolved = resolve_media_codes(conn, org_id, media_id, lead, adapter_version=snapshot.get('adapter_version'))
    previous = payload.get('media_dictionary')
    if previous and any(str(previous.get(key)) != str(resolved[key]) for key in
            ('media_id', 'city_id') + required_vehicle_fields(snapshot.get('adapter_version'))):
        raise Fault('MEDIA_CATALOG_CHANGED', '媒体字典编号已变化，请重新预览创建分发')
    return resolved


def yiche_fields_from_catalog(config: dict, extra: dict, codes: dict) -> tuple:
    """将最终媒体解析结果填入直连协议，原需求配置及线索原编码保持不变。"""
    config, extra = config.copy(), extra.copy()
    config.update(cityId=codes['city_id'], serialId=codes['series_id'],
                  manufacturerId=codes['factory_id'], brandId=codes['brand_id'])
    # 旧线索可能带另一份字典的编码，不能再让它覆盖最终媒体解析结果。
    for key in ('cityId', 'serialId', 'manufacturerId', 'brandId', 'carId'):
        extra.pop(key, None)
    config.pop('carId', None)
    return config, extra


def register_demand_vehicle(conn, org_id: str, media_id: str, series_id: str) -> str:
    """手动需求选择媒体车系后注册内部名称，不允许选用别家媒体车系。"""
    if not isinstance(series_id, str) or not series_id.startswith('media:'):
        return series_id
    rows = conn.execute('''SELECT DISTINCT brand_id,brand_name,series_id,series_name FROM media_vehicle_items
        WHERE org_id=? AND media_id=? AND 'media:'||media_id||':'||brand_id||':'||series_id=?''',
        (org_id, media_id, series_id)).fetchall()
    if len(rows) != 1:
        raise Fault('MEDIA_VEHICLE_UNMATCHED', '请选择最终媒体车型库中的唯一车系')
    row = rows[0]
    existing = conn.execute('SELECT brand_name,series_name FROM catalog WHERE series_id=?', (series_id,)).fetchone()
    if existing and (existing['brand_name'], existing['series_name']) != (row['brand_name'], row['series_name']):
        raise Fault('MEDIA_CATALOG_CHANGED', '车系编号对应名称已变化，请核对后再创建需求')
    conn.execute('INSERT OR IGNORE INTO catalog(series_id,brand_id,brand_name,series_name) VALUES(?,?,?,?)',
                 (series_id, str(row['brand_id']), row['brand_name'], row['series_name']))
    return series_id
