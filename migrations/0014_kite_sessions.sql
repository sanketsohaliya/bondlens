-- Per-user Zerodha Kite Connect access tokens (daily; expire ~7:30 AM IST).
-- One row per BondLens user who has connected their own Kite account.
CREATE TABLE kite_sessions (
    user_id     bigint PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
