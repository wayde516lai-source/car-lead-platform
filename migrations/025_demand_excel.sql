ALTER TABLE demand_sync_sources ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'QQ_DOC';
CREATE TABLE demand_excel_batches(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,actor_id TEXT NOT NULL,filename TEXT NOT NULL,uploaded_at TEXT NOT NULL,expires_at TEXT NOT NULL,payload_json TEXT NOT NULL,result_json TEXT,source_id TEXT REFERENCES demand_sync_sources(id));
INSERT INTO schema_version VALUES(25,datetime('now'));
