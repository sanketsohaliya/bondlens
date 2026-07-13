"""Canonical cashflow schedule engine.

Builds a normalized per-unit cashflow stream for one bond from the parsed
NSDL tables (bond_cashflows + bond_terms). Handles the schedule shapes
observed across the full database:

  plain        level coupons + single full redemption at maturity
  staggered    partial redemptions stepping face value down over time
  zero_coupon  no interest flows; single redemption (issued at discount)
  cumulative   interest paid with principal at maturity ("On Maturity")
  perpetual    no redemption by design (bond_terms.perpetual)
  irregular    anything that resists classification

Amount normalization: NSDL publishes amounts inconsistently — usually per
unit of face value, sometimes at issue level (total ₹ across all units).
The engine reconciles the redemption total against face value to detect
and rescale issue-level schedules instead of assuming either convention.

Missing interest amounts are estimated from the coupon rate, face value
outstanding and the actual day-count between flows; every estimated flow
is flagged so downstream YTMs can be labeled exact vs estimated.
"""
import itertools
from dataclasses import dataclass, field
from datetime import date

from . import daycount

# A redemption total within this tolerance of face value counts as per-unit.
_FACE_TOL = 0.02
# Scale factors below this are treated as noise (e.g. redemption premium).
_MIN_SCALE = 5.0


@dataclass(slots=True)
class Flow:
    """One normalized per-unit cash flow."""
    event: str                       # 'interest' | 'redemption'
    due_date: date
    record_date: date | None = None
    amount: float | None = None   # ₹ per unit of face value
    estimated: bool = False          # amount was estimated, not published

    def receivable_by_buyer(self, today: date) -> bool:
        """True if a buyer settling today still receives this flow (record
        date, falling back to due date, hasn't passed)."""
        if self.due_date <= today:
            return False
        if self.record_date is not None and self.record_date <= today:
            return False
        return True


@dataclass(slots=True)
class Schedule:
    """Normalized schedule + provenance for one bond."""
    isin: str
    shape: str                       # plain|staggered|zero_coupon|cumulative|perpetual|irregular|empty
    flows: list[Flow] = field(default_factory=list)
    face_value: float | None = None
    frequency: str | None = None  # monthly|quarterly|semiannual|annual|cumulative|irregular
    periods_per_year: int | None = None
    day_count: str = daycount.ACT_ACT
    scale: float = 1.0               # divisor applied to issue-level amounts
    allotment_date: date | None = None  # accrual anchor for the first period
    amounts_estimated: bool = False  # any flow amount was estimated
    redemption_matches_face: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Good enough for exact YTM: dated flows whose principal
        reconciles with face value (perpetuals never redeem by design)."""
        if not self.flows:
            return False
        return self.redemption_matches_face or self.shape == "perpetual"

    def future_flows(self, today: date | None = None) -> list[Flow]:
        today = today or date.today()
        return [f for f in self.flows
                if f.amount and f.receivable_by_buyer(today)]


_FREQ_MAP = [
    # (needle in lowered frequency text, canonical name, periods/year)
    ("twelve times", "monthly", 12),
    ("monthly", "monthly", 12),
    ("once a year", "annual", 1),
    ("annual", "annual", 1),
    ("yearly", "annual", 1),
    ("quarter", "quarterly", 4),
    ("four times", "quarterly", 4),
    ("twice a year", "semiannual", 2),
    ("half year", "semiannual", 2),
    ("semi", "semiannual", 2),
    ("maturity", "cumulative", None),
    ("irregular", "irregular", None),
]


def classify_frequency(text: str | None) -> tuple[str | None, int | None]:
    """Map NSDL's frequency free-text to (name, periods_per_year)."""
    t = (text or "").strip().lower()
    if not t:
        return None, None
    for needle, name, ppy in _FREQ_MAP:
        if needle in t:
            return name, ppy
    return "irregular", None


def infer_frequency_from_flows(interest: list[Flow]) -> tuple[str | None, int | None]:
    """Median gap between consecutive interest dates -> frequency."""
    if len(interest) < 2:
        return None, None
    dates = sorted(f.due_date for f in interest)
    gaps = sorted((b - a).days for a, b in itertools.pairwise(dates) if b > a)
    if not gaps:
        return None, None
    med = gaps[len(gaps) // 2]
    for lo, hi, name, ppy in ((25, 45, "monthly", 12), (80, 100, "quarterly", 4),
                              (170, 195, "semiannual", 2), (350, 380, "annual", 1)):
        if lo <= med <= hi:
            return name, ppy
    return "irregular", None


def build(isin: str, cashflows: list[dict], terms: dict | None,
          today: date | None = None) -> Schedule:
    """Build the canonical Schedule for one bond.

    ``cashflows``: bond_cashflows rows (event, due_date, record_date,
    amount_payable, amount_redeem, amount_outstanding) with DATE columns
    already as datetime.date (psycopg does this).
    ``terms``: the bond_terms row, or None.
    """
    terms = terms or {}
    today = today or date.today()
    face = _num(terms.get("face_value"))
    coupon = _num(terms.get("coupon_rate"))
    perpetual = bool(terms.get("perpetual"))
    dc = daycount.normalize(terms.get("day_count_convention"))
    sched = Schedule(isin=isin, shape="empty", face_value=face, day_count=dc,
                     allotment_date=terms.get("allotment_date"))

    freq_name, ppy = classify_frequency(terms.get("interest_payment_frequency"))
    sched.frequency, sched.periods_per_year = freq_name, ppy

    flows = _normalize_rows(cashflows)
    if not flows:
        if perpetual:
            sched.shape = "perpetual"
            sched.warnings.append("perpetual with no published schedule")
        return sched

    interest = [f for f in flows if f.event == "interest"]
    redemptions = [f for f in flows if f.event == "redemption"]

    # --- repair missing redemption information -----------------------------
    # a) single redemption row with no amount -> the face value.
    if face and len(redemptions) == 1 and redemptions[0].amount is None:
        redemptions[0].amount = face
        redemptions[0].estimated = True
    # b) single full-redemption row implausibly below face (usually an
    #    interest amount misfiled as the redemption) -> override with face.
    if (face and len(redemptions) == 1 and redemptions[0].amount is not None
            and interest
            and "full" in (terms.get("redemption_type") or "").lower()
            and redemptions[0].amount < face * (1.0 - _FACE_TOL)):
        sched.warnings.append(
            f"implausible redemption {redemptions[0].amount:,.2f} vs face "
            f"{face:,.0f} — overridden with face")
        redemptions[0].amount = face
        redemptions[0].estimated = True
    # c) no redemption rows at all but terms carry a redemption date ->
    #    synthesize the principal flow (NSDL sometimes omits it).
    if face and not redemptions and not perpetual:
        red_date = terms.get("redemption_date")
        if red_date is not None and red_date > today:
            synth = Flow(event="redemption", due_date=red_date,
                         amount=face, estimated=True)
            flows.append(synth)
            redemptions = [synth]
            sched.warnings.append("synthesized redemption from bond_terms")

    # --- reconcile principal against face value ----------------------------
    redemption_total = sum(f.amount for f in redemptions if f.amount)
    if face and redemption_total:
        ratio = redemption_total / face
        future_total = sum(f.amount for f in redemptions
                           if f.amount and f.due_date > today)
        max_folded = 1.0 + max(coupon or 0.0, 15.0) / 100.0 + _FACE_TOL
        if abs(ratio - 1.0) <= _FACE_TOL:
            sched.redemption_matches_face = True
        elif not interest and 1.0 - _FACE_TOL <= ratio < _MIN_SCALE:
            # Zero-coupon / cumulative payout: redemption legitimately
            # exceeds face (accreted interest rides the principal).
            sched.redemption_matches_face = True
        elif interest and 1.0 < ratio <= max_folded:
            # Final coupon folded into the redemption row — flows are
            # still correct for pricing even though principal > face.
            sched.redemption_matches_face = True
        elif face and abs(future_total - face) / face <= _FACE_TOL:
            # Already partially redeemed: bond_terms.face_value is the
            # CURRENT outstanding face; past redemptions explain the rest.
            sched.redemption_matches_face = True
        elif ratio >= _MIN_SCALE:
            # Issue-level schedule: try net_quantity first, else the ratio
            # itself (redemption premium bonds excluded by _MIN_SCALE).
            qty = _num(terms.get("net_quantity"))
            scale = qty if qty and abs(redemption_total / qty / face - 1.0) <= _FACE_TOL else ratio
            for f in flows:
                if f.amount:
                    f.amount = f.amount / scale
            sched.scale = scale
            sched.redemption_matches_face = True
            sched.warnings.append(
                f"issue-level amounts rescaled by {scale:,.0f}")
        else:
            sched.warnings.append(
                f"redemption total {redemption_total:,.2f} vs face "
                f"{face:,.0f} (ratio {ratio:.3f}) — unreconciled")

    # --- frequency fallback: infer from the flow spacing ------------------
    if sched.periods_per_year is None and sched.frequency not in ("cumulative",):
        name, ppy = infer_frequency_from_flows(interest)
        if name:
            sched.frequency = sched.frequency or name
            sched.periods_per_year = ppy

    # --- estimate missing interest amounts --------------------------------
    if face and coupon:
        _estimate_missing(interest, redemptions, face, coupon, dc)
    if any(f.estimated for f in flows):
        sched.amounts_estimated = True

    # --- classify shape ----------------------------------------------------
    zero_coupon = "zero" in (terms.get("coupon_basis") or "").lower()
    n_redemptions = sum(1 for f in redemptions if (f.amount or 0) > 0)
    if perpetual and not redemptions:
        sched.shape = "perpetual"
    elif not interest and redemptions:
        sched.shape = "zero_coupon" if (zero_coupon or not coupon) else "cumulative"
    elif sched.frequency == "cumulative":
        sched.shape = "cumulative"
    elif n_redemptions > 1:
        sched.shape = "staggered"
    elif interest and redemptions:
        sched.shape = "plain"
    else:
        sched.shape = "irregular"
        sched.warnings.append("unclassifiable flow pattern")

    sched.flows = sorted(flows, key=lambda f: (f.due_date, f.event))
    return sched


def _normalize_rows(cashflows: list[dict]) -> list[Flow]:
    """bond_cashflows rows -> raw Flows (amounts still unscaled)."""
    flows = []
    for r in cashflows:
        due = r.get("due_date")
        if due is None:
            continue
        event_raw = (r.get("event") or "").strip().lower()
        if "interest" in event_raw:
            event, amount = "interest", _num(r.get("amount_payable"))
        elif "redemption" in event_raw or not event_raw:
            event = "redemption"
            amount = _num(r.get("amount_redeem")) or _num(r.get("amount_payable"))
        else:
            continue
        flows.append(Flow(event=event, due_date=due,
                          record_date=r.get("record_date"), amount=amount))
    return flows


def _estimate_missing(interest: list[Flow], redemptions: list[Flow],
                      face: float, coupon_pct: float, dc: str):
    """Fill missing interest amounts as coupon x outstanding face x period
    year-fraction. Outstanding face steps down at each partial redemption
    (staggered bonds keep paying coupons on the reduced principal)."""
    if not interest:
        return
    events = sorted(interest + redemptions, key=lambda f: f.due_date)
    outstanding = face
    prev_date = None
    for f in events:
        if f.event == "redemption":
            if f.amount:
                outstanding = max(0.0, outstanding - f.amount)
            continue
        if f.amount is None:
            if prev_date is not None and outstanding > 0:
                frac = daycount.year_frac(prev_date, f.due_date, dc)
                # Guard: a first-in-series or irregular gap > 1.5y means we
                # can't trust the estimate.
                if 0 < frac <= 1.5:
                    f.amount = round(outstanding * coupon_pct / 100.0 * frac, 2)
                    f.estimated = True
        prev_date = f.due_date


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard
