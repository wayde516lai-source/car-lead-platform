ALTER TABLE media_vehicle_sources ADD COLUMN start_on TEXT NOT NULL DEFAULT '';
UPDATE media_vehicle_sources SET start_on=date('now','+8 hours','+1 day') WHERE start_on='';
INSERT INTO schema_version VALUES(4,datetime('now'));
