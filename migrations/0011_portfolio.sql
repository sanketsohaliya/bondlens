-- Portfolio lots replace the flat holdings list: each row is one purchase
-- (units at an optional price/date/platform). Existing holdings become
-- editable 1-unit lots; user_lists (holdings was its last use) is dropped.
CREATE TABLE user_portfolio_lots (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       bigint   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    isin          char(12) NOT NULL,
    units         numeric  NOT NULL CHECK (units > 0),
    price         numeric  CHECK (price > 0),
    purchase_date date,
    platform      text     CHECK (length(platform) <= 40),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX user_portfolio_lots_user ON user_portfolio_lots (user_id, isin);

INSERT INTO user_portfolio_lots (user_id, isin, units, created_at)
    SELECT user_id, isin, 1, added_at FROM user_lists WHERE list = 'holdings';

DROP TABLE user_lists;
