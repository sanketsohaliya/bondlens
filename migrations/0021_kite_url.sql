-- Zerodha Kite chart deep link, resolved once per bond by compute_analytics
-- (a cached instrument-list lookup) so explorer rows can link to Kite
-- directly. NULL when Kite does not list the bond or the instrument list was
-- unreachable during that refresh; bond_detail falls back to a live lookup.
ALTER TABLE bond_analytics
    ADD COLUMN kite_url text;
