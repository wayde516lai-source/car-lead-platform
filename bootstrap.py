"""首次管理员初始化与显式演示启动边界，不创建业务样例或执行外发。"""
from domain import uid
from security import password_hash


def initialize_admin(platform, username: str, password: str, org_id: str = 'main') -> str:
    """仅在全库无用户时原子创建管理员和组织配置，默认关闭分发。

    密码由命令行隐藏输入提供，不记录到审计；已有用户时拒绝修改账号。
    """
    username = username.strip()
    org_id = org_id.strip()
    if not username or len(username) > 80 or not org_id or len(org_id) > 80:
        raise ValueError('账号和组织标识不能为空，且最多80个字符')
    if len(password) < 12 or len(password) > 256:
        raise ValueError('管理员密码长度须为12至256个字符')
    encoded_password = password_hash(password)
    with platform.tx() as conn:
        if conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise ValueError('已有账号，不能重复初始化或覆盖现有管理员')
        # 该命令用于首次建立组织，不接管已有组织配置。
        if conn.execute('SELECT 1 FROM settings LIMIT 1').fetchone():
            raise ValueError('已有组织配置，请通过维护流程恢复账号，不能首次初始化')
        conn.execute('INSERT INTO settings(org_id, dispatch_enabled) VALUES(?, 0)', (org_id,))
        user_id = uid('u_')
        conn.execute(
            'INSERT INTO users(id, org_id, username, password_hash, role, enabled) VALUES(?,?,?,?,?,1)',
            (user_id, org_id, username, encoded_password, 'admin'),
        )
        platform.audit(conn, {'id': user_id, 'org_id': org_id}, 'INITIAL_ADMIN_CREATE', user_id)
    return user_id


def prepare_startup(platform, seed_demo: bool = False, host: str = '127.0.0.1',
                    public_test: bool = False, external_push: bool = False) -> None:
    """普通启动只核实账号存在；演示造数需显式选项且限回环、无外发环境。"""
    if seed_demo:
        if host not in ('127.0.0.1', 'localhost', '::1') or public_test or external_push:
            raise ValueError('演示初始化仅允许本机回环且未开启外部推送的环境')
        platform.seed()
    conn = platform.connect()
    try:
        if not conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise ValueError('尚未初始化管理员，请先运行 scripts/init_admin.py；普通启动不会生成演示数据')
    finally:
        conn.close()
