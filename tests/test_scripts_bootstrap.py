"""Cron scripts are run as `python scripts/foo.py`, which puts scripts/
— not the repo root — on sys.path. Any script importing `backend` must
therefore carry the sys.path bootstrap header, or it crashes on every
cron run while passing under pytest (where the root IS on the path).
That exact failure shipped once (sweep_kite_fills); this guard makes it
impossible to repeat."""
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def test_backend_importing_scripts_have_bootstrap():
    missing = []
    for script in sorted(SCRIPTS.glob("*.py")):
        src = script.read_text(encoding="utf-8")
        if "from backend" in src or "import backend" in src:
            if "sys.path.insert" not in src:
                missing.append(script.name)
    assert not missing, (
        f"scripts missing the sys.path bootstrap header: {missing} — "
        "they will ImportError under cron (python scripts/<name>.py)"
    )


def test_scripts_dir_is_nonempty_sanity():
    # If the glob silently matched nothing, the guard above would pass
    # vacuously — make sure we are actually scanning the real directory.
    assert any(SCRIPTS.glob("*.py"))
