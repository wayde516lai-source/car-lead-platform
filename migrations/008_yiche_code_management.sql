CREATE TABLE yiche_code_overrides (
 org_id TEXT NOT NULL,
 code TEXT NOT NULL,
 status TEXT CHECK(status IN ('VALID','INVALID')),
 description TEXT NOT NULL,
 PRIMARY KEY(org_id, code)
);
ALTER TABLE yiche_submissions ADD COLUMN callback_description TEXT;
INSERT INTO schema_version VALUES(8,datetime('now'));
