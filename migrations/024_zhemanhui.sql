CREATE TABLE zmh_routes(route_id TEXT PRIMARY KEY REFERENCES routes(id),org_id TEXT NOT NULL,callback_url TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE zmh_mappings(org_id TEXT NOT NULL,route_id TEXT NOT NULL REFERENCES routes(id),kind TEXT NOT NULL CHECK(kind IN ('city','series')),local_value TEXT NOT NULL,remote_id TEXT NOT NULL,PRIMARY KEY(org_id,route_id,kind,local_value));
CREATE TABLE zmh_submissions(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,delivery_id TEXT UNIQUE REFERENCES deliveries(id),token_hash TEXT NOT NULL,created_at TEXT NOT NULL,send_status TEXT NOT NULL DEFAULT 'UNKNOWN',result_code TEXT,code TEXT,validity TEXT NOT NULL DEFAULT 'PENDING',description TEXT NOT NULL DEFAULT '',callback_at TEXT);
INSERT INTO schema_version VALUES(24,datetime('now'));
