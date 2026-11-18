ALTER TABLE partners ADD COLUMN cooperation_project TEXT CHECK(cooperation_project IN ('KA','MEMBER'));
UPDATE partners SET cooperation_project='MEMBER' WHERE role IN ('MEDIA','AGENCY') AND id IN (SELECT media_id FROM demand_sync_sources WHERE sale_project='MEMBER');
UPDATE partners SET cooperation_project='MEMBER' WHERE role='MEDIA' AND cooperation_project IS NULL AND id IN (SELECT media_id FROM lead_sale_history WHERE project='MEMBER');
INSERT INTO schema_version VALUES(18,datetime('now'));
