-- Watchlist and holdings moved to browser localStorage (per-visitor lists;
-- no shared server state ahead of the public release).
DROP TABLE IF EXISTS watchlist;
DROP TABLE IF EXISTS holdings;
