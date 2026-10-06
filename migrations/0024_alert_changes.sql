-- Per-user "alert rules changed" marker: the API touches it on every rule,
-- exit-target or topic write and the watcher polls it every few seconds, so
-- a saved rule is being watched within seconds instead of at the next
-- ten-minute refresh. Plus a post-push safety net on the ledger: the exact
-- yield computed after the push is checked against the rule, and a hit that
-- falls short records by how much (no bell row; the panel tags it).
CREATE TABLE alert_changes (
    user_id     bigint PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    changed_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE opportunity_alerts ADD COLUMN below_threshold numeric;
