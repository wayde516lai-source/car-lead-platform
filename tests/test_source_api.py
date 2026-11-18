import json
import secrets
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import server
from domain import Platform, Fault
from security import canonical, sign

ADMIN={'id':'u_admin','org_id':'demo','role':'admin'}

class SourceAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.platform=Platform(self.temp.name)
        self.platform.seed()
        server.P=self.platform
        self.key=self.platform.issue_source_key(ADMIN,'ADS')
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.row={
            '编号':'SOURCE-0001','需求编号':'000123','渠道名称':'供应方表单',
            '车系名称':'宝马 X5','品牌名称':'宝马','城市':'上海市',
            '手机号':'13900008881','客户姓名':'测试客户','上报状态':'上报成功',
            '回调状态':'失败','线索状态':'-12','上报时间':'2026-11-11 10:00:00',
            '回调时间':'2026-11-11T10:01:00+08:00',
        }

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.temp.cleanup()

    def send(self,channel='ADS',row=None,key=None):
        key=key or self.key
        body=canonical({'channel_id':channel,'items':[row or self.row]}).encode()
        timestamp=str(int(time.time()))
        nonce=secrets.token_hex(16)
        headers={
            'Content-Type':'application/json','X-Key-Id':key['key_id'],
            'X-Timestamp':timestamp,'X-Nonce':nonce,
            'X-Signature':sign(key['secret'],'POST','/open/v1/leads',timestamp,nonce,body),
        }
        request=urllib.request.Request(
            f'http://127.0.0.1:{self.http.server_port}/open/v1/leads',
            data=body,headers=headers)
        try:
            response=urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response=error
        with response:
            return response.status,json.load(response)

    def test_details_idempotency_and_permissions(self):
        status,response=self.send()
        self.assertEqual(status,202)
        item=response['data']['items'][0]
        lead_id=item['lead_id']
        details=self.platform.detail(ADMIN,'leads',lead_id)
        source=details['source_details'][0]
        self.assertEqual((source['source_record_id'],source['demand_no']),('SOURCE-0001','000123'))
        self.assertEqual((source['callback_status'],source['lead_status']),('FAILED','姓名无效'))
        self.assertEqual(source['reported_at'],'2026-11-11T02:00:00+00:00')
        self.assertNotIn('13900008881',json.dumps(source))
        self.assertNotIn('source_status',details)
        self.assertEqual(details['channel_id'],'ADS')
        self.assertEqual(self.send()[1]['data']['items'][0]['lead_id'],lead_id)
        self.assertEqual(len(self.platform.detail(ADMIN,'leads',lead_id)['source_details']),1)
        changed=dict(self.row,需求编号='different')
        self.assertEqual(self.send(row=changed)[1]['data']['items'][0]['code'],'IDEMPOTENCY_CONFLICT')
        with self.platform.tx() as c:
            c.execute('INSERT INTO lead_field_permissions VALUES(?,?)',('u_operator',canonical(['lead_no'])))
        restricted=self.platform.detail({'id':'u_operator','org_id':'demo','role':'operator'},'leads',lead_id)
        self.assertNotIn('phone',restricted['source_details'][0])
        self.assertNotIn('lead_status',restricted['source_details'][0])

    def test_channel_binding_revocation_and_validation(self):
        response=self.send(channel='TMK')[1]
        self.assertEqual(response['data']['items'][0]['code'],'CHANNEL_FORBIDDEN')
        self.assertEqual(self.send(channel=None)[1]['data']['items'][0]['code'],'CHANNEL_REQUIRED')
        invalid=dict(self.row,回调状态='其他状态')
        self.assertEqual(self.send(row=invalid)[1]['data']['items'][0]['code'],'SCHEMA_INVALID')
        self.assertEqual(self.send(row=dict(self.row,is_sold=True))[1]['data']['items'][0]['code'],'SALE_MEDIA_REQUIRED')
        self.platform.issue_source_key(ADMIN,'ADS')
        self.assertEqual(self.send()[0],401)
        with self.assertRaises(Fault):
            self.platform.issue_source_key(dict(ADMIN,role='operator'),'ADS')

if __name__=='__main__':
    unittest.main()
