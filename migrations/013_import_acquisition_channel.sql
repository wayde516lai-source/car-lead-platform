ALTER TABLE leads ADD COLUMN acquisition_channel TEXT NOT NULL DEFAULT '';
INSERT INTO schema_version VALUES(13,datetime('now'));
