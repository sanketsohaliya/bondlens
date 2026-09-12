import subprocess

import pytest

from scripts import backup_db


@pytest.fixture(autouse=True)
def no_alert_state_database(monkeypatch):
    monkeypatch.setattr(backup_db.operational_alerts, "report",
                        lambda name, problems, sender: sender(f"BondLens {name}", "\n".join(problems)) if problems else False)


def test_backup_failure_never_logs_or_notifies_connection_secrets(tmp_path, monkeypatch, capsys):
    secret = "EXAMPLE_ONLY_PASSWORD"
    url = f"postgresql://example:{secret}@db/example"
    final = tmp_path / backup_db.NAME
    final.write_bytes(b"previous-good-backup")
    alerts = []
    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(backup_db.db, "DATABASE_URL", url)
    monkeypatch.setattr(backup_db.db, "close_pool", lambda: None)
    monkeypatch.setattr(backup_db.health, "notify", lambda *args: alerts.append(args))

    def fail(command, **kwargs):
        assert secret not in " ".join(command)
        assert kwargs["env"]["PGPASSWORD"] == secret
        raise subprocess.CalledProcessError(1, command, stderr=f"connection failed: {url}".encode())

    monkeypatch.setattr(backup_db.subprocess, "run", fail)
    assert backup_db.main() == 1
    output = capsys.readouterr().out + str(alerts)
    assert secret not in output and url not in output
    assert "CalledProcessError" in output
    assert final.read_bytes() == b"previous-good-backup"
    assert alerts


def test_notification_failure_is_also_redacted(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(backup_db.db, "close_pool", lambda: None)
    monkeypatch.setattr(backup_db.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("EXAMPLE_DB_SECRET")))
    monkeypatch.setattr(backup_db.health, "notify", lambda *args: (_ for _ in ()).throw(RuntimeError("EXAMPLE_TOPIC_SECRET")))
    assert backup_db.main() == 1
    output = capsys.readouterr().out
    assert "EXAMPLE_DB_SECRET" not in output and "EXAMPLE_TOPIC_SECRET" not in output
