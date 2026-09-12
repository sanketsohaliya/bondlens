import pytest


@pytest.fixture(autouse=True)
def offline_kite(monkeypatch):
    """compute.build_rows resolves a Kite deep link per bond, which downloads
    Kite's instrument CSV on first use. Keep the unit suite offline and
    deterministic; tests that care about the link patch kite_url themselves."""
    from backend import kite

    monkeypatch.setattr(kite, "kite_url", lambda *args, **kwargs: None)
