-- Retail close on the bond's previous trading day, so the screener can
-- show whether the price moved up or down vs the latest trade.
ALTER TABLE bond_analytics ADD COLUMN IF NOT EXISTS prev_ltp double precision;
