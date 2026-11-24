"""逻辑删除误填来源，原子停止同步和未发送交付，保留业务历史。"""
from domain import Fault, now


def require_current_source(source: dict):
    """拒绝使用已删除来源执行同步、编辑或导入写回，不阻止历史只读查询。"""
    if source.get('deleted_at'):
        raise Fault('SOURCE_DELETED', '该需求来源已删除，不能再次同步或修改', 409)


def delete_source(sync, actor: dict, source_id: str, version) -> dict:
    """本组织需求权限下按版本删除整份来源，不物理删除原文或交付。

    同步中的读取不持有事务，此处使运行锁失效，迟到结果不得写回。
    已发送和UNKNOWN交付保留，未发送任务经原取消方法释放配额。
    重复删除幂等，未授权和跨组织请求不能修改来源。
    """
    sync.p.require(actor, 'demand')
    with sync.p.tx() as conn:
        source = sync._source(conn, actor, source_id)
        if source.get('deleted_at'):
            return {'source_id': source_id, 'deleted': True, 'already_deleted': True}
        if source['replaced_by']:
            raise Fault('SOURCE_REPLACED', '该来源已归档，请删除当前使用的来源', 409)
        if type(version) is not int or version != source['version']:
            raise Fault('VERSION_CONFLICT', '来源已修改，请刷新后再删除', 409)
        stamp = now()
        conn.execute('''UPDATE demand_sync_sources SET deleted_at=?,deleted_by=?,enabled=0,
                        version=version+1,run_id=NULL,running_until=NULL WHERE org_id=? AND id=?''',
                     (stamp, actor['id'], actor['org_id'], source_id))
        if source['run_id']:
            conn.execute("UPDATE demand_sync_runs SET status='FAILED',finished_at=?,error='来源已删除，本轮同步终止' WHERE org_id=? AND id=? AND status='RUNNING'",
                         (stamp, actor['org_id'], source['run_id']))
        outcome = sync.p._reconcile_source(conn, actor, source_id)
        sync.p.audit(conn, actor, 'DEMAND_SOURCE_DELETED', source_id,
                     {'project': source['sale_project'], 'closed_demands': outcome['closed']})
        return {'source_id': source_id, 'deleted': True, 'already_deleted': False,
                'closed_demands': outcome['closed']}
