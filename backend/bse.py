"""BSE listed-securities master (list-of-scrips API).

Reconstructed from bytecode after the source was lost in the v2 cleanup —
only the functions the universe builder uses.

api.bseindia.com sits behind Akamai, which from 24 Sept 2026 rejects
anything whose TLS handshake does not look like a browser (HTTP 403
"Access Denied" regardless of headers or cookies - four days of failed
universe builds and corporate-action captures). Every BSE API call
therefore goes through curl_cffi impersonating Chrome; the plain
www.bseindia.com downloads (bhavcopies) are unaffected.
"""

import time

from curl_cffi import requests

BSE_HOME = "https://www.bseindia.com/"
SCRIP_MASTER_URL = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
IMPERSONATE = "chrome"  # curl_cffi's current Chrome TLS/HTTP2 fingerprint

HEADERS = {
    # Matches the fingerprint curl_cffi impersonates; Akamai scores TLS and
    # header consistency together, so the two must agree.
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
    "Referer": BSE_HOME,
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# What callers should catch around a BSE call (plus ValueError for bad JSON).
RequestError = (requests.exceptions.RequestException,)

# Suspended bonds still matter: they carry ratings/default history and may
# resume trading.
LISTED_STATUSES = ("Active", "Suspended")


def _to_float(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def new_session(handshake: bool = False) -> requests.Session:
    """A Chrome-shaped session for api.bseindia.com."""
    session = requests.Session(impersonate=IMPERSONATE)
    session.headers.update(HEADERS)
    if handshake:
        try:
            session.get(BSE_HOME, timeout=30)
        except requests.exceptions.RequestException:
            pass  # cookies are nice-to-have; the API works without
    return session


def _new_session() -> requests.Session:
    return new_session(handshake=True)


def fetch_scrip_master_raw(statuses=LISTED_STATUSES, timeout: int = 60,
                           retries: int = 6, backoff: float = 30.0) -> dict:
    """Fetch BSE's raw list-of-securities payload per status.

    Returns {status: [rows...]} exactly as the API delivered it (all debt
    segments, unfiltered) so the payload can be archived verbatim.
    Requires the homepage handshake for cookies.

    The API intermittently answers HTTP 200 with a non-JSON error page, so
    each status is retried with backoff on a fresh session before giving up.
    """
    session = _new_session()

    raw = {}
    for status in statuses:
        params = {"Group": "", "Scripcode": "", "industry": "",
                  "segment": "Debt", "status": status}
        last_exc = None
        resp = None
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(backoff * attempt)
                session = _new_session()  # stale cookies may be the problem
            try:
                resp = session.get(SCRIP_MASTER_URL, params=params,
                                   timeout=timeout)
                resp.raise_for_status()
                rows = resp.json()
            except (*RequestError, ValueError) as exc:
                last_exc = exc
                continue
            if isinstance(rows, list):
                raw[status] = rows
            break
        else:
            body = resp.text[:200] if resp is not None else ""
            raise RuntimeError(
                f"BSE scrip master ({status}) failed after "
                f"{retries + 1} attempts: {last_exc!r}; body={body!r}")
    return raw


def parse_scrip_master(raw: dict) -> list[dict]:
    """Normalize the raw payload to one 'Debentures and Bonds' record per
    ISIN, deduped so an ISIN seen in multiple statuses keeps the first
    (Active before Suspended, per fetch order)."""
    out = {}
    for status, rows in raw.items():
        for row in rows:
            if row.get("Segment") != "Debentures and Bonds":
                continue
            isin = (row.get("ISIN_NUMBER") or "").strip()
            if not isin or isin in out:
                continue
            out[isin] = {
                "isin": isin,
                "bse_status": (row.get("Status") or status).strip(),
                "bse_scrip_code": (str(row.get("SCRIP_CD"))
                                   if row.get("SCRIP_CD") else None),
                "bse_scrip_id": (row.get("scrip_id") or "").strip() or None,
                "issuer_name": (row.get("Issuer_Name")
                                or row.get("Scrip_Name") or "").strip() or None,
                "face_value": _to_float(row.get("FACE_VALUE")),
            }
    return list(out.values())


def fetch_scrip_master(statuses=LISTED_STATUSES, timeout: int = 60) -> list[dict]:
    """Fetch and parse the listed 'Debentures and Bonds' set in one step."""
    return parse_scrip_master(fetch_scrip_master_raw(statuses, timeout=timeout))
