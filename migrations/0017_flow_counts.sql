-- Cashflow counts for the explorer's Payout column ("20/32 left") —
-- filled by compute_analytics from the normalized schedule.
ALTER TABLE bond_analytics
    ADD COLUMN future_flows integer,
    ADD COLUMN total_flows integer;
