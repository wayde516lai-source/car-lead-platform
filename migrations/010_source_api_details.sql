CREATE TABLE source_api_keys (
 id TEXT PRIMARY KEY,
 org_id TEXT NOT NULL,
 channel_id TEXT NOT NULL REFERENCES channels(id),
 secret_cipher TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL
);
CREATE TABLE source_lead_details (
 ingestion_id TEXT PRIMARY KEY REFERENCES ingestions(id),
 lead_id TEXT NOT NULL REFERENCES leads(id),
 details_cipher TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX source_lead_details_lookup ON source_lead_details(lead_id,created_at);
UPDATE lead_field_permissions
SET visible_fields=json_insert(visible_fields,'$[#]','source_status')
WHERE EXISTS (SELECT 1 FROM json_each(visible_fields) WHERE value='status')
AND NOT EXISTS (SELECT 1 FROM json_each(visible_fields) WHERE value='source_status');
INSERT INTO schema_version VALUES(10,datetime('now'));
