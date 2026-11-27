"""只读需求重合分析；共享分发校验，不预占库存，不发送或修改需求。"""
import json
import math

from demand_statistics import demand_has_ended
from project_availability import demand_project_condition
from synced_demands import city_name, model_name, norm


def analyze(platform, actor, query='', project='', sort='quantity', page=1, page_size=20, overlap_only=True):
    """按组织聚合车型/城市；同媒体取最大目标，跨城市共享量不重复求和。

    库存为当前逐条可分发候选，不保证可同时分发；未知未来授权和共享额度
    分配使补充建议不能精确推断，因此保留待核算并展示独立线索参考下界。
    """
    from domain import Fault, now, day, external_demand_no
    platform.require(actor, 'read')
    if project not in ('', 'MEMBER', 'KA') or sort not in ('quantity', 'coverage') or page_size not in (20, 50, 100):
        raise Fault('OVERLAP_FILTER_INVALID', '请选择合法项目、排序与分页')
    conn = platform.connect()
    try:
        fields = platform._lead_field_override(conn, actor)
        inventory_visible = fields is None or {'brand', 'model', 'city'}.issubset(fields)
        at = now()
        demands = [dict(row) for row in conn.execute(
            "SELECT d.*,cat.brand_name,cat.series_name FROM demands d LEFT JOIN catalog cat ON cat.series_id=d.series_id "
            "WHERE d.org_id=? AND d.status IN ('ACTIVE','PAUSED') AND " + demand_project_condition('d'), (actor['org_id'],))
            if not demand_has_ended(dict(row), at)]
        names = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM partners WHERE org_id=?', (actor['org_id'],))}
        vehicles = set()
        for row in conn.execute("SELECT brand_name,series_name FROM catalog WHERE NOT EXISTS (SELECT 1 FROM manual_vehicle_entries m WHERE m.series_id=catalog.series_id AND m.org_id!=?)", (actor['org_id'],)):
            if norm(row['series_name']) not in ('全系', '全车型', '不限'):
                vehicles.add((row['brand_name'], row['series_name']))
        for row in conn.execute('SELECT DISTINCT brand_name,factory_name,series_name FROM media_vehicle_items WHERE org_id=?', (actor['org_id'],)):
            vehicles.add((row['brand_name'], row['series_name']))
            vehicles.add((row['factory_name'], row['series_name']))
        explicit_cities = set()
        prepared = []
        for demand in demands:
            selected_project = demand['sale_project']
            if selected_project == 'UNKNOWN':
                selected_project = conn.execute('SELECT cooperation_project FROM partners WHERE org_id=? AND id=?', (actor['org_id'], demand['buyer_id'])).fetchone()[0] or 'MEMBER'
            if project and selected_project != project:
                continue
            pairs = [(demand['brand_name'] or demand['match_brand'], demand['series_name'] or '')]
            if demand['external_project_id']:
                pairs = [(demand['match_brand'], value) for value in json.loads(demand['match_models'])]
            if demand['api_vehicles'] != '[]':
                pairs = [(value['brand'], value['model']) for value in json.loads(demand['api_vehicles'])]
            cities = [city_name(value) for value in json.loads(demand['match_regions'] or '[]') or json.loads(demand['city_codes'])]
            national = demand['all_regions'] or any(value in ('全国', '不限', '全部地区') for value in cities)
            if not national:
                explicit_cities.update(cities)
            for brand, model in pairs:
                if norm(model) not in ('全系', '全车型', '不限'):
                    vehicles.add((brand, model))
            prepared.append((demand, selected_project, pairs, cities, national))
        groups = {}
        for demand, selected_project, pairs, cities, national in prepared:
            expanded = set()
            unresolved = False
            for brand, model in pairs:
                if norm(model) in ('全系', '全车型', '不限'):
                    choices = {(brand, name) for owner, name in vehicles if norm(owner) == norm(brand)}
                    if not choices:
                        choices = {(brand, '全系（范围待展开）')}
                        unresolved = True
                    expanded.update(choices)
                else:
                    expanded.add((brand, model))
            cities = sorted(explicit_cities) if national and explicit_cities else ['全国（范围待展开）'] if national else cities
            daily = demand['api_submit_limit'] if demand['api_submit_limit'] is not None else demand['target_total'] if demand['quantity_basis'] == 'DAILY' else demand['daily_limit']
            if daily <= 0:
                continue
            remaining = platform._sync_demand_public(conn, demand.copy()).get('remaining_today')
            if demand['api_submit_limit'] is None and demand['quantity_basis'] != 'DAILY':
                counter = conn.execute('SELECT reserved,sent FROM daily_counter WHERE demand_id=? AND day=?', (demand['id'], day())).fetchone()
                remaining = max(0, min(daily - (counter['reserved'] + counter['sent'] if counter else 0), demand['target_total'] - demand['reserved_count'] - demand['valid_count']))
            for brand, model in expanded:
                for city in cities:
                    if query.strip().casefold() not in (brand + ' ' + model + ' ' + city).casefold():
                        continue
                    key = ('unresolved:' + norm(brand) if model == '全系（范围待展开）' else model_name(model, brand), city_name(city))
                    group = groups.setdefault(key, {'model_key': key[0], 'brand': set(), 'model': model, 'city': city, 'details': {}, 'range_pending': False})
                    group['brand'].add(brand)
                    group['range_pending'] |= unresolved or '范围待展开' in city
                    group['details'][demand['id']] = {'id': demand['id'], 'demand_no': external_demand_no(demand),
                        'media_id': demand['media_id'], 'media': names.get(demand['media_id'], ''),
                        'buyer': names.get(demand['buyer_id'], ''), 'project': selected_project,
                        'daily_quantity': daily, 'remaining': remaining or 0, 'end_at': None if demand['open_ended'] else demand['end_at'],
                        'shared_scope': len(expanded) > 1 or len(cities) > 1 or national,
                        'scope_note': '多车型/城市共享额度，不可跨行相加' if len(expanded) > 1 or len(cities) > 1 or national else '',
                        'status': demand['status'], 'start_at': demand['start_at'], 'block_reasons': set()}
                    if len(groups) > 20000:
                        raise Fault('OVERLAP_SCOPE_TOO_LARGE', '范围过大，请输入车型或城市缩小分析范围')
        rows = []
        for group in groups.values():
            details = list(group['details'].values())
            if overlap_only and len(details) < 2:
                continue
            targets, remaining = {}, {}
            for detail in details:
                media = detail['media_id']
                targets[media] = max(targets.get(media, 0), detail['daily_quantity'])
                remaining[media] = max(remaining.get(media, 0), detail['remaining'])
            rows.append({**group, 'brand': ' / '.join(sorted(group['brand'])), 'details': details,
                'media_count': len(targets), 'demand_count': len(details), 'daily_quantity': sum(targets.values()),
                'common_quantity': min(targets.values()) if len(targets) > 1 else 0,
                'remaining': sum(remaining.values()), 'unique_reference': max(remaining.values(), default=0),
                'inventory': None, 'suggested': None, 'nearest_end': min((x['end_at'] for x in details if x['end_at']), default=None)})
        settings = conn.execute('SELECT max_media_count,dispatch_enabled FROM settings WHERE org_id=?', (actor['org_id'],)).fetchone()
        for row in rows:
            row['unique_reference'] = max(row['unique_reference'], math.ceil(row['remaining'] / max(1, settings[0])))
        rows.sort(key=lambda row: (-(row['media_count'] if sort == 'coverage' else row['daily_quantity']), -row['daily_quantity'], row['model'], row['city']))
        total = len(rows)
        page = max(1, page)
        items = rows[(page-1)*page_size:page*page_size]
        demand_by_id = {demand['id']: demand for demand, *_ in prepared}
        leads = platform._lead_rows(conn, actor) if inventory_visible else []
        lead_groups = {}
        for lead in leads:
            key = (model_name(lead['filter_model'] or '', lead['filter_brand'] or ''), city_name(lead['city_code']))
            lead_groups.setdefault(key, []).append(lead)
        context = {}
        for row in items:
            eligible_ids = set()
            for detail in row['details']:
                demand = demand_by_id[detail['id']]
                if detail['status'] != 'ACTIVE' or detail['start_at'] > at:
                    detail['block_reasons'].add('DEMAND_INACTIVE')
                route = platform.one(conn, 'routes', demand['route_id'], actor)
                if not route['enabled']:
                    detail['block_reasons'].add('ROUTE_UNAVAILABLE')
                if not settings['dispatch_enabled']:
                    detail['block_reasons'].add('DISPATCH_PAUSED')
                if not detail['remaining']:
                    detail['block_reasons'].add('DAILY_QUOTA_FULL')
                if route['adapter_version'] == 'zhemanhui-v1':
                    from route_sending import send_block
                    blocked = send_block(conn, actor['org_id'], route)
                    if blocked:
                        detail['block_reasons'].add(blocked)
                for lead in lead_groups.get((row['model_key'], city_name(row['city'])), []):
                    reason = platform._block(conn, actor, lead, demand, project=detail['project'], match_context=context)
                    if reason:
                        detail['block_reasons'].add(reason)
                    else:
                        eligible_ids.add(lead['id'])
                detail['block_reasons'] = sorted(detail['block_reasons'])
            row['inventory'] = len(eligible_ids) if inventory_visible and not row['range_pending'] else None
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size, 'has_more': page*page_size < total,
                'as_of': at, 'inventory_visible': inventory_visible}
    finally:
        conn.close()
