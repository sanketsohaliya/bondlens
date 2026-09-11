-- Earliest payment/record date after the payout snapshot. The buyer total is
-- provably unchanged until then, so the read path keeps serving the snapshot
-- past midnight instead of blanking the Explore Payout column until 07:00.
-- NULL (including rows computed before this migration) means "no boundary
-- known" and is treated as not stale; the next compute_analytics fills it.
ALTER TABLE bond_analytics
    ADD COLUMN payout_valid_until date;
