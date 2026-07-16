"""Session JWT and allowlist unit tests (Google verification is mocked
elsewhere — network is never touched here)."""
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from backend import auth


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_SECRET", "test-secret")


def test_session_round_trip():
    token = auth.mint_session(42)
    assert auth.parse_session(token) == 42


def test_tampered_session_rejected():
    token = auth.mint_session(42)
    other = pyjwt.encode({"sub": "99"}, "wrong-secret", algorithm="HS256")
    assert auth.parse_session(other) is None
    assert auth.parse_session(token[:-2]) is None
    assert auth.parse_session("") is None


def test_expired_session_rejected():
    past = datetime.now(UTC) - timedelta(days=1)
    token = pyjwt.encode(
        {"sub": "42", "exp": past}, "test-secret", algorithm="HS256"
    )
    assert auth.parse_session(token) is None


def test_require_user():
    assert auth.require_user(auth.mint_session(7)) == 7
    with pytest.raises(HTTPException) as e:
        auth.require_user(None)
    assert e.value.status_code == 401
