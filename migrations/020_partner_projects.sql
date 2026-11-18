CREATE TABLE partner_projects (
 org_id TEXT NOT NULL,
 partner_id TEXT NOT NULL REFERENCES partners(id),
 project TEXT NOT NULL CHECK(project IN ('MEMBER','KA')),
 matching_mode TEXT NOT NULL DEFAULT 'MANUAL' CHECK(matching_mode IN ('MANUAL','AUTO')),
 version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(org_id,partner_id,project)
);
INSERT INTO partner_projects(org_id,partner_id,project,matching_mode)
SELECT p.org_id,p.id,j.project,CASE WHEN EXISTS(SELECT 1 FROM settings s WHERE s.org_id=p.org_id AND s.auto_dispatch_enabled=1) AND EXISTS(SELECT 1 FROM demands d WHERE d.org_id=p.org_id AND d.buyer_id=p.id AND d.sale_project=j.project AND d.auto_enabled=1) THEN 'AUTO' ELSE 'MANUAL' END
FROM partners p CROSS JOIN (SELECT 'MEMBER' project UNION ALL SELECT 'KA') j WHERE p.role IN ('MEDIA','AGENCY');
INSERT INTO schema_version VALUES(20,datetime('now'));
