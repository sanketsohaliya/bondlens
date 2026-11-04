-- mld_premium must be double precision like every other analytics number:
-- numeric comes back as Decimal and fails the stored-vs-fresh equality check.
ALTER TABLE bond_analytics ALTER COLUMN mld_premium TYPE double precision USING mld_premium::double precision;
