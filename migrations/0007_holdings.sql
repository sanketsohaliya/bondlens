-- Holdings: bonds the user actually owns (simple flag, like watchlist).
CREATE TABLE holdings (
    isin text NOT NULL PRIMARY KEY REFERENCES bonds(isin),
    added_at timestamp with time zone DEFAULT now() NOT NULL
);
