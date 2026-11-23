-- 目的：更换链接时归档旧来源，保留原表及历史交付身份
-- 兼容：新增可空字段，旧记录默认仍是当前来源
-- 恢复：保留归档关系，不以旧数据库覆盖后续业务
ALTER TABLE demand_sync_sources ADD COLUMN replaced_by TEXT REFERENCES demand_sync_sources(id);
CREATE INDEX demand_sync_sources_replaced ON demand_sync_sources(org_id,replaced_by);
INSERT INTO schema_version VALUES(27,datetime('now'));
