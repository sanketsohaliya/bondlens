-- Market-linked debentures carry an explicit flag so every surface (rows,
-- alerts, auto-buy) can treat them alike without re-deriving it.
ALTER TABLE bond_analytics ADD COLUMN IF NOT EXISTS is_mld boolean NOT NULL DEFAULT false;
