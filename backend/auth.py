"""Google sign-in and session handling.

Flow: the frontend obtains a Google ID token via Google Identity
Services (button / One Tap), POSTs it to /api/auth/google, and we
verify it against Google's JWKS (google-auth does the fetching and
caching). We then upsert the user row and issue our own stateless
session JWT in an httpOnly cookie — no server-side session store, so
restarts don't log anyone out. Who may sign in is controlled on the
Google side (consent-screen Testing mode + test-user list).

Env:
  GOOGLE_CLIENT_ID  OAuth Web client ID (aud claim of incoming tokens)
  SESSION_SECRET    HMAC key for our session JWTs
"""
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Cookie, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

SESSION_COOKIE = "bl_session"
SESSION_DAYS = 30
# Secure cookies break plain-http localhost; enable for the public deploy.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "") == "1"


def verify_google_token(credential: str) -> dict[str, Any]:
    """Validate a Google ID token; returns its claims or raises 401/403."""
    if not GOOGLE_CLIENT_ID or not SESSION_SECRET:
        raise HTTPException(status_code=503, detail="Auth is not configured")
    try:
        claims = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google token") from None
    if not claims.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google email not verified")
    return dict(claims)


def mint_session(user_id: int) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": now + timedelta(days=SESSION_DAYS)},
        SESSION_SECRET,
        algorithm="HS256",
    )


def parse_session(token: str) -> int | None:
    """User id from a session JWT, or None if missing/invalid/expired."""
    if not token or not SESSION_SECRET:
        return None
    try:
        claims = jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
        return int(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


KITE_STATE_MINUTES = 10


def mint_kite_state(user_id: int) -> str:
    """Short-lived token identifying the user through the Kite login
    redirect. Needed because the app serves multiple domains but Kite
    allows one redirect URL: a user starting the login on domain B
    lands on domain A's callback, where their (host-scoped) session
    cookie is absent."""
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": str(user_id), "purpose": "kite_state",
         "iat": now, "exp": now + timedelta(minutes=KITE_STATE_MINUTES)},
        SESSION_SECRET,
        algorithm="HS256",
    )


def parse_kite_state(token: str) -> int | None:
    """User id from a kite state token, or None if invalid/expired/
    not a state token (a session JWT must not pass as state)."""
    if not token or not SESSION_SECRET:
        return None
    try:
        claims = jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
        if claims.get("purpose") != "kite_state":
            return None
        return int(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def require_user(bl_session: str | None = Cookie(default=None)) -> int:
    """FastAPI dependency: the signed-in user's id, else 401."""
    user_id = parse_session(bl_session or "")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user_id
