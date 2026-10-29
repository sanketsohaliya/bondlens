-- Ask conditions become: minimum yield (required) and a minimum ticket
-- (price x qty on offer). Max price, max ticket and min units go: price is
-- the yield, ticket ceilings belong to the auto-buy budget, and a unit
-- floor is a ticket floor. Rules keep their yield and filters.
ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS min_ticket numeric;
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_check;
ALTER TABLE alert_rules DROP COLUMN IF EXISTS max_ticket;
ALTER TABLE alert_rules DROP COLUMN IF EXISTS max_price;
ALTER TABLE alert_rules DROP COLUMN IF EXISTS min_qty;
ALTER TABLE alert_rules ALTER COLUMN min_ask_ytm SET NOT NULL;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_min_ticket_positive CHECK (min_ticket IS NULL OR min_ticket > 0);
