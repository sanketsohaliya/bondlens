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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, health  # noqa: E402

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/desktop"))
NAME = "bondlens.dump"


def main() -> int:
    tmp = BACKUP_DIR / f"{NAME}.tmp"
    final = BACKUP_DIR / NAME
    t0 = time.time()
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as out:
            subprocess.run(
                ["pg_dump", "--format=custom", "--dbname", db.DATABASE_URL],
                stdout=out, stderr=subprocess.PIPE, check=True, timeout=1800)
        tmp.replace(final)
        size_mb = final.stat().st_size / 1e6
        print(f"backup OK: {final} ({size_mb:.0f} MB in {time.time()-t0:.0f}s)")
        return 0
    except Exception as exc:  # noqa: BLE001 - report anything
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail = ": " + exc.stderr.decode(errors="replace")[-300:]
        msg = f"{type(exc).__name__}: {exc}{detail}"
        print(f"backup FAILED: {msg}")
        tmp.unlink(missing_ok=True)
        try:
            health.notify("BondLens backup FAILED", msg)
        except Exception as notify_exc:  # noqa: BLE001
            print(f"(alert also failed: {notify_exc})")
        return 1
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
