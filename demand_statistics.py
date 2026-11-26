"""需求下单量统计口径：每日目标与项目周期总目标分开汇总。"""
import json
import re
from datetime import datetime


def summarize_order_quantities(orders) -> dict:
    """汇总(basis, quantity)序列，拒绝把未识别或非法数量当作有效下单量。

    DAILY单位为条/日，TOTAL单位为条/项目；两种单位不相加。
    不扣减有效或在途量，统计原需求下单目标而非剩余配额。
    """
    result = {'daily': 0, 'total': 0, 'invalid_rows': 0}
    for basis, quantity in orders:
        text = str(quantity).strip()
        if basis not in ('DAILY', 'TOTAL') or not re.fullmatch(r'[0-9]+', text) or int(text) <= 0:
            result['invalid_rows'] += 1
            continue
        result['daily' if basis == 'DAILY' else 'total'] += int(text)
    return result


def demand_has_ended(demand, at):
    """识别当前展示中的结束需求，不改写需求或历史交付状态。"""
    try:
        time_ended = bool(demand.get('end_at') and datetime.fromisoformat(demand['end_at']) <= datetime.fromisoformat(at))
    except (ValueError, TypeError):
        time_ended = False
    return (demand.get('status') in ('CLOSED', 'COMPLETED')
            or demand.get('sync_state') in ('ENDED', 'REMOVED')
            or time_ended)


def execution_days(start_at, at):
    """按已过去的完整24小时计算执行天数；未开始为0，缺失/非法开始时间为空。"""
    if not start_at:
        return None
    try:
        elapsed = datetime.fromisoformat(at) - datetime.fromisoformat(start_at)
    except (ValueError, TypeError):
        return None
    return max(0, elapsed.days)


def daily_demand_ranking(demands, at):
    """每日下单目标前十；保留一项需求的多车型/多城市，不重复放大量级。"""
    from synced_demands import city_name
    result = []
    for demand in demands:
        row = dict(demand)
        if row['status'] not in ('ACTIVE', 'PAUSED'):
            continue
        quantity = row['target_total'] if row['quantity_basis'] == 'DAILY' else row['daily_limit']
        if demand_has_ended(row, at) or quantity <= 0:
            continue
        vehicles = json.loads(row['api_vehicles'])
        brands = list(dict.fromkeys(item['brand'] for item in vehicles)) if vehicles else [row['brand'] or '未填写品牌']
        models = list(dict.fromkeys(item['model'] for item in vehicles)) if vehicles else json.loads(row['match_models'] or '[]') or [row['series_name'] or '未填写车型']
        cities = json.loads(row['match_regions'] or '[]') or json.loads(row['city_codes'])
        result.append({'id': row['id'], 'demand_no': row['external_demand_no'] or row['demand_no'],
                       'brand': ' / '.join(brands), 'model': ' / '.join(models),
                       'city': ' / '.join(city_name(value) for value in cities) or '未填写城市', 'daily_quantity': quantity})
    return sorted(result, key=lambda row: (-row['daily_quantity'], row['id']))[:10]
