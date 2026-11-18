import tempfile
import unittest
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain import Fault, Platform
from media_vehicles import ADAPTERS, MediaVehicles, SyncError, normalize_yiche


ADMIN = {'org_id': 'demo', 'id': 'u_admin', 'role': 'admin'}


def car(car_id, name):
    return {
        'carId': car_id, 'carName': name, 'seriesId': 7, 'seriesName': '车系',
        'factoryId': 8, 'factoryName': '厂商', 'brandId': 9, 'brandName': '品牌',
    }


class MediaVehiclesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.platform = Platform(directory.name)
        self.platform.seed()
        self.library = MediaVehicles(self.platform)
        self.library.configure(ADMIN, 'MEDIA_A', '728', 'synthetic-test-key')

    def sync(self, pages):
        def fetch(cid, key, page):
            result = pages[page - 1]
            if isinstance(result, Exception):
                raise result
            return result

        with patch.dict(ADAPTERS, {'yiche': (fetch, normalize_yiche)}):
            claim = self.library._claim(ADMIN, 'MEDIA_A', 'manual')
            self.library._sync(*claim)

    def rows(self, media='MEDIA_A'):
        return self.library.list(ADMIN, media, '', 1)['items']

    def test_full_snapshot_and_media_isolation(self):
        self.sync([[car(1, '旧车型')], [car(2, '保留车型')], []])
        self.assertEqual(len(self.rows()), 2)
        self.sync([[car(2, '保留车型'), car(3, '新增车型')], []])
        self.assertEqual({row['carId'] for row in self.rows()}, {2, 3})
        self.assertEqual(self.rows('MEDIA_B'), [])
        self.assertIsNone(self.library._claim(ADMIN, 'MEDIA_A', 'auto'))
        self.assertNotIn('synthetic-test-key', str(self.library.overview(ADMIN)))

    def test_failed_later_page_keeps_previous_snapshot(self):
        self.sync([[car(1, '旧车型')], []])
        self.sync([[car(2, '未完成新车型')], SyncError('请求失败')])
        self.assertEqual([row['carId'] for row in self.rows()], [1])
        self.assertEqual(self.library.overview(ADMIN)[0]['runs'][0]['state'], 'FAILED')

    def test_schedule_waits_for_first_midnight(self):
        with patch('media_vehicles.day', return_value='2026-11-06'):
            self.library.configure(ADMIN, 'MEDIA_A', '728', 'synthetic-test-key', enabled=False)
            self.library.configure(ADMIN, 'MEDIA_A', '728', '', enabled=True)
            self.assertIsNone(self.library._claim(ADMIN, 'MEDIA_A', 'auto'))
        with patch('media_vehicles.day', return_value='2026-11-07'):
            self.assertIsNotNone(self.library._claim(ADMIN, 'MEDIA_A', 'auto'))
            self.assertIsNone(self.library._claim(ADMIN, 'MEDIA_A', 'auto'))

    def test_duplicate_sync_and_unauthorized_media(self):
        self.library._claim(ADMIN, 'MEDIA_A', 'manual')
        with self.assertRaisesRegex(Fault, 'VEHICLE_SYNC_RUNNING'):
            self.library._claim(ADMIN, 'MEDIA_A', 'manual')
        with self.assertRaisesRegex(Fault, 'NOT_FOUND'):
            self.library.list({**ADMIN, 'org_id': 'other'}, 'MEDIA_A', '', 1)
        with self.assertRaisesRegex(Fault, 'VEHICLE_ADAPTER_MISSING'):
            self.library.configure(ADMIN, 'MEDIA_B', '728', 'synthetic-test-key')


if __name__ == '__main__':
    unittest.main(verbosity=2)
