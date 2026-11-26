"""字典页面隔离验收服务，只用临时库和假API，禁止连接外网。"""
import json
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import server
from domain import Platform
from demand_sync import DemandSync
from media_catalogs import MediaCatalogs, DEFAULT_ENDPOINTS
from media_vehicles import MediaVehicles
from test_media_catalogs import car, page, A


def fetcher(endpoint, token, kind, number):
    vehicle = dict(car(), id=99, year=2026, guidePrice='13.50', displacement=None,
                   power='150', gearbox='测试变速箱', saleStatus=0, memberBrandName='测试会员品牌')
    return page([vehicle]) if kind == 'vehicles' else page([
        {'id': 99, 'cityCode': 2501, 'cityName': '上海', 'parentName': '上海市'}])


def blocked_demand_fetch(url):
    """本地预览只读取已有需求，拒绝读取真实外部来源。"""
    raise RuntimeError('LOCAL_PREVIEW_EXTERNAL_NETWORK_BLOCKED')


class FakeCatalogs(MediaCatalogs):
    def __init__(self, platform):
        super().__init__(platform, fetcher)


with tempfile.TemporaryDirectory(prefix='catalog-ui-only-') as directory:
    platform = Platform(directory)
    platform.seed()
    library = FakeCatalogs(platform)
    for kind in ('cities', 'vehicles'):
        library.configure(A, 'MEDIA_A', kind, {'endpoint': DEFAULT_ENDPOINTS[kind], 'enabled': True, 'version': 0})
        library._sync(*library._claim(A, 'MEDIA_A', kind, 'manual'))
    server.DEMAND_SYNC = DemandSync(platform, blocked_demand_fetch)
    server.P = platform
    server.VEHICLES = MediaVehicles(platform)
    server.MediaCatalogs = FakeCatalogs
    original = socket.socket
    class LocalSocket(original):
        def connect(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in ('127.0.0.1', 'localhost', '::1'):
                raise RuntimeError('CATALOG_TEST_EXTERNAL_NETWORK_BLOCKED')
            return super().connect(address)
    socket.socket = LocalSocket
    print(json.dumps({'ready': True, 'temporary_database': True, 'external_network': False}), flush=True)
    server.ThreadingHTTPServer(('127.0.0.1', 8879), server.Handler).serve_forever()
