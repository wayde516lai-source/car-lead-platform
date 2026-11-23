"""通过隐藏密码输入初始化首个管理员，不在命令参数或日志中传递密码。"""
import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import initialize_admin
from domain import Platform


def main():
    """初始化空数据库，已有账号或输入不合格时返回失败。"""
    parser = argparse.ArgumentParser(description='首次管理员初始化（不生成演示数据）')
    parser.add_argument('--data', default=str(ROOT / 'data'))
    parser.add_argument('--username', required=True)
    parser.add_argument('--org-id', default='main')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error('请在交互终端中运行，以隐藏方式输入密码')
    password = getpass.getpass('管理员密码（至少12字符）：')
    if password != getpass.getpass('再次输入密码：'):
        parser.error('两次密码不一致')
    try:
        initialize_admin(Platform(args.data), args.username, password, args.org_id)
    except ValueError as error:
        parser.error(str(error))
    print('管理员已创建，未生成演示业务数据；分发默认关闭。')


if __name__ == '__main__':
    main()
