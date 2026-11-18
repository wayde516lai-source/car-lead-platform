ALTER TABLE partners ADD COLUMN cooperation_price_fen INTEGER CHECK(cooperation_price_fen>=0);
ALTER TABLE partners ADD COLUMN cooperation_note TEXT NOT NULL DEFAULT '';
ALTER TABLE channels ADD COLUMN cooperation_price_fen INTEGER CHECK(cooperation_price_fen>=0);
ALTER TABLE channels ADD COLUMN cooperation_note TEXT NOT NULL DEFAULT '';
INSERT INTO schema_version VALUES(9,datetime('now'));
