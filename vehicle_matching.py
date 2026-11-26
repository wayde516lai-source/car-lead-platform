"""车型名称候选规则；发送条件由领域分发校验负责。"""
import json

from synced_demands import model_name, norm


def demand_model_matches(conn, org_id, lead, demand, context=None):
    """按车型匹配需求，忽略符号；全系需求仍限定品牌，避免扩展为所有车型。

    只比较本组织可见的车型名称，不依赖媒体编码、城市、额度或发送开关。
    字母和数字保留，防止不同型号因去符号被混淆。
    """
    cache = context.setdefault('model_names', {}) if context is not None else {}

    def vehicle(series_id):
        key = (org_id, series_id)
        if key not in cache:
            cache[key] = conn.execute("""SELECT brand_name,series_name FROM catalog WHERE series_id=?
                AND NOT EXISTS (SELECT 1 FROM manual_vehicle_entries WHERE series_id=catalog.series_id
                AND org_id!=?)""", (series_id, org_id)).fetchone()
        return cache[key]

    source = vehicle(lead['series_id'])
    if not source:
        return False
    if demand.get('external_project_id'):
        if demand.get('api_submit_limit') is not None:
            targets = [(row['model'], row['brand']) for row in json.loads(demand['api_vehicles'])]
        else:
            targets = [(name, demand['match_brand']) for name in json.loads(demand['match_models'])]
            if demand['all_models']:
                targets = [('全系', demand['match_brand'])]
    else:
        target = vehicle(demand['series_id'])
        targets = [(target['series_name'], target['brand_name'])] if target else []
    for name, brand in targets:
        if norm(name) in ('全系', '全车型', '不限') or norm(source['series_name']) == '全系':
            if norm(source['brand_name']) == norm(brand):
                return True
        elif model_name(name, brand) and model_name(source['series_name'], source['brand_name']) == model_name(name, brand):
            return True
    return False
