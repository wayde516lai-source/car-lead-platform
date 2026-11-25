-- 目的：按组织和最终媒体管理车型及城市API和同步历史
-- 兼容：复用原车型明细，新增配置不自动启用，不修改历史交付
-- 回退：新增表可保留，旧版不会读取城市库及新API配置
CREATE TABLE media_dictionary_sources (
    org_id TEXT NOT NULL,
    media_id TEXT NOT NULL REFERENCES partners(id),
    kind TEXT NOT NULL CHECK(kind IN ('vehicles','cities')),
    endpoint TEXT NOT NULL,
    token_cipher TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    schedule_hour INTEGER NOT NULL DEFAULT 0 CHECK(schedule_hour BETWEEN 0 AND 23),
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(org_id,media_id,kind)
);
CREATE TABLE media_city_items (
    org_id TEXT NOT NULL,
    media_id TEXT NOT NULL REFERENCES partners(id),
    city_code TEXT NOT NULL,
    city_name TEXT NOT NULL,
    parent_code TEXT NOT NULL DEFAULT '',
    parent_name TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(org_id,media_id,city_code)
);
CREATE TABLE media_dictionary_runs (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    media_id TEXT NOT NULL REFERENCES partners(id),
    kind TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    sync_day TEXT NOT NULL,
    trigger_kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL CHECK(state IN ('RUNNING','SUCCESS','FAILED')),
    pages INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX media_dictionary_history ON media_dictionary_runs(org_id,media_id,kind,started_at DESC);
INSERT INTO schema_version VALUES(31,datetime('now'));
