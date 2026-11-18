CREATE TABLE yiche_submissions (
 delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),
 platform_id INTEGER NOT NULL,
 token_hash TEXT NOT NULL,
 callback_result INTEGER,
 callback_code TEXT
);
CREATE INDEX media_vehicle_series_lookup ON media_vehicle_items(org_id,media_id,series_id,factory_id);
INSERT INTO schema_version VALUES(5,datetime('now'));
