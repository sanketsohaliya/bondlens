"""Zerodha Kite Connect: per-user sessions, quotes and orders.

One Kite Connect app (KITE_API_KEY/KITE_API_SECRET env); each BondLens
user connects their *own* Zerodha account through the standard redirect
login, and their daily access token is stored against their user_id
(kite_sessions table + in-process cache). No market data or order ever
crosses users — everything here takes a user_id and runs under that
user's token.

Tokens expire every morning (~7:30 AM IST): any Kite 403 clears the
stored token so /api/kite/status flips to disconnected and the UI shows
the Connect button again. Depth fetching degrades to the exchange
scrapers in depth.py; order calls surface Zerodha's error message.
"""
import hashlib
import os
from typing import Any
from urllib.parse import urlencode

import requests

from . import db

API_ROOT = "https://api.kite.trade"
LOGIN_URL = "https://kite.zerodha.com/connect/login"

API_KEY = os.environ.get("KITE_API_KEY", "")
API_SECRET = os.environ.get("KITE_API_SECRET", "")
# The app serves multiple domains but Kite allows one redirect URL, so
# the callback bounces the user on to where they started — only ever to
# one of these origins (comma-separated, e.g. "https://a.com,https://b.com").
REDIRECT_ORIGINS = frozenset(
    o.strip().rstrip("/")
    for o in os.environ.get("KITE_REDIRECT_ORIGINS", "").split(",") if o.strip()
)

_tokens: dict[int, str] = {}  # user_id -> access_token (cache over DB)


class KiteError(Exception):
    """A Kite API error worth showing to the user (order rejections,
    margin shortfalls, bad params). `status` is a suggested HTTP code."""
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def configured() -> bool:
    return bool(API_KEY and API_SECRET)


def login_url(state: str | None = None, origin: str | None = None) -> str | None:
    """Kite login URL. `state`/`origin` ride through Kite's
    redirect_params and come back on the callback, so it can identify
    the user without a same-domain session cookie and send them back
    to the domain they started on."""
    if not configured():
        return None
    url = f"{LOGIN_URL}?v=3&api_key={API_KEY}"
    params = {}
    if state:
        params["state"] = state
    if origin:
        params["origin"] = origin
    if params:
        url += "&" + urlencode({"redirect_params": urlencode(params)})
    return url


def safe_origin(origin: str | None) -> str:
    """`origin` if allowlisted, else '' (relative redirect only —
    never an open redirect)."""
    if origin and origin.rstrip("/") in REDIRECT_ORIGINS:
        return origin.rstrip("/")
    return ""


def token_for(user_id: int) -> str | None:
    token = _tokens.get(user_id)
    if token is None:
        token = db.get_kite_token(user_id)
        if token:
            _tokens[user_id] = token
    return token


def clear_token(user_id: int) -> None:
    _tokens.pop(user_id, None)
    db.delete_kite_token(user_id)


def exchange_token(user_id: int, request_token: str) -> None:
    """Swap the redirect's request_token for a daily access token and
    store it for this user. Raises KiteError on failure."""
    if not configured():
        raise KiteError(503, "Kite is not configured")
    checksum = hashlib.sha256(
        (API_KEY + request_token + API_SECRET).encode()).hexdigest()
    resp = requests.post(
        f"{API_ROOT}/session/token", timeout=10,
        data={"api_key": API_KEY, "request_token": request_token,
              "checksum": checksum},
        headers={"X-Kite-Version": "3"},
    )
    payload = _payload(resp)
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not access_token:
        raise KiteError(502, "Kite returned no access token")
    _tokens[user_id] = access_token
    db.set_kite_token(user_id, access_token)


def _payload(resp: requests.Response) -> dict[str, Any] | list[Any]:
    """Unwrap Kite's {status, data|message} envelope; raise KiteError
    with Zerodha's own message on error responses."""
    try:
        body = resp.json()
    except ValueError:
        raise KiteError(502, f"Kite returned non-JSON (HTTP {resp.status_code})") from None
    if resp.ok and body.get("status") == "success":
        data = body.get("data")
        return data if data is not None else {}
    message = body.get("message") or f"Kite error (HTTP {resp.status_code})"
    # 409 (not 401) for Kite-session problems: the frontend treats any
    # non-/api/auth 401 as a dead BondLens session and flips to the
    # sign-in screen — a mid-day Kite token expiry must not look like that.
    raise KiteError(409 if resp.status_code == 403 else 400, message)


def _request(user_id: int, method: str, path: str,
             params: list[tuple[str, str]] | None = None,
             data: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
    """Authenticated Kite call under this user's token. A 403
    (TokenException — daily expiry / revoked) clears the stored token."""
    token = token_for(user_id)
    if token is None:
        raise KiteError(409, "Kite is not connected")
    resp = requests.request(
        method, API_ROOT + path, params=params, data=data, timeout=10,
        headers={"X-Kite-Version": "3",
                 "Authorization": f"token {API_KEY}:{token}"},
    )
    if resp.status_code == 403:
        clear_token(user_id)
    return _payload(resp)


# --- Quotes / depth ---------------------------------------------------------

def fetch_quotes(user_id: int, instruments: list[str]) -> dict[str, Any]:
    """Full quotes (with 5-level depth) for `instruments` like
    'NSE:96IIFL28-N5'. Returns {} on any failure — quotes must never
    5xx the depth endpoint; auth failures still clear the token."""
    if not instruments:
        return {}
    try:
        data = _request(user_id, "GET", "/quote",
                        params=[("i", i) for i in instruments])
        return data if isinstance(data, dict) else {}
    except KiteError:
        return {}


def parse_kite_depth(quote: dict[str, Any]) -> dict[str, Any] | None:
    """Kite quote payload -> DepthSide dict (None when fully empty).
    Empty book slots come through as all-zero entries."""
    book = quote.get("depth") or {}

    def levels(rows: Any) -> list[dict[str, Any]]:
        out = []
        for row in rows or []:
            price, qty = row.get("price"), row.get("quantity")
            if not price and not qty:
                continue
            orders = row.get("orders")
            out.append({"price": float(price) if price else None,
                        "qty": float(qty) if qty else None,
                        "orders": int(orders) if orders else None})
        return out

    bids, asks = levels(book.get("buy")), levels(book.get("sell"))
    num = lambda v: float(v) if v not in (None, "", 0, 0.0) else None  # noqa: E731
    volume = num(quote.get("volume"))
    if not bids and not asks and not volume:
        return None
    avg = num(quote.get("average_price"))
    ohlc = quote.get("ohlc") or {}
    return {
        "bids": bids[:5],
        "asks": asks[:5],
        "total_bid_qty": float(q) if (q := quote.get("buy_quantity")) else None,
        "total_ask_qty": float(q) if (q := quote.get("sell_quantity")) else None,
        "as_of": quote.get("timestamp"),
        "source": "kite",
        # Today's trade on this exchange - the piece the bhavcopy only
        # delivers after the close. Kite reports last_trade_time in IST.
        "last_price": num(quote.get("last_price")) if volume else None,
        "last_trade_at": (quote.get("last_trade_time") or None) if volume else None,
        "last_qty": num(quote.get("last_quantity")) if volume else None,
        "day_volume": volume,
        "day_avg_price": avg,
        "day_turnover": round(volume * avg, 2) if volume and avg else None,
        "ohlc": {k: float(v) for k, v in ohlc.items() if v not in (None, 0, 0.0)} or None,
    }


# --- Orders -----------------------------------------------------------------

def place_order(user_id: int, exchange: str, tradingsymbol: str,
                transaction_type: str, quantity: int, price: float) -> str:
    """LIMIT/CNC/DAY order (bonds are delivery instruments; market
    orders on illiquid books are dangerous, so LIMIT only). Returns the
    Kite order_id; raises KiteError with Zerodha's message otherwise."""
    data = _request(user_id, "POST", "/orders/regular", data={
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "transaction_type": transaction_type,
        "order_type": "LIMIT",
        "product": "CNC",
        "validity": "DAY",
        "quantity": str(quantity),
        "price": f"{price:.2f}",
    })
    order_id = data.get("order_id") if isinstance(data, dict) else None
    if not order_id:
        raise KiteError(502, "Kite returned no order id")
    return str(order_id)


def modify_order(user_id: int, order_id: str, quantity: int,
                 price: float) -> str:
    """Change an open order's quantity/limit price (side and product
    can't change on a Kite modify; stays LIMIT/DAY)."""
    data = _request(user_id, "PUT", f"/orders/regular/{order_id}", data={
        "order_type": "LIMIT",
        "validity": "DAY",
        "quantity": str(quantity),
        "price": f"{price:.2f}",
    })
    new_id = data.get("order_id") if isinstance(data, dict) else None
    if not new_id:
        raise KiteError(502, "Kite returned no order id")
    return str(new_id)


def token_is_live(user_id: int) -> bool:
    """Cheap liveness probe for a stored daily token (the watcher asks
    before opening ticker sockets). A dead token is cleared by _request."""
    _tokens.pop(user_id, None)  # another process may have refreshed it
    try:
        _request(user_id, "GET", "/user/profile")
        return True
    except KiteError:
        return False


def margins(user_id: int) -> dict[str, Any]:
    """Equity-segment funds snapshot (bonds trade in the equity
    segment): the four numbers the UI shows."""
    data = _request(user_id, "GET", "/user/margins/equity")
    if not isinstance(data, dict):
        data = {}
    available = data.get("available") or {}
    utilised = data.get("utilised") or {}
    return {
        "net": data.get("net"),
        "available_cash": available.get("cash"),
        "available_margin": available.get("live_balance"),
        "margin_used": utilised.get("debits"),
    }


_ORDER_FIELDS = ("order_id", "tradingsymbol", "exchange", "transaction_type",
                 "quantity", "filled_quantity", "price", "average_price",
                 "status", "status_message", "order_timestamp", "exchange_timestamp",
                 "exchange_update_timestamp", "variety")


def list_orders(user_id: int) -> list[dict[str, Any]]:
    """Today's orders (Kite only ever returns today's), slimmed down."""
    data = _request(user_id, "GET", "/orders")
    rows = data if isinstance(data, list) else []
    out = []
    for row in rows:
        slim = {f: row.get(f) for f in _ORDER_FIELDS}
        ts = slim["order_timestamp"]
        slim["order_timestamp"] = str(ts) if ts is not None else None
        out.append(slim)
    return out


def cancel_order(user_id: int, order_id: str) -> None:
    _request(user_id, "DELETE", f"/orders/regular/{order_id}")
