CREATE TABLE agency_final_media (
 org_id TEXT NOT NULL,
 agency_id TEXT NOT NULL REFERENCES partners(id),
 media_id TEXT NOT NULL REFERENCES partners(id),
 PRIMARY KEY(org_id,agency_id,media_id)
);
INSERT OR IGNORE INTO agency_final_media(org_id,agency_id,media_id)
SELECT r.org_id,r.buyer_id,r.media_id FROM routes r JOIN partners p ON p.id=r.buyer_id AND p.org_id=r.org_id WHERE p.role='AGENCY';
INSERT INTO schema_version VALUES(21,datetime('now'));
