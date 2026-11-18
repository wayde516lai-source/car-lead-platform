CREATE TABLE media_vehicle_sources (
 org_id TEXT NOT NULL,
 media_id TEXT NOT NULL REFERENCES partners(id),
 adapter TEXT NOT NULL CHECK (adapter IN ('yiche')),
 cid TEXT NOT NULL,
 key_cipher TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY (org_id, media_id)
);
CREATE TABLE media_vehicle_items (
 org_id TEXT NOT NULL,
 media_id TEXT NOT NULL REFERENCES partners(id),
 car_id INTEGER NOT NULL,
 car_name TEXT NOT NULL,
 series_id INTEGER NOT NULL,
 series_name TEXT NOT NULL,
 factory_id INTEGER NOT NULL,
 factory_name TEXT NOT NULL,
 brand_id INTEGER NOT NULL,
 brand_name TEXT NOT NULL,
 payload TEXT NOT NULL,
 PRIMARY KEY (org_id, media_id, car_id)
);
CREATE TABLE media_vehicle_runs (
 id TEXT PRIMARY KEY,
 org_id TEXT NOT NULL,
 media_id TEXT NOT NULL REFERENCES partners(id),
 sync_day TEXT NOT NULL,
 trigger_kind TEXT NOT NULL,
 started_at TEXT NOT NULL,
 finished_at TEXT,
 state TEXT NOT NULL CHECK (state IN ('RUNNING','SUCCESS','FAILED')),
 pages INTEGER NOT NULL DEFAULT 0,
 item_count INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX media_vehicle_runs_history ON media_vehicle_runs(org_id, media_id, started_at DESC);
INSERT INTO schema_version VALUES(3,datetime('now'));
