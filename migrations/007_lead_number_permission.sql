UPDATE lead_field_permissions
SET visible_fields=json_insert(visible_fields,'$[#]','lead_no')
WHERE EXISTS (SELECT 1 FROM json_each(visible_fields) WHERE value='customer')
  AND NOT EXISTS (SELECT 1 FROM json_each(visible_fields) WHERE value='lead_no');
INSERT INTO schema_version VALUES(7,datetime('now'));
