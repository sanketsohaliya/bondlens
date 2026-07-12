Silent failure mode. If a cron run fails or never fires, nothing tells you — you'd discover it by manually reading logs. A tiny healthcheck (e.g. daily query: "is eod_raw current? did last night's runs have ok=true?" → ntfy/Telegram/email ping) closes this.
nsdl_runs both ok=false — never investigated. Something in the NSDL harvest consistently reports fetch failures; worth reading the notes/logs to see if it's a handful of dead ISINs (fine) or a systematic issue.
WSL cron fragility. The whole platform assumes your Windows box + WSL are awake at 05:30/07:00/19:00. WSL can be shut down by Windows when idle. Mitigations: Windows Task Scheduler entry that pings WSL awake before cron times, or eventually a ₹300/month VPS.
Late universe additions lose history. parse_eod.py filters by today's universe: a bond added to bonds next month won't get its historical trades parsed until a manual --rebuild. Options: monthly --rebuild cron, or parse-time re-check of new ISINs. Same applies to parse_nsdl quarters, though NSDL is per-ISIN so less affected.
Log rotation — logs grows unbounded; trivial logrotate entry someday.

pg_dump backup cron (protects everything else)
Healthcheck/alerting (makes all silent failures loud)
NSDL run failures investigation (5 min)
Weekly trailing --force refetch + monthly --rebuild (data correctness)
Parser test fixtures
The rest as they become relevant