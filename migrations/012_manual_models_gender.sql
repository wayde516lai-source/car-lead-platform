CREATE TABLE manual_vehicle_entries (
 org_id TEXT NOT NULL,
 series_id TEXT PRIMARY KEY REFERENCES catalog(series_id),
 all_series INTEGER NOT NULL DEFAULT 0 CHECK(all_series IN (0,1))
);
ALTER TABLE leads ADD COLUMN gender TEXT NOT NULL DEFAULT 'UNKNOWN'
 CHECK(gender IN ('UNKNOWN','MALE','FEMALE'));
UPDATE leads SET gender=CASE customer_title WHEN '先生' THEN 'MALE' WHEN '女士' THEN 'FEMALE' ELSE 'UNKNOWN' END;
INSERT INTO schema_version VALUES(12,datetime('now'));
