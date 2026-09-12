"""Nightly pg_dump of the bondlens DB to the Windows Desktop.

Writes to <dir>/bondlens.dump.tmp then renames over bondlens.dump, so a
crash mid-dump never destroys the previous good backup. Alerts via ntfy
on failure.

Cron: 08:00 daily inside the jobs container (Desktop bind-mounted at
/desktop; pg_dump 18 from the PGDG client package).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, health, operational_alerts, safe_errors  # noqa: E402

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/desktop"))
NAME = "bondlens.dump"


def main() -> int:
    tmp = BACKUP_DIR / f"{NAME}.tmp"
    final = BACKUP_DIR / NAME
    t0 = time.time()
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        connection = {key: str(value) for key, value in conninfo_to_dict(db.DATABASE_URL).items()
                      if value is not None}
        password = connection.pop("password", None)
        env = dict(os.environ)
        if password is not None:
            env["PGPASSWORD"] = password
        with open(tmp, "wb") as out:
            subprocess.run(
                ["pg_dump", "--format=custom", "--dbname", make_conninfo(**connection)],
                stdout=out, stderr=subprocess.PIPE, check=True, timeout=1800, env=env)
        tmp.replace(final)
        size_mb = final.stat().st_size / 1e6
        print(f"backup OK: {final} ({size_mb:.0f} MB in {time.time()-t0:.0f}s)")
        try:
            operational_alerts.report("backup", [], health.notify)
        except Exception as alert_exc:
            print(f"backup recovery alert failed: {safe_errors.error_summary(alert_exc)}")
        return 0
    except Exception as exc:  # noqa: BLE001 - report anything
        msg = safe_errors.error_summary(exc)
        print(f"backup FAILED: {msg}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            print(f"backup temporary-file cleanup failed: {safe_errors.error_summary(cleanup_exc)}")
        try:
            operational_alerts.report("backup", [msg], health.notify)
        except Exception as notify_exc:  # noqa: BLE001
            print(f"(alert also failed: {safe_errors.error_summary(notify_exc)})")
        return 1
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
