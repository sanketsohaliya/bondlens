-- Zerodha Kite chart deep link, resolved once per bond by compute_analytics
-- (a cached instrument-list lookup) so explorer rows can link to Kite
-- directly. NULL when Kite does not list the bond or the instrument list was
-- unreachable during that refresh; bond_detail falls back to a live lookup.
--
-- IF NOT EXISTS: this file was first deployed as 0021_kite_url.sql and then
-- renumbered after colliding with 0021_valuation_context.sql, so production
-- already has the column under the old migration name.
ALTER TABLE bond_analytics
    ADD COLUMN IF NOT EXISTS kite_url text;
