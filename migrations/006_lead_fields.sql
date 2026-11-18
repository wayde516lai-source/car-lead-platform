ALTER TABLE leads ADD COLUMN customer_title TEXT NOT NULL DEFAULT '' CHECK(customer_title IN ('','先生','女士'));
CREATE TABLE lead_field_permissions (
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 visible_fields TEXT NOT NULL
);
INSERT INTO schema_version VALUES(6,datetime('now'));
