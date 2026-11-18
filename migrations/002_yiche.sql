ALTER TABLE leads ADD COLUMN yiche_cipher TEXT NOT NULL DEFAULT '';
ALTER TABLE demands ADD COLUMN yiche_config TEXT NOT NULL DEFAULT '{}';
INSERT INTO schema_version VALUES(2,datetime('now'));
