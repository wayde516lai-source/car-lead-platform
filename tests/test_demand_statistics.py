"""数量汇总不混合单位，不把非法数量计成有效下单目标。"""
import unittest

from demand_statistics import summarize_order_quantities


class DemandStatisticsTests(unittest.TestCase):
    def test_quantity_units_and_invalid_values(self):
        result = summarize_order_quantities([
            ('DAILY', '12'), ('DAILY', 3), ('TOTAL', '20'),
            ('TOTAL', ''), ('DAILY', True), ('UNKNOWN', 1), ('DAILY', '-2'), ('TOTAL', '1.5'),
        ])
        self.assertEqual(result, {'daily': 15, 'total': 20, 'invalid_rows': 5})
