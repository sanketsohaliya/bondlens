-- Per-unit premium NSDL has filed for a market-linked debenture (the
-- scenario payout beyond face); NULL when nothing beyond face is filed.
ALTER TABLE bond_analytics ADD COLUMN IF NOT EXISTS mld_premium double precision;
