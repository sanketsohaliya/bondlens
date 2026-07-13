-- Retail turnover on the bond's previous trading day, so the screener
-- can show whether activity picked up or dropped off vs the latest day.
ALTER TABLE bond_analytics ADD COLUMN IF NOT EXISTS prev_turnover double precision;
