-- Opportunity alerts: saved screens + ask thresholds that a watcher
-- evaluates against every Kite depth tick during market hours, holding
-- exit targets on the bid side, the dedupe/latency ledger, the yield ->
-- price threshold cache that keeps the tick path free of valuation
-- maths, and the per-user ntfy topic the pushes go to.

CREATE TABLE alert_rules (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         text   NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    -- Explore filter params that describe the candidate set (static
    -- filters only: rating, maturity, sector...; trade-history filters
    -- are refused by the API because a never-traded bond with a great
    -- resting ask is exactly what must not be missed).
    filters      jsonb  NOT NULL DEFAULT '{}'::jsonb,
    min_ask_ytm  numeric,          -- % ; fires when YTM @ best ask >= this
    max_ticket   numeric,          -- INR ; best-ask price x qty <= this
    max_price    numeric,          -- INR per unit ; best-ask price <= this
    min_qty      numeric,          -- units at the best ask, default 1
    enabled      boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    last_hit_at  timestamptz,
    CHECK (min_ask_ytm IS NOT NULL OR max_price IS NOT NULL)
);
CREATE INDEX alert_rules_user ON alert_rules (user_id, enabled);

CREATE TABLE exit_targets (
    user_id        bigint   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    isin           char(12) NOT NULL,
    min_bid_price  numeric,        -- fires when best bid >= this
    max_bid_ytm    numeric,        -- fires when YTM @ best bid <= this
    enabled        boolean NOT NULL DEFAULT true,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, isin),
    CHECK (min_bid_price IS NOT NULL OR max_bid_ytm IS NOT NULL)
);

-- Every fired alert: the dedupe ledger (one per source/bond/day unless the
-- quote improves materially) and the latency record (tick -> push accepted).
CREATE TABLE opportunity_alerts (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    bigint   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source     text     NOT NULL,     -- 'rule:<id>' | 'target' | 'nudge'
    isin       char(12) NOT NULL,
    side       text     NOT NULL CHECK (side IN ('ask', 'bid')),
    price      numeric  NOT NULL,
    qty        numeric,
    ytm        numeric,
    exchange   text,
    trade_day  date     NOT NULL,
    tick_at    timestamptz,
    fired_at   timestamptz NOT NULL DEFAULT now(),
    pushed_at  timestamptz,
    push_ms    integer
);
CREATE INDEX opportunity_alerts_day ON opportunity_alerts (user_id, trade_day, isin, source);

-- yield -> dirty price per bond for a valuation date. Shared by every rule
-- and user that asks for the same yield; invalidated by date, calculator
-- version or a change in the bond's terms/cashflows (schedule_version is a
-- fingerprint of the raw inputs). price NULL = no finite price reaches
-- that yield (unusable schedule / out of range).
CREATE TABLE price_thresholds (
    isin                 char(12) NOT NULL,
    ytm                  numeric  NOT NULL,
    valuation_date       date     NOT NULL,
    schedule_version     text     NOT NULL,
    calculation_version  text     NOT NULL,
    price                numeric,
    computed_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (isin, ytm, valuation_date)
);

ALTER TABLE users
    ADD COLUMN ntfy_topic text CHECK (ntfy_topic ~ '^[A-Za-z0-9_-]{6,64}$');

-- Single-row liveness record for the watcher service (health.py reads it).
CREATE TABLE watcher_heartbeat (
    id                 int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    beat_at            timestamptz NOT NULL,
    users              int NOT NULL DEFAULT 0,
    tokens             int NOT NULL DEFAULT 0,
    push_p50_ms        integer,
    push_p99_ms        integer,
    thresholds_ready   boolean NOT NULL DEFAULT false,
    note               text
);
