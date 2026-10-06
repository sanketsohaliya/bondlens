-- Rules no longer carry a user-typed name: one label is derived from the
-- rule's own fields ("Ask YTM >= 15%, Rating >= BBB-, Maturity >= 6mo") and
-- shown identically in the panel, the bell, the hits list and the push.
-- Each fired alert stores the label it was judged by, so hits keep
-- reading the same after the rule is edited or deleted.
ALTER TABLE alert_rules DROP COLUMN name;
ALTER TABLE opportunity_alerts ADD COLUMN rule_label text;
