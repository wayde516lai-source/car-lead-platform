-- 目的：页面管理软米会员路由的测试或正式环境及配置版本
-- 兼容：旧路由默认仍走测试接口，不改变既有发送开关
-- 恢复：新增表可保留，不恢复旧数据库覆盖后续业务
CREATE TABLE route_send_controls(route_id TEXT PRIMARY KEY REFERENCES routes(id),org_id TEXT NOT NULL,environment TEXT NOT NULL DEFAULT 'TEST' CHECK(environment IN ('TEST','PRODUCTION')),version INTEGER NOT NULL DEFAULT 1);
INSERT INTO schema_version VALUES(31,datetime('now'));
