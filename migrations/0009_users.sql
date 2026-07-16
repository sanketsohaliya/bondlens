-- Google-authenticated users and their per-user bond lists
-- (watchlist/holdings moved from browser localStorage to the account).
CREATE TABLE users (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    google_sub  text UNIQUE NOT NULL,
    email       text NOT NULL,
    name        text,
    picture     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_login  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_lists (
    user_id   bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    list      text NOT NULL CHECK (list IN ('watchlist', 'holdings')),
    isin      char(12) NOT NULL,
    added_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, list, isin)
);
