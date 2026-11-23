-- 目的：保存KA需求接口配置及每日提交上限，月度量仅展示
-- 兼容：既有文档和Excel需求保持原配额口径，新增字段默认空
-- 恢复：旧代码不理解API来源，回滚须暂停该来源的新分发
CREATE TABLE ka_demand_sources(source_id TEXT PRIMARY KEY REFERENCES demand_sync_sources(id),org_id TEXT NOT NULL,auth_mode TEXT NOT NULL DEFAULT 'UNCONFIRMED' CHECK(auth_mode IN ('UNCONFIRMED','NONE','BEARER')),token_cipher TEXT NOT NULL DEFAULT '');
ALTER TABLE demands ADD COLUMN api_submit_limit INTEGER CHECK(api_submit_limit IS NULL OR api_submit_limit>=0);
ALTER TABLE demands ADD COLUMN api_quota_day TEXT NOT NULL DEFAULT '';
ALTER TABLE demands ADD COLUMN api_monthly_limit INTEGER;
ALTER TABLE demands ADD COLUMN api_vehicles TEXT NOT NULL DEFAULT '[]';
INSERT INTO schema_version VALUES(29,datetime('now'));
