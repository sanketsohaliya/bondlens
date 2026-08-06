-- Technical delays (investor account/IFSC issues, cured in days) are not
-- payment defaults. Track them as a separate soft-warning flag and stop
-- letting them brand sound bonds as defaulted.

ALTER TABLE bond_analytics
    ADD COLUMN IF NOT EXISTS tech_delay boolean NOT NULL DEFAULT false;

UPDATE bond_analytics
SET tech_delay = true
WHERE isin IN (SELECT isin FROM bond_technical_defaults);

-- Backfill ahead of the nightly recompute: clear in_default where the only
-- cause was a technical delay (no real default record, not rated D — D is
-- rank 19 in the shared RATING_ORDER scale).
UPDATE bond_analytics
SET in_default = false
WHERE in_default
  AND isin NOT IN (SELECT isin FROM bond_defaults)
  AND (rating_rank IS NULL OR rating_rank <> 19);
