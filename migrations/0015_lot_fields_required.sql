-- Lots without a price or purchase date made P&L and income history
-- ambiguous; all existing rows already carry both (verified in prod),
-- so make them hard requirements.
ALTER TABLE user_portfolio_lots
    ALTER COLUMN price SET NOT NULL,
    ALTER COLUMN purchase_date SET NOT NULL;
