"""Offline protocol and delivery tests. All HTTP transport is replaced by fakes."""
import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import yiche_transport as wire
from domain import Fault, Platform, uid

ACTOR = {'org_id': 'demo', 'id': 'u_admin', 'role': 'admin'}
CONFIG = wire.Config('728', 'fake-sign', b'fake-aes', 'https://callback.example.invalid' + wire.CALLBACK_PATH)


def unpack_callback(body):
    value = base64.urlsafe_b64decode(body['param']['sourceUrl'])
    cipher = Cipher(algorithms.AES(CONFIG.aes_key.ljust(16, b'\0')), modes.ECB()).decryptor()
    unpad = padding.PKCS7(128).unpadder()
    decoded = unpad.update(cipher.update(value) + cipher.finalize()) + unpad.finalize()
    return {key: values[0] for key, values in parse_qs(urlsplit(decoded.decode()).query).items()}


class FakeResponse(io.BytesIO):
    status = 200


class YichePush(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.platform = Platform(directory.name)
        self.platform.seed()
        self.lead = next(x for x in self.platform.list(ACTOR, 'leads', 100)['items'] if x['lead_no'] == 'L000001')
        self.demand = next(x for x in self.platform.list(ACTOR, 'demands', 100)['items'] if x['demand_no'] == '000123')

    def queue(self):
        result = self.platform.dispatch(ACTOR, [(self.lead['id'], self.demand['id'])], uid())
        return result['items'][0]['delivery_id']

    def live_queue(self):
        with self.platform.tx() as conn:
            conn.execute("INSERT INTO media_vehicle_items VALUES('demo','MEDIA_A',900005,'车款',900003,'车系',900004,'厂商',900006,'品牌','{}')")
        self.platform.yiche_live = CONFIG
        return self.queue()

    def callback(self, params):
        handler = object.__new__(server.Handler)
        handler.send = Mock()
        with patch.object(server, 'P', self.platform):
            handler.yiche_callback('GET', urlencode(params))
        return handler.send.call_args.args

    def test_signs_exact_param_bytes_and_optional_uid(self):
        config = wire.Config('728','fake-sign',b'fake-aes',CONFIG.callback_url,'test-uid')
        request = wire.signed_request(config, {'param': {'ptitle':'中文','cityId':2}}, 1234567890123)
        param = '{"ptitle":"中文","cityId":2}'
        self.assertEqual(request.data.decode(), '{"param":' + param + '}')
        base = 'cid=728;param=' + param + ';uid=test-uid;ver=1.0.0;t=1234567890123;fake-sign'
        self.assertEqual(request.get_header('Sign'), hashlib.md5(base.encode()).hexdigest())
        self.assertEqual(request.get_method(), 'POST')

    def test_default_mode_never_calls_transport(self):
        delivery_id = self.queue()
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(wire.from_environment())
        with patch.object(wire, 'send', side_effect=AssertionError('outbound disabled')) as send:
            self.platform.worker(ACTOR)
            send.assert_not_called()
        self.assertEqual(self.platform.detail(ACTOR, 'deliveries', delivery_id)['send_status'], 'ACKED')

    def test_acceptance_and_duplicate_final_callback(self):
        delivery_id = self.live_queue()
        with patch.object(wire, 'build_opener', return_value=Mock(open=Mock(return_value=FakeResponse(b'{"status":"1","data":null}')))) as opener:
            self.platform.worker(ACTOR)
        request = opener.return_value.open.call_args.args[0]
        payload = json.loads(request.data)
        params = unpack_callback(payload) | {'resultCode': '0', 'code': '2001'}
        self.assertEqual(params['orderId'], delivery_id)
        self.assertEqual(params['platFormId'], '900001')
        self.assertEqual(self.platform.detail(ACTOR,'deliveries',delivery_id)['storage_status'], 'STORED')
        expected = (200, {'resultCode':0,'resultMessage':'success'})
        self.assertEqual(self.callback(params), expected)
        self.assertEqual(self.callback(params), expected)
        self.assertEqual(self.callback(params | {'platFormId': '1'})[0], 403)
        self.assertEqual(self.callback(params | {'callbackToken': 'x'*43})[0], 403)
        d = self.platform.detail(ACTOR, 'deliveries', delivery_id)
        self.assertEqual((d['validity_status'],d['callback_status']), ('VALID','COMPLETE'))
        self.assertEqual(d['yiche_code'], {'code': '2001', 'description': '有效线索'})
        self.assertEqual(len(d['callbacks']), 1)
        self.assertTrue(self.platform.integrity(ACTOR)['passed'])

    def test_callback_before_ambiguous_http_result(self):
        delivery_id = self.live_queue()

        def send(config, body):
            params = unpack_callback(body) | {'resultCode':'0','code':'-10'}
            self.assertEqual(self.callback(params)[0], 200)
            return 'UNKNOWN', None, 'YICHE_TRANSPORT_ERROR'

        with patch.object(wire, 'send', side_effect=send):
            self.platform.worker(ACTOR)
        d = self.platform.detail(ACTOR, 'deliveries', delivery_id)
        self.assertEqual((d['send_status'],d['validity_status'],d['quota_state']), ('ACKED','INVALID','RELEASED'))
        self.assertEqual(d['yiche_code'], {'code': '-10', 'description': '无效用户'})
        self.assertTrue(self.platform.integrity(ACTOR)['passed'])

    def test_definite_rejection_releases_quota_and_does_not_retry(self):
        delivery_id = self.live_queue()
        with patch.object(wire,'build_opener',return_value=Mock(open=Mock(return_value=FakeResponse(b'{"status":2,"ercd":"-16"}')))) as opener:
            self.platform.worker(ACTOR)
            self.platform.worker(ACTOR)
        opener.return_value.open.assert_called_once()
        d = self.platform.detail(ACTOR,'deliveries',delivery_id)
        self.assertEqual((d['send_status'],d['storage_status'],d['quota_state']), ('FAILED','REJECTED','RELEASED'))
        self.assertTrue(self.platform.integrity(ACTOR)['passed'])

    def test_uncertain_response_preserves_reservation_and_does_not_retry(self):
        delivery_id = self.live_queue()
        with patch.object(wire,'send',return_value=('UNKNOWN',None,'YICHE_TRANSPORT_ERROR')) as send:
            self.platform.worker(ACTOR)
            self.platform.worker(ACTOR)
        send.assert_called_once()
        d = self.platform.detail(ACTOR,'deliveries',delivery_id)
        self.assertEqual((d['send_status'],d['quota_state']), ('UNKNOWN','RESERVED'))
        self.assertTrue(self.platform.integrity(ACTOR)['passed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
