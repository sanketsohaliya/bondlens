-- Split trade-volume filtering by segment: bond_analytics.turnover is the
-- retail (exchange) number; add the OTC/institutional counterpart.
ALTER TABLE bond_analytics ADD COLUMN IF NOT EXISTS otc_turnover double precision;
