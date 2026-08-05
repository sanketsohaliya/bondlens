-- Multiple named watchlists per user. Existing starred bonds move into a
-- default "Watchlist 1" list; user_lists keeps holdings only.
CREATE TABLE user_watchlists (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       text   NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 40),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE user_watchlist_items (
    watchlist_id bigint   NOT NULL REFERENCES user_watchlists(id) ON DELETE CASCADE,
    isin         char(12) NOT NULL,
    added_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (watchlist_id, isin)
);

-- One "Watchlist 1" per user who has starred bonds; move their rows over.
INSERT INTO user_watchlists (user_id, name)
    SELECT DISTINCT user_id, 'Watchlist 1' FROM user_lists WHERE list = 'watchlist';

INSERT INTO user_watchlist_items (watchlist_id, isin, added_at)
    SELECT w.id, ul.isin, ul.added_at
    FROM user_lists ul
    JOIN user_watchlists w ON w.user_id = ul.user_id
    WHERE ul.list = 'watchlist';

DELETE FROM user_lists WHERE list = 'watchlist';

ALTER TABLE user_lists DROP CONSTRAINT user_lists_list_check;
ALTER TABLE user_lists ADD CONSTRAINT user_lists_list_check CHECK (list = 'holdings');
