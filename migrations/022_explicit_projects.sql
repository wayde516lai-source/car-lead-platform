DELETE FROM partner_projects
WHERE matching_mode='MANUAL' AND version=1
AND project!=(SELECT cooperation_project FROM partners p WHERE p.id=partner_projects.partner_id AND p.org_id=partner_projects.org_id)
AND NOT EXISTS(SELECT 1 FROM demands d WHERE d.org_id=partner_projects.org_id AND d.buyer_id=partner_projects.partner_id AND d.sale_project=partner_projects.project)
AND NOT EXISTS(SELECT 1 FROM demand_sync_sources s WHERE s.org_id=partner_projects.org_id AND s.partner_id=partner_projects.partner_id AND s.sale_project=partner_projects.project)
AND NOT EXISTS(SELECT 1 FROM lead_sale_history h WHERE h.media_id=partner_projects.partner_id AND h.project=partner_projects.project);
INSERT INTO schema_version VALUES(22,datetime('now'));
