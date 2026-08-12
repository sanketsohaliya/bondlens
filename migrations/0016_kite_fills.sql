-- Ledger of Kite orders already reconciled into the portfolio; the PK
-- makes fill processing idempotent across order polls and the
-- market-hours sweep job.
CREATE TABLE kite_fills (
    user_id      bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id     text   NOT NULL,
    lot_id       bigint,  -- NULL = processed but no lot (sell / non-bond / no fill)
    processed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, order_id)
);
