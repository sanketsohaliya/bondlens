-- Bonds a user never wants auto-bought. Alerts still fire for them; the
-- auto-buy gate refuses first, before any money question, so the hit
-- reads "skipped - blacklisted". Per user, per ISIN, optional note.
CREATE TABLE auto_buy_blacklist (
    user_id    bigint   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    isin       char(12) NOT NULL,
    note       text     CHECK (note IS NULL OR length(note) <= 60),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, isin)
);
