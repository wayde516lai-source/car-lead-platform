"""仅用虚构响应验证KA需求解析，不访问真实接口或业务数据库。"""
import copy
import json
import unittest

from ka_demand_api import DemandProtocolError, parse_response


def sample():
    return {'errorCode': '1', 'message': {'data': [{
        'id': 1, 'crid': '000123', 'limit': 62, 'LimitOfDay': 2,
        'ListPro': json.dumps([{'ListCity': [
            {'CityId': 101, 'CityName': '测试城市甲'},
            {'CityId': 102, 'CityName': '测试城市乙'}]}]),
        'ListCmb': json.dumps([{'CmbId': 2, 'CmbName': '测试品牌', 'ListCmk': [
            {'CmkId': 3, 'CmkName': '测试厂商', 'ListCmd': [
                {'CmdId': 4, 'CmdName': '测试车型甲'},
                {'CmdId': 5, 'CmdName': '测试车型乙'}]}]}]),
        'CRDID': '000456', 'BeginTime': '2026-11-01 00:00:00',
        'EndTime': '2026-11-30 23:59:59', 'UpdateTime': '2026-11-23 12:00:00',
        'status': 0, 'day': 20261123}]}}


class KaDemandApiTests(unittest.TestCase):
    def test_fields_and_multiple_values(self):
        payload = sample()
        original = copy.deepcopy(payload)
        row = parse_response(payload)[0]
        self.assertEqual(row['demand_no'], '000123')
        self.assertEqual(row['activity_id'], '000456')
        self.assertEqual((row['monthly_limit'], row['daily_limit']), (62, 2))
        self.assertEqual([city['id'] for city in row['cities']], ['101', '102'])
        self.assertEqual([vehicle['series_id'] for vehicle in row['vehicles']], ['4', '5'])
        self.assertEqual(payload, original)

    def test_brand_model_relationship_and_dedup(self):
        payload = sample()
        row = payload['message']['data'][0]
        brands = json.loads(row['ListCmb'])
        brands[0]['ListCmk'][0]['ListCmd'].append({'CmdId': 4, 'CmdName': '测试车型甲'})
        brands.append({'CmbId': 6, 'CmbName': '另一品牌', 'ListCmk': [
            {'CmkId': 7, 'CmkName': '另一厂商', 'ListCmd': [{'CmdId': 8, 'CmdName': '另一车型'}]}]})
        row['ListCmb'] = brands
        result = parse_response(payload)[0]
        self.assertEqual(len(result['vehicles']), 3)
        self.assertEqual(result['vehicles'][2]['brand'], '另一品牌')

    def test_empty_result_does_not_infer_deletions(self):
        self.assertEqual(parse_response({'errorCode': 1, 'message': {'data': []}}), [])

    def test_failed_or_unknown_envelopes(self):
        for payload in ({}, None, {'errorCode': 0}, {'errorCode': 1, 'message': {'data': {}}}):
            with self.subTest(payload=payload), self.assertRaises(DemandProtocolError):
                parse_response(payload)

    def test_duplicate_demand_and_partial_failure_rejected(self):
        payload = sample()
        payload['message']['data'].append(copy.deepcopy(payload['message']['data'][0]))
        with self.assertRaises(DemandProtocolError):
            parse_response(payload)
        payload['message']['data'][1]['crid'] = '000124'
        payload['message']['data'][1]['ListPro'] = 'invalid'
        with self.assertRaises(DemandProtocolError):
            parse_response(payload)

    def test_invalid_fields_and_unsafe_implicit_types(self):
        for field, value in [('crid', 123), ('limit', -1), ('LimitOfDay', True),
                             ('BeginTime', 'bad'), ('EndTime', '2026-10-31 00:00:00'),
                             ('day', 20260230), ('ListPro', []), ('ListCmb', [])]:
            with self.subTest(field=field):
                payload = sample()
                payload['message']['data'][0][field] = value
                with self.assertRaises(DemandProtocolError):
                    parse_response(payload)

    def test_zero_quota_and_unknown_status_rejected(self):
        payload = sample()
        row = payload['message']['data'][0]
        row.update(status=0, limit=0, LimitOfDay=0)
        result = parse_response(payload)[0]
        self.assertEqual(result['status'], 0)
        self.assertEqual(result['daily_limit'], 0)
        self.assertNotIn('eligible', result)
        row['status'] = 99
        with self.assertRaises(DemandProtocolError):
            parse_response(payload)

    def test_conflicting_series_identity_rejected(self):
        payload = sample()
        row = payload['message']['data'][0]
        brands = json.loads(row['ListCmb'])
        brands[0]['ListCmk'][0]['ListCmd'].append({'CmdId': 4, 'CmdName': '错误车型'})
        row['ListCmb'] = brands
        with self.assertRaises(DemandProtocolError):
            parse_response(payload)

class TransportTests(unittest.TestCase):
    def test_no_token_get_and_secret_safe_failures(self):
        from unittest.mock import MagicMock, patch
        from ka_demand_api import API_URL, read_demands
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(sample()).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(len(read_demands(API_URL, 'NONE')), 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertNotIn('Authorization', dict(request.header_items()))
        self.assertIsNone(request.data)
        with patch('urllib.request.build_opener') as transport:
            for url, mode, token in [(API_URL, 'UNCONFIRMED', ''),
                                     (API_URL, 'BEARER', 'fake-token'),
                                     ('http://127.0.0.1/private', 'NONE', '')]:
                with self.assertRaises(DemandProtocolError):
                    read_demands(url, mode, token)
            transport.assert_not_called()

    def test_token_only_https_and_redirects_disabled(self):
        from unittest.mock import MagicMock, patch
        from ka_demand_api import API_URL, read_demands
        opener = MagicMock()
        opener.open.side_effect = RuntimeError('DO-NOT-EXPOSE-fake-test-secret')
        with patch('urllib.request.build_opener', return_value=opener) as factory:
            with self.assertRaises(DemandProtocolError) as caught:
                read_demands(API_URL.replace('http:', 'https:'), 'BEARER', 'fake-test-secret')
            self.assertNotIn('fake-test-secret', str(caught.exception))
            self.assertIsNone(factory.call_args.args[0].redirect_request(None,None,None,None,None,None))


if __name__ == '__main__':
    unittest.main()
