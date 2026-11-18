ALTER TABLE demand_sync_sources ADD COLUMN partner_id TEXT REFERENCES partners(id);
UPDATE demand_sync_sources SET partner_id=COALESCE((SELECT buyer_id FROM routes WHERE routes.id=demand_sync_sources.route_id),media_id);
INSERT INTO schema_version VALUES(19,datetime('now'));
