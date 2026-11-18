# 供应商线索 API 接入

## 绑定规则
在「合作与渠道 → 线索采购」创建供应商后，列表「渠道号」为平台唯一编号。
点击该行「API 对接」生成凭证，凭证绑定渠道和工作空间。名称只用于展示。
每次请求须携带 channel_id，服务端同时检查签名、渠道归属、启用状态和合同有效期。
重置凭证后旧凭证立即失效。不同渠道分别建立记录和凭证。

## 签名
POST /open/v1/leads，Content-Type 为 application/json。

请求头包括 X-Key-Id、X-Timestamp（Unix 秒）、X-Nonce（随机唯一值）、X-Signature。
时间戳与服务端相差不超过 300 秒。重试须换 nonce，保留来源记录编号。
签名为 Base64(HMAC-SHA256(secret, message))。
message 为以下五项通过换行符连接，末尾无换行。

1. POST
2. /open/v1/leads
3. X-Timestamp
4. X-Nonce
5. 实际请求体原始字节的 SHA256 十六进制摘要

密钥保管在来源方服务端。

## 请求示例
一批支持 1 至 100 条。单条可省略 items，直接传字段对象。
channel_id 可放在批次顶层或每条记录中，必须匹配凭证。

```json
{
  "channel_id": "从平台列表复制的渠道号",
  "items": [{
    "source_record_id": "41992",
    "demand_no": "103817",
    "channel_name": "来源方渠道名称",
    "series_name": "宝马 X5",
    "brand_name": "宝马",
    "city": "上海市",
    "phone": "13900009999",
    "name": "测试客户",
    "report_status": "SUCCESS",
    "callback_status": "NONE",
    "lead_code": null,
    "reported_at": "2026-11-11T10:00:00+08:00",
    "callback_at": null,
    "collected_at": "2026-11-11T09:58:00+08:00",
    "series_id": "BMW_X5",
    "city_code": "310100",
    "recipients": [],
    "evidence_ref": ""
  }]
}
```

series_id 请使用实际平台字典编码。也支持按品牌与车系名称精确匹配现有平台字典。
未映射记录保留原始名称。采集时间可用 collected_at 传入。新记录默认进入可分发库存，具体分发仍校验车型、城市、有效期、媒体去重与配额。

## 13 个来源字段
| 中文 | API 字段 | 规则 |
|---|---|---|
| 编号 | source_record_id | 来源方唯一编号，必填，与平台线索编号分别存储 |
| 需求编号 | demand_no | 来源方原始需求编号 |
| 渠道名称 | channel_name | 原始名称，详情展示 |
| 车系名称 | series_name | 原始名称 |
| 品牌名称 | brand_name | 原始名称 |
| 城市 | city | 原始名称 |
| 手机号 | phone | 必填，校验并加密存储，界面按已有权限脱敏 |
| 客户姓名 | name | 加密存储，界面按已有权限脱敏 |
| 上报状态 | report_status | SUCCESS 上报成功、FAILED 上报失败、PENDING 未上报，省略为 UNKNOWN 未提供 |
| 回调状态 | callback_status | SUCCESS 成功、FAILED 失败、NONE 未回调，省略默认 NONE |
| 线索状态 | lead_code | 传数字 Code 码，无结果传 null |
| 上报时间 | reported_at | ISO 8601，建议携带 +08:00 或 Z |
| 回调时间 | callback_at | 无回调传 null |

也支持以上中文字段名。中文和英文同时传入必须一致。
无时区时间按北京时间解释。N/A、字符串 null 兼容视为无时间。
每个文本字段最多 200 字。

## 展示和去重
线索池供应商、获客渠道根据 API 凭证和平台渠道号关联。
来源方渠道名另存在来源详情，不覆盖供应商关系。
来源详情的线索状态根据当前工作空间易车 Code 字典转换为原因，未知码显示「未收录码」。
来源方结果在「查看链路」的来源原始记录中展示，各媒体回调保留在每笔分发记录中。
来源记录保留接收时的状态原因，各媒体分发结果另在链路中分别展示。
线索池已移除供应商、获客方式、线索状态和库存状态列。来源关系仍在后台按渠道号保存。

HTTP 202 表示批次已处理，请逐条检查返回的 items。
成功项包含 lead_id、status（CREATED 或 DUPLICATE）、duplicate。
失败项包含 status=REJECTED、code、message。
签名错误返回 HTTP 401，渠道不匹配在对应项返回 CHANNEL_FORBIDDEN。
同渠道、同 source_record_id 和相同内容重试返回原结果，不增加记录。
同一来源编号改动内容返回 IDEMPOTENCY_CONFLICT。当前接口用于入库，后续状态更新须另行约定独立事件接口。
相同手机号与车系命中平台去重规则时关联既有线索，并保留新的来源记录。
来源授权材料仍可随请求提供并记录。新入库线索按直接入库规则参与匹配。
如 API 同时传入 is_sold，请一并提供品牌、车型和城市；is_sold 为是或 true 时，sale_media_id、sale_project（KA 或 MEMBER）、sold_at 必填，为否或 false 时可留空。
