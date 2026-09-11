ALTER TABLE bond_analytics
    ADD COLUMN payout_total_snapshot double precision,
    ADD COLUMN payout_state_snapshot text,
    ADD COLUMN payout_as_of date;
