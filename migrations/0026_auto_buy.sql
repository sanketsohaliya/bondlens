-- Auto-buy on alert hits. A rule can be armed for the day (auto_buy_until)
-- to place a LIMIT/CNC buy at the best ask when it fires, sized by a rupee
-- budget per hit and fenced by a per-rule daily cap, a per-user daily cap
-- and the account's available cash. Every attempt - placed, skipped or
-- rejected - is recorded; one order per bond per rule per day is enforced
-- by the unique key so two ticks can never double-buy.
ALTER TABLE alert_rules
    ADD COLUMN buy_budget     numeric CHECK (buy_budget > 0),
    ADD COLUMN buy_daily_cap  numeric CHECK (buy_daily_cap > 0),
    ADD COLUMN auto_buy_until timestamptz;

ALTER TABLE users ADD COLUMN auto_buy_daily_cap numeric CHECK (auto_buy_daily_cap > 0);

CREATE TABLE auto_orders (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        bigint   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rule_id        bigint   NOT NULL,          -- kept after the rule is deleted
    rule_label     text     NOT NULL,
    alert_id       bigint   REFERENCES opportunity_alerts(id) ON DELETE SET NULL,
    isin           char(12) NOT NULL,
    exchange       text     NOT NULL,
    tradingsymbol  text,
    qty            integer  NOT NULL,
    price          numeric  NOT NULL,
    value          numeric  NOT NULL,
    trade_day      date     NOT NULL,
    status         text     NOT NULL CHECK (status IN ('placed', 'skipped', 'rejected')),
    reason         text,
    kite_order_id  text,
    placed_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, rule_id, isin, trade_day)
);
CREATE INDEX auto_orders_day ON auto_orders (user_id, trade_day);
