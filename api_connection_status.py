"""从可核验的线索API收发成功记录生成接通状态，不进行网络探测。

密钥和配置存在、模拟发送、需求拉取均不是线索API联调成功证据。
状态按采购渠道或供应主体加项目隔离，不等同于业务启停或最终有效。
"""
import json

from route_sending import control


def _detail(value):
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def public_status(stamp=None):
    """只返回状态及最近可核验成功时间，不返回凭证、报文或客户信息。"""
    return {'api_status': 'CONNECTED' if stamp else 'NOT_CONNECTED',
            'api_status_label': '接通' if stamp else '未接通', 'api_verified_at': stamp}


def connection_statuses(conn, org_id):
    """读取本组织证据，返回渠道和主体项目的最近成功时间映射。

    入站必须由当前有效密钥完成真实签名接收，旧LEAD_INGEST审计可作为历史证据。
    出站须具有专用提交记录及ACKED审计或已鉴权回调，路由快照须对应当前配置。
    独立探针仅在维护人员显式归属且当前路由身份仍一致时认作对应会员项目的接通证据。
    未认领的探针保持未接通，不猜测合作主体或会员/KA归属。
    """
    inbound, outbound = {}, {}
    for row in conn.execute('''SELECT k.channel_id,e.action,e.target_id,e.detail,e.created_at
        FROM source_api_keys k JOIN audit_log e
          ON e.org_id=k.org_id AND e.actor_id='open:'||k.id
        WHERE k.org_id=? AND k.enabled=1
          AND e.action IN ('SOURCE_API_RECEIVED','LEAD_INGEST')''', (org_id,)):
        detail = _detail(row['detail'])
        verified = (row['action'] == 'SOURCE_API_RECEIVED' and row['target_id'] == row['channel_id']
                    and detail.get('accepted') is True)
        legacy = (row['action'] == 'LEAD_INGEST' and detail.get('channel_id') == row['channel_id']
                  and detail.get('status') in ('CREATED', 'DUPLICATE'))
        if verified or legacy:
            key = row['channel_id']
            inbound[key] = max(inbound.get(key, ''), row['created_at'])
    for row in conn.execute('''SELECT e.action,e.detail,e.created_at,d.sale_project,d.route_snapshot,
        r.buyer_id,r.receiver_id,r.media_id,r.adapter_version,
        EXISTS(SELECT 1 FROM yiche_submissions y WHERE y.delivery_id=d.id) AS yiche_submission,
        EXISTS(SELECT 1 FROM zmh_submissions z WHERE z.delivery_id=d.id AND z.org_id=d.org_id) AS zmh_submission
        FROM audit_log e JOIN deliveries d ON d.id=e.target_id AND d.org_id=e.org_id
        JOIN routes r ON r.id=d.route_id AND r.org_id=d.org_id
        WHERE e.org_id=? AND e.action IN ('YICHE_SEND_RESULT','ZMH_SEND_RESULT','YICHE_CALLBACK','ZMH_CALLBACK')
          AND d.first_sent_at IS NOT NULL''', (org_id,)):
        detail = _detail(row['detail'])
        snapshot = _detail(row['route_snapshot'])
        if any(snapshot.get(key) != row[key] for key in ('buyer_id','receiver_id','media_id','adapter_version')):
            continue
        yiche = (row['yiche_submission']
                 and (row['action'] == 'YICHE_CALLBACK' or (row['action'] == 'YICHE_SEND_RESULT' and detail.get('outcome') == 'ACKED')) and row['receiver_id'] == row['media_id'])
        agency = (row['zmh_submission']
                  and (row['action'] == 'ZMH_CALLBACK' or (row['action'] == 'ZMH_SEND_RESULT' and detail.get('status') == 'ACKED')) and row['adapter_version'] == 'zhemanhui-v1')
        if agency and snapshot.get('send_environment', 'TEST') != control(conn, org_id, snapshot['id'])['environment']:
            agency = False
        if yiche or agency:
            key = (row['buyer_id'], row['sale_project'])
            outbound[key] = max(outbound.get(key, ''), row['created_at'])
    for proof in conn.execute('''SELECT p.created_at,e.detail
        FROM audit_log e JOIN zmh_submissions p ON p.org_id=e.org_id AND p.id=e.target_id
        WHERE e.org_id=? AND e.action='ZMH_PROBE_BINDING' AND p.delivery_id IS NULL
          AND p.send_status='ACKED' ''', (org_id,)):
        binding = _detail(proof['detail'])
        row = conn.execute('''SELECT r.id AS route_id,r.buyer_id,r.receiver_id,r.media_id,r.adapter_version
            FROM routes r JOIN zmh_routes z ON z.org_id=r.org_id AND z.route_id=r.id
            WHERE r.org_id=? AND r.id=? AND EXISTS(SELECT 1 FROM partner_projects pp
                WHERE pp.org_id=r.org_id AND pp.partner_id=r.buyer_id AND pp.project='MEMBER')''',
                           (org_id, binding.get('route_id'))).fetchone()
        if not row or binding.get('project') != 'MEMBER' or row['adapter_version'] != 'zhemanhui-v1':
            continue
        if row['receiver_id'] != row['buyer_id'] or any(binding.get(key) != row[key] for key in (
                'route_id', 'buyer_id', 'receiver_id', 'media_id', 'adapter_version')):
            continue
        if control(conn, org_id, row['route_id'])['environment'] != 'TEST':
            continue
        key = (row['buyer_id'], 'MEMBER')
        outbound[key] = max(outbound.get(key, ''), proof['created_at'])
    return inbound, outbound
