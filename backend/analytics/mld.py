"""Market-linked debentures (MLDs).

Their coupon is a formula settled against an index on an observation
date, so NSDL publishes a single zero-amount interest row "on maturity"
and no rate. Two signals identify them: NSDL's coupon basis (index /
commodity linked) and the agencies' PP-MLD rating prefix - the rating
catches the ones NSDL files as "Variable-Others".
"""
from __future__ import annotations

import re

_BASIS = re.compile(r"index.?linked|commodity", re.I)
_RATING = re.compile(r"\bPP-?\s?MLD\b", re.I)


def is_mld(coupon_basis: str | None, credit_rating: str | None) -> bool:
    return bool(_BASIS.search(coupon_basis or "") or _RATING.search(credit_rating or ""))
