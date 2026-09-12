ALTER TABLE bond_analytics ADD COLUMN valuation jsonb;

CREATE TABLE operational_alert_state (
    name text PRIMARY KEY,
    active boolean NOT NULL DEFAULT false,
    fingerprint text NOT NULL DEFAULT '',
    last_notified_at timestamptz
);
