ALTER TABLE deliveries ADD COLUMN sale_project TEXT NOT NULL DEFAULT 'UNKNOWN'
 CHECK(sale_project IN ('KA','MEMBER','UNKNOWN'));
INSERT INTO schema_version VALUES(14,datetime('now'));
