ALTER TABLE leads ADD COLUMN direct_admission INTEGER NOT NULL DEFAULT 0;
CREATE TABLE lead_sale_history (
 id TEXT PRIMARY KEY,
 lead_id TEXT NOT NULL REFERENCES leads(id),
 ingestion_id TEXT NOT NULL UNIQUE REFERENCES ingestions(id),
 media_id TEXT NOT NULL REFERENCES partners(id),
 project TEXT NOT NULL CHECK(project IN ('KA','MEMBER')),
 sold_at TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX lead_sale_history_lookup ON lead_sale_history(lead_id,media_id,sold_at);
UPDATE lead_field_permissions SET visible_fields=(
 SELECT json_group_array(value) FROM json_each(visible_fields)
 WHERE value NOT IN ('supplier','channel','status','source_status')
);
UPDATE lead_field_permissions SET visible_fields=json_insert(visible_fields,'$[#]','phone')
WHERE EXISTS(SELECT 1 FROM json_each(visible_fields) WHERE value='customer');
UPDATE lead_field_permissions SET visible_fields=json_insert(visible_fields,'$[#]','is_sold','$[#]','sold_media','$[#]','sold_at','$[#]','project')
WHERE EXISTS(SELECT 1 FROM json_each(visible_fields) WHERE value='sales_count');
INSERT INTO schema_version VALUES(11,datetime('now'));
