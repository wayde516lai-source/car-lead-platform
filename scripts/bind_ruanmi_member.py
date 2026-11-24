"""维护时预览或应用软米会员接口归属，不启动服务或发送线索。"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain import Fault, Platform
from zhemanhui_binding import MEDIA_NAME, PARTNER_NAME, bind_member_interface, binding_plan


def main():
    """默认只读；显式apply前校验库版本、管理员与归属，创建加密备份。"""
    parser = argparse.ArgumentParser(description='绑定软米会员接口，默认只预览')
    parser.add_argument('--data', required=True, help='待维护系统的私有数据目录')
    parser.add_argument('--admin-user', required=True, help='该组织已启用的管理员用户名')
    parser.add_argument('--partner-name', default=PARTNER_NAME)
    parser.add_argument('--media-name', default=MEDIA_NAME)
    parser.add_argument('--route-id', help='存在多条候选路由时必须指定')
    parser.add_argument('--probe-id', help='本组织已成功的独立联调记录编号，可选')
    parser.add_argument('--apply', action='store_true', help='执行归属，保持外发开关不变')
    args = parser.parse_args()
    directory = Path(args.data).resolve()
    database = directory / 'platform.sqlite'
    if not database.is_file():
        parser.error('数据库不存在，不自动初始化新系统')
    selection = {'partner_name': args.partner_name, 'media_name': args.media_name,
                 'route_id': args.route_id, 'probe_id': args.probe_id}
    try:
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            expected = max(int(path.name.split('_')[0]) for path in (ROOT / 'migrations').glob('*.sql'))
            version = conn.execute('SELECT max(version) FROM schema_version').fetchone()[0]
            if version != expected:
                parser.error('数据库版本与工具不一致，先核对版本；本工具不执行升级')
            user = conn.execute("SELECT id,org_id,role FROM users WHERE username=? AND enabled=1 AND role='admin'",
                                (args.admin_user,)).fetchone()
            if not user:
                parser.error('请指定本系统已启用的管理员')
            actor = dict(user)
            plan = binding_plan(conn, actor, **selection)
        if args.apply:
            platform = Platform(directory)
            backup = platform.backup(actor)
            plan = bind_member_interface(platform, actor, apply=True, **selection)
            plan['backup_file'] = backup['file']
        print(json.dumps({'mode': 'applied' if args.apply else 'read-only preview', **plan}, ensure_ascii=False, indent=2))
    except Fault as error:
        parser.error(error.code + '：' + error.message)
    except sqlite3.Error:
        parser.error('数据库不可读取或结构不兼容，请先核对路径和运行版本')


if __name__ == '__main__':
    main()
