ALTER TABLE demands ADD COLUMN external_demand_no TEXT;
INSERT INTO schema_version VALUES(26,datetime('now'));
