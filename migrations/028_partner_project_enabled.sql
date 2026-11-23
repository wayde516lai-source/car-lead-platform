-- 目的：会员与KA独立启停，不再复用主体总开关
-- 兼容：已有项目默认启用，既有主体停用状态保持不变
-- 恢复：保留新增字段，不恢复旧数据库覆盖后续业务
ALTER TABLE partner_projects ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1));
INSERT INTO schema_version VALUES(28,datetime('now'));
