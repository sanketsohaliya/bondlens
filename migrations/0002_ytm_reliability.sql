-- YTM reliability flag: false = annualization artifact (near-maturity or
-- out-of-band), display with caution. Populated by compute_analytics.
ALTER TABLE bond_analytics ADD COLUMN IF NOT EXISTS ytm_reliable boolean;
