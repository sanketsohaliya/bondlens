-- Per-venue turnover for the screener's traded-value breakdown:
-- NSE and BSE retail figures from each exchange's latest trade day
-- (bond_analytics.turnover keeps the best single-venue number used
-- for sorting; otc_turnover arrived in 0003).
ALTER TABLE bond_analytics ADD COLUMN IF NOT EXISTS nse_turnover double precision;
ALTER TABLE bond_analytics ADD COLUMN IF NOT EXISTS bse_turnover double precision;
