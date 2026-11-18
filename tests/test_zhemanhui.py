import unittest
from urllib.parse import urlsplit,parse_qs
from zhemanhui import *
class ZhemanhuiTests(unittest.TestCase):
 def request(self,**changes):
  data=dict(demand_no='00102217',city_id='2501',series_id='11433',name='虚构测试先生',mobile='19900000000',delivery_id='test_delivery',callback_token='x'*40);data.update(changes)
  return prepare(Config('https://saas.asoapple.cn'+CALLBACK_PATH),**data)
 def test_request_fields_and_nested_callback_encoding(self):
  r=self.request();v=parse_qs(urlsplit(r.full_url).query);self.assertEqual(v['supid'],['6']);self.assertEqual(v['ptitle'],['00102217']);self.assertEqual(v['name'],['虚构测试先生']);self.assertEqual(r.method,'GET')
  fields=callback_fields(urlsplit(v['sourceUrl'][0]).query+'&resultCode=0&code=2001');self.assertEqual(fields['delivery_id'],'test_delivery')
 def test_invalid_input_and_no_live_by_default(self):
  for change in [dict(mobile='abc'),dict(city_id='上海市'),dict(series_id='全系'),dict(callback_token='bad')]:
   with self.subTest(change=change),self.assertRaises(ProtocolError):self.request(**change)
  with self.assertRaises(ProtocolError):send_test(self.request())
 def test_success_is_not_final_valid_and_ambiguity_never_auto_retry(self):
  self.assertEqual(submission_result(200,b'{"errorCode":"1"}')['validity'],'PENDING')
  self.assertEqual(submission_result(201,b'{"errorCode":"-1"}')['status'],'FAILED')
  for status,body in [(500,b'{"errorCode":"1"}'),(200,b'garbage'),(200,b'[]'),(200,b'{"errorCode":0}')]:self.assertEqual(submission_result(status,body)['status'],'UNKNOWN')
 def test_callback_duplicate_parameter_rejected(self):
  with self.assertRaises(ProtocolError):callback_fields('delivery_id=a&delivery_id=b&callback_token=t&resultCode=0&code=2001')
 def test_dictionary_ids_are_not_row_ids(self):
  row={'id':24,'cityCode':201,'cityName':'北京'};self.assertEqual(dictionary_rows({'errorCode':'1','message':{'data':[row]}},'cities')[0]['cityCode'],201)
