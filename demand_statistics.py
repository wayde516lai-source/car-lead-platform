"""需求下单量统计口径：每日目标与项目周期总目标分开汇总。"""
import re


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
