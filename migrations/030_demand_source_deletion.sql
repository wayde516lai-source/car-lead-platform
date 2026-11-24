-- 目的：标记误填需求来源已删除，保留来源原文、交付及审计历史
-- 兼容：旧来源默认未删除，删除时间为UTC ISO文本
-- 回退：旧代码不识别删除标记，回退前须保留停同步和需求停发保护
ALTER TABLE demand_sync_sources ADD COLUMN deleted_at TEXT;
ALTER TABLE demand_sync_sources ADD COLUMN deleted_by TEXT;
INSERT INTO schema_version VALUES(30,datetime('now'));
