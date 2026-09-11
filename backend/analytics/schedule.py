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
The engine reconciles principal against face value and requires published
quantity evidence before rescaling issue-level schedules.

Missing interest amounts are estimated from the coupon rate, face value
outstanding and the actual day-count between flows; every estimated flow
is flagged so downstream YTMs can be labeled exact vs estimated.
"""
import calendar
import itertools
import math
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
    # Business-day-adjusted date the money actually lands (NSDL's
    # payment_date; can precede the due date across weekends/holidays).
    payment_date: date | None = None
    adjustments: list[dict] = field(default_factory=list)
    principal_reported: bool = False
    full_redemption: bool = False
    optional: bool = False

    def adjust(self, amount: float | None, kind: str, reason: str, *, estimated: bool = True) -> None:
        self.adjustments.append({"kind": kind, "previous_amount": self.amount,
                                 "amount": amount, "reason": reason})
        self.amount = amount
        self.estimated = self.estimated or estimated

    @property
    def effective_date(self) -> date:
        """When the money lands: payment date, else the due date."""
        return self.payment_date or self.due_date

    def receivable_by_buyer(self, today: date) -> bool:
        """True if a buyer settling today still receives this flow (record
        date, falling back to the money-lands date, hasn't passed). A
        coupon already paid early is gone even before its due date."""
        if self.optional or self.effective_date <= today:
            return False
        if self.record_date is not None and self.record_date <= today:
            return False
        return True

    def receivable_by_holder(self, purchase_date: date | None) -> bool:
        """True if a lot bought on ``purchase_date`` receives this flow
        (on the register by the record date, falling back to the
        money-lands date). Strict inequality: purchase dates are trade
        dates and exchange bonds settle T+1, so a lot traded ON the
        record date settles too late — same convention as
        receivable_by_buyer. Lots without a purchase date count as held
        all along."""
        if self.optional:
            return False
        if purchase_date is None:
            return True
        if self.record_date is not None:
            return purchase_date < self.record_date
        return purchase_date < self.effective_date


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
    coupon_rate: float | None = None
    perpetual: bool = False
    maturity_date: date | None = None
    call_price_unsupported: bool = False
    input_issues: list[tuple[date | None, str]] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Principal-reconciled schedule; pricing separately validates
        coupon completeness and dates (perpetuals need a call scenario)."""
        if not self.flows:
            return False
        return self.redemption_matches_face or self.shape == "perpetual"

    def future_flows(self, today: date | None = None) -> list[Flow]:
        today = today or date.today()
        return [f for f in self.flows
                if f.amount and f.receivable_by_buyer(today)]

    def principal_at(self, on: date, today: date) -> float | None:
        redemptions = [f for f in self.flows if f.event == "redemption" and not f.optional and f.due_date.year < 2500]
        base = _principal_base(self.face_value, redemptions, today)
        if base is None:
            return None
        return max(0.0, base - sum(f.amount or 0 for f in redemptions if f.due_date < on))

    def yield_amounts_estimated(self, today: date, until: date | None = None) -> bool:
        return any(f.estimated for f in self.future_flows(today)
                   if until is None or f.due_date <= until)

    def pricing_issues(self, today: date, until: date | None = None, *, check_convention: bool = True) -> list[str]:
        issues = [message for due, message in self.input_issues
                  if due is None or (due >= today and (until is None or due <= until))]
        if check_convention and self.day_count not in daycount.SUPPORTED:
            issues.append("Unsupported day-count convention; yield withheld")
        relevant = [f for f in self.flows if (until is None or f.due_date <= until)
                    and f.receivable_by_buyer(today)]
        if any(f.amount is None or not math.isfinite(f.amount) or f.amount < 0 for f in relevant):
            issues.append("Missing or invalid future cashflow amounts; yield withheld")
        if any(f.due_date.year >= 2500 or f.effective_date.year >= 2500
               or (f.record_date is not None and f.record_date > f.effective_date)
               for f in relevant):
            issues.append("Invalid cashflow dates; yield withheld")
        dates = sorted({f.due_date for f in self.flows if f.event == "interest" and not f.optional
                        and f.amount is not None and f.due_date.year < 2500
                        and (until is None or f.due_date <= until)})
        redemptions = [f for f in self.flows if f.event == "redemption" and not f.optional and f.amount
                       and f.due_date.year < 2500]
        end = until or max((f.due_date for f in redemptions), default=self.maturity_date)
        periodic = (self.periods_per_year in (1, 2, 4, 12)
                    and (self.coupon_rate or (dates and self.shape not in ("zero_coupon", "cumulative")))
                    and self.frequency != "cumulative")
        if periodic and self.flows and end is not None and today < end and end.year < 2500:
            period = 365.25 / (self.periods_per_year or 1)
            future = [d for d in dates if today < d <= end]
            if not future or (future[0] - today).days > 1.5 * period:
                issues.append("Missing upcoming coupon dates; yield withheld")
            if any(b > today and (b - max(a, today)).days > 1.5 * period
                   for a, b in itertools.pairwise(dates)):
                issues.append("Gap in periodic coupon schedule; yield withheld")
            if not dates or (end - dates[-1]).days > max(7, period / 2):
                issues.append("Coupon schedule ends before redemption; yield withheld")
            if any(b > today and (b - a).days < period / 2
                   for a, b in itertools.pairwise(dates[1:-1])):
                issues.append("Periodic coupon dates are clustered; yield withheld")
        return list(dict.fromkeys(issues))


_FREQ_MAP = [
    # (needle in lowered frequency text, canonical name, periods/year)
    # Order matters: 'semi annual' contains 'annual' and 'half yearly'
    # contains 'yearly', so semiannual needles must be checked first.
    ("twice a year", "semiannual", 2),
    ("half year", "semiannual", 2),
    ("semi", "semiannual", 2),
    ("twelve times", "monthly", 12),
    ("monthly", "monthly", 12),
    ("once a year", "annual", 1),
    ("annual", "annual", 1),
    ("yearly", "annual", 1),
    ("quarter", "quarterly", 4),
    ("four times", "quarterly", 4),
    ("every year", "annual", 1),
    ("each year", "annual", 1),
    ("cumulative", "cumulative", None),
    ("maturity", "cumulative", None),
    ("irregular", "irregular", None),
]


def classify_frequency(text: str | None) -> tuple[str | None, int | None]:
    """Map NSDL's frequency free-text to (name, periods_per_year)."""
    t = (text or "").strip().lower().replace("-", " ")
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
    maturity = terms.get("redemption_date")
    perpetual = bool(terms.get("perpetual")) or bool(maturity and maturity.year >= 2500)
    dc = daycount.normalize(terms.get("day_count_convention"))
    sched = Schedule(isin=isin, shape="empty", face_value=face, day_count=dc,
                     allotment_date=terms.get("allotment_date"), coupon_rate=coupon,
                     perpetual=perpetual, maturity_date=None if perpetual else maturity)
    premium = str(terms.get("redemption_premium_details") or "").strip().lower()
    call_text = str(terms.get("call_specified_dates") or "").lower()
    sched.call_price_unsupported = (
        premium not in ("", "-", "na", "n.a.", "not applicable", "nil", "none", "no", "0", "0.0", "0.00", "0%")
        or any(word in call_text for word in ("premium", "make whole", "make-whole")))
    for row in cashflows:
        due = row.get("due_date")
        if due is None:
            sched.input_issues.append((None, "Undated cashflow obligations; yield withheld"))
        for key in ("amount_payable", "amount_redeem"):
            value = row.get(key)
            if value is not None and (_num(value) is None or float(value) < 0):
                sched.input_issues.append((due, "Invalid source cashflow amount; yield withheld"))

    freq_name, ppy = classify_frequency(terms.get("interest_payment_frequency"))
    sched.frequency, sched.periods_per_year = freq_name, ppy
    zero_basis = "zero" in str(terms.get("coupon_basis") or "").lower()
    variable = (str(terms.get("coupon_basis") or "").lower().startswith("variable")
                or any(word in str(terms.get("coupon_type") or "").lower() for word in ("float", "compound")))
    fixed_coupon = not variable and not zero_basis and sched.frequency != "cumulative"

    flows = _normalize_rows(cashflows)
    # NSDL repeats the entire per-unit schedule once per further-issue
    # tranche for some bonds (observed up to 27 copies). Collapse exact
    # duplicates, or N identical full redemptions masquerade as a
    # staggered issue-level schedule and everything gets rescaled.
    # Genuine staggered schedules survive: real partial redemptions
    # differ in date or amount.
    seen: set[tuple] = set()
    payment_dates: dict[tuple, date | None] = {}
    unique: list[Flow] = []
    for f in flows:
        key = (f.event, f.due_date, f.record_date, f.amount)
        if key in seen and payment_dates[key] != f.payment_date:
            sched.input_issues.append((f.due_date, "Conflicting payment dates for duplicated cashflows; yield withheld"))
        if key not in seen:
            seen.add(key)
            payment_dates[key] = f.payment_date
            unique.append(f)
    if len(unique) < len(flows):
        sched.warnings.append(
            f"collapsed {len(flows) - len(unique)} duplicated schedule rows "
            "(NSDL repeats schedules per reissue tranche)")
        flows = unique
    if not flows:
        if perpetual:
            sched.shape = "perpetual"
            sched.warnings.append("perpetual with no published schedule")
        return sched

    _mark_optional_exits(sched, flows, terms)
    terminal_ambiguous = _split_terminal_payment(sched, flows, terms)
    interest = [f for f in flows if f.event == "interest" and not f.optional]
    redemptions = [f for f in flows if f.event == "redemption" and not f.optional]

    # --- repair missing redemption information -----------------------------
    # a) single redemption row with no amount -> the face value.
    if face and len(redemptions) == 1 and redemptions[0].amount is None:
        possible_combined = any(f.amount is not None and f.amount >= face * (1 - _FACE_TOL)
                                and abs((f.due_date - redemptions[0].due_date).days) <= 7 for f in interest)
        if sched.call_price_unsupported or possible_combined:
            sched.input_issues.append((redemptions[0].due_date,
                "Missing principal has unresolved premium or combined-payment terms; par redemption not assumed"))
        else:
            redemptions[0].adjust(face, "principal_assumed", "Missing redemption amount filled from face value")
    # b) a small full-redemption may contain a misfiled coupon rate. Repair
    #    only with matching coupon-field evidence; otherwise preserve it.
    if (face and len(redemptions) == 1 and redemptions[0].amount is not None
            and interest
            and "full" in (terms.get("redemption_type") or "").lower()
            and redemptions[0].amount < face * (1.0 - _FACE_TOL)):
        rate_filed = (fixed_coupon and coupon is not None and len(interest) >= 2
                      and redemptions[0].amount == coupon
                      and all(f.amount == coupon for f in interest))
        if rate_filed:
            redemptions[0].adjust(face, "principal_overridden",
                                  "Coupon rate filed in redemption and coupon fields; principal inferred from full-redemption terms")
            sched.warnings.append("coupon rate filed as redemption — overridden with face")
        else:
            sched.input_issues.append((redemptions[0].due_date,
                                       "Published redemption is below face; amount preserved, principal basis requires review"))
    # c) no redemption rows at all but terms carry a redemption date ->
    #    synthesize the principal flow (NSDL sometimes omits it).
    if face and not redemptions and not perpetual and not terminal_ambiguous:
        red_date = terms.get("redemption_date")
        if red_date is not None and red_date > today:
            if sched.call_price_unsupported:
                sched.input_issues.append((None, "Unresolved redemption premium; principal cannot be synthesized at par"))
            elif "partial" in str(terms.get("redemption_type") or "").lower():
                sched.input_issues.append((None, "Missing partial-redemption timeline; principal cannot be synthesized"))
            else:
                synth = Flow(event="redemption", due_date=red_date)
                synth.adjust(face, "principal_synthesized", "Added principal from face value and maturity terms; no redemption row was published")
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
            # Issue-level schedules require a published quantity to verify
            # scaling; the redemption/face ratio alone is not evidence.
            qty = _num(terms.get("net_quantity"))
            if qty and abs(redemption_total / qty / face - 1.0) <= _FACE_TOL:
                for f in flows:
                    if f.amount:
                        f.adjust(f.amount / qty, "issue_rescaled",
                                 f"Converted issue-level amount to one unit using published quantity {qty:g}", estimated=False)
                sched.scale = qty
                sched.redemption_matches_face = True
                sched.warnings.append(f"issue-level amounts rescaled by {qty:,.0f}")
            else:
                sched.input_issues.append((None, "Issue-level amount basis is unverified; amounts preserved, yield withheld"))
        else:
            sched.warnings.append(
                f"redemption total {redemption_total:,.2f} vs face "
                f"{face:,.0f} (ratio {ratio:.3f}) — unreconciled")

    # --- split above-face payouts into principal + interest ----------------
    # Cumulative bonds often publish face + accumulated interest as one
    # "Full Redemption" amount (observed at 1.6x and 2x face). The excess
    # over face is interest, not principal — otherwise "projected
    # principal" exceeds what was ever lent. Same dated totals, so YTM is
    # untouched. Runs after rescaling so amounts are per-unit; single
    # redemptions only (staggered schedules redeem face in parts).
    if (face and len(redemptions) == 1 and not sched.input_issues and not sched.call_price_unsupported
            and (amt := redemptions[0].amount) is not None
            and amt > face * (1.0 + _FACE_TOL)):
        r = redemptions[0]
        excess = round(amt - face, 2)
        # NSDL sometimes lists the final coupon BOTH as its own interest
        # row AND folded into the redemption amount (INE538L07536:
        # interest 97.50 + redemption 1,097.50 on the same date) — that
        # double-counts the coupon and inflates YTM. When the excess
        # matches the same-date interest, drop the folded copy.
        same_date_interest = sum(f.amount or 0 for f in interest
                                 if f.due_date == r.due_date)
        if (same_date_interest
                and abs(excess - same_date_interest) <= max(0.01, same_date_interest * 0.01)):
            r.adjust(face, "folded_coupon_removed", "Removed a matching final coupon also published separately on the same date")
            sched.warnings.append(
                f"final coupon {excess:,.2f} listed both separately and "
                "inside the redemption — folded copy dropped")
        else:
            split = Flow(event="interest", due_date=r.due_date,
                         record_date=r.record_date, amount=r.amount,
                         estimated=r.estimated, payment_date=r.payment_date)
            reason = "Split published redemption total into face principal and excess interest; total payment unchanged"
            split.adjust(excess, "redemption_interest_split", reason, estimated=False)
            r.adjust(face, "redemption_interest_split", reason, estimated=False)
            flows.append(split)
            interest = [f for f in flows if f.event == "interest" and not f.optional]
            sched.warnings.append(
                f"redemption exceeded face by {excess:,.2f} — "
                "reclassified as interest")
        sched.redemption_matches_face = True

    # --- frequency fallback: infer from the flow spacing ------------------
    if sched.periods_per_year is None and sched.frequency not in ("cumulative",):
        name, ppy = infer_frequency_from_flows(interest)
        if name:
            sched.frequency = sched.frequency or name
            sched.periods_per_year = ppy

    if _split_combined_installments(sched, flows, interest, redemptions, fixed_coupon, today):
        interest = [f for f in flows if f.event == "interest" and not f.optional]
        redemptions = [f for f in flows if f.event == "redemption" and not f.optional]
        sched.redemption_matches_face = True

    # --- repair rate-filed-as-amount interest rows -------------------------
    # Some issuers file the coupon RATE into every amount field
    # (INE0IID07751: 12.75% monthly bond, every interest row = 12.75 on
    # face 1000). Trigger only when ALL interest amounts equal the rate
    # numerically AND that disagrees with coupon x face / m — on bonds
    # where the two legitimately coincide (face 100 annual) the mismatch
    # test fails and nothing changes.
    if face and coupon and fixed_coupon and sched.periods_per_year and len(interest) >= 2:
        expected = face * coupon / 100.0 / sched.periods_per_year
        amts = [f.amount for f in interest if f.amount is not None]
        if (len(amts) == len(interest)
                and all(abs(a - coupon) < 0.005 for a in amts)
                and abs(expected - coupon) / expected > 0.05):
            previous = sched.allotment_date
            for f in sorted(interest, key=lambda f: f.due_date):
                amount = _coupon_amount(previous, f.due_date, redemptions, face, coupon, dc, today)
                f.adjust(amount, "coupon_rate_repaired",
                         "Published amount equals the coupon rate; recalculated using accrual dates and outstanding principal when known")
                previous = f.due_date
            sched.warnings.append(
                f"interest amounts equal the coupon rate ({coupon:g}) — "
                "recomputed from coupon dates and principal timeline")

    # --- estimate missing interest amounts --------------------------------
    if face and coupon and fixed_coupon and dc in daycount.SUPPORTED and not sched.input_issues:
        _estimate_missing(interest, redemptions, face, coupon, dc, today, sched.allotment_date)
    if any(f.estimated for f in flows):
        sched.amounts_estimated = True

    # --- repair interest published on the wrong face basis -----------------
    # Amortized bonds sometimes publish interest computed on the ORIGINAL
    # face while bond_terms carries the reduced current face (79NHIT35:
    # coupon 7.9% on face 300, published coupons ~119 — the 3,000
    # original-face basis). An implied factor flags a suspected mismatch;
    # it is not independent evidence for rewriting published payments.
    if face and coupon and fixed_coupon and len(interest) >= 2:
        dated = sorted((f for f in interest if f.amount),
                       key=lambda f: f.due_date)
        implied = []
        for prev, cur in itertools.pairwise(dated):
            gap = (cur.due_date - prev.due_date).days / 365.0
            if gap > 0.04 and cur.amount:  # ignore same-date splits
                basis = _coupon_amount(prev.due_date, cur.due_date, redemptions, face, 1.0, dc, today)
                if basis and cur.receivable_by_buyer(today) and not cur.estimated:
                    implied.append(cur.amount / basis)
        if implied:
            med = sorted(implied)[len(implied) // 2]
            if med > coupon * 2.5:
                sched.input_issues.append((None,
                    "Coupon amounts imply an unverified original face basis; published amounts preserved, yield withheld"))

    # --- synthesize missing future coupons ----------------------------------
    # When NSDL publishes no future interest rows for a coupon-bearing
    # bond (and the above-face split produced none either), an "exact"
    # solve on the bare redemption prices a bond that doesn't exist —
    # premium bonds come out with negative YTMs (738REC27TF: -5.09% vs
    # true ~5.2%). Bullet bonds pay coupons to maturity, so generate the
    # missing ones from the terms instead. A cumulative-frequency label
    # only excuses the gap when the redemption actually carries the
    # accumulated payout (exceeds face) — free text like "…AND ON
    # MATURITY" misclassifies plain annual bonds.
    future_red = sum(f.amount or 0 for f in redemptions if f.due_date > today)
    genuinely_cumulative = bool(
        face and future_red > face * (1.0 + _FACE_TOL))
    if (face and coupon and not zero_basis and not genuinely_cumulative and not perpetual
            and future_red > 0
            and not any(f.due_date > today for f in interest)):
        m = sched.periods_per_year
        final = max(f.due_date for f in redemptions if f.due_date > today)
        anchor = max((f.due_date for f in interest if f.due_date <= today), default=sched.allotment_date)
        if (m not in (1, 2, 4, 12) or anchor is None or anchor > today or not fixed_coupon or final.year >= 2500
                or sched.input_issues or _principal_base(face, redemptions, today) is None):
            sched.input_issues.append((None, "Missing or ambiguous coupon anchor, rate, or principal timeline; coupons not synthesized"))
        else:
            assert m is not None
            step = 12 // m
            index = max(0, ((today.year - anchor.year) * 12 + today.month - anchor.month) // step)
            previous = _months_back(anchor, -index * step)
            if previous > today:
                index -= 1
                previous = _months_back(anchor, -index * step)
            synthesized = []
            while previous < final:
                index += 1
                due = min(_months_back(anchor, -index * step), final)
                if due > today:
                    f = Flow(event="interest", due_date=due)
                    f.adjust(_coupon_amount(previous, due, redemptions, face, coupon, dc, today),
                             "coupon_synthesized", "Generated from the fixed coupon, dated principal balances, and coupon anchor; record date not supplied")
                    synthesized.append(f)
                previous = due
            flows.extend(synthesized)
            interest = [f for f in flows if f.event == "interest" and not f.optional]
            sched.amounts_estimated = bool(synthesized) or sched.amounts_estimated
            sched.warnings.append(
                f"{len(synthesized)} future coupon(s) synthesized from terms "
                f"({coupon:g}% using principal timeline) — none were published")

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
        # Accreted-interest payout: every interest flow lands on the final
        # redemption date, so nothing is paid before maturity.
        final_red = max(f.due_date for f in redemptions)
        if all(f.due_date == final_red for f in interest):
            sched.shape = "zero_coupon" if (zero_coupon or not coupon) else "cumulative"
            sched.frequency = sched.frequency or "cumulative"
        else:
            sched.shape = "plain"
    else:
        sched.shape = "irregular"
        sched.warnings.append("unclassifiable flow pattern")

    sched.flows = sorted(flows, key=lambda f: (f.due_date, f.event))
    return sched


def _mark_optional_exits(sched: Schedule, flows: list[Flow], terms: dict) -> None:
    face, maturity = sched.face_value, sched.maturity_date
    if not face or maturity is None or "face value" in str(terms.get("redemption_type") or "").lower():
        return
    option_terms = (sched.frequency == "cumulative"
                    or any(str(terms.get(k) or "").strip().lower() == "yes" for k in ("call_indicator", "put_indicator")))
    redemptions = [f for f in flows if f.full_redemption and f.amount is not None
                   and math.isclose(f.amount, face, rel_tol=1e-6, abs_tol=0.01)]
    dates = {f.due_date for f in redemptions}
    if not option_terms or len(dates) < 2 or maturity not in dates or max(dates) != maturity:
        return
    optional_dates = dates - {maturity}
    for f in flows:
        if f.due_date in optional_dates and (f in redemptions or (sched.frequency == "cumulative" and f.event == "interest")):
            f.optional = True
            f.adjust(f.amount, "optional_redemption_scenario",
                     "Alternative full-exit payout; excluded from hold-to-maturity cashflows, not treated as a receipt", estimated=False)
    sched.warnings.append("Optional full-redemption scenarios are excluded from hold-to-maturity receipts and yields")


def _split_combined_installments(sched: Schedule, flows: list[Flow], interest: list[Flow],
                                 redemptions: list[Flow], fixed_coupon: bool, today: date) -> bool:
    face, coupon, m = sched.face_value, sched.coupon_rate, sched.periods_per_year
    if (not fixed_coupon or not face or not coupon or m not in (1, 2, 4, 12)
            or sched.call_price_unsupported or sched.input_issues or len(redemptions) < 2
            or sched.day_count not in daycount.SUPPORTED
            or any(f.principal_reported or f.due_date <= today or f.amount is None for f in redemptions)
            or sum(f.amount or 0 for f in redemptions) <= face):
        return False
    ordered = sorted(redemptions, key=lambda f: f.due_date)
    if ordered[-1].due_date != sched.maturity_date:
        return False
    previous = max((f.due_date for f in interest if f.due_date < ordered[0].due_date), default=sched.allotment_date)
    if previous is None:
        return False
    assert m is not None
    outstanding = face
    plan = []
    tolerance = max(0.02, face * 0.0001)
    for index, f in enumerate(ordered):
        assert f.amount is not None
        previous = max(previous, max((i.due_date for i in interest if i.due_date < f.due_date), default=previous))
        fraction = daycount.year_frac(previous, f.due_date, sched.day_count)
        if not 0 < fraction <= 1.5 / m:
            return False
        expected_coupon = round(outstanding * coupon / 100 * fraction, 2)
        separate_coupon = any(i.due_date == f.due_date for i in interest)
        principal = (f.amount if separate_coupon else
                     round(f.amount - expected_coupon, 2) if index < len(ordered) - 1 else round(outstanding, 2))
        if -0.02 <= principal < 0:
            principal = 0.0
        component = f.amount - principal
        allowed_error = max(0.02, min(tolerance, expected_coupon * 0.01))
        if (principal < 0 or principal > outstanding or component < 0
                or (not separate_coupon and (component <= 0 or abs(component - expected_coupon) > allowed_error))):
            return False
        plan.append((f, principal, component))
        outstanding = round(outstanding - principal, 2)
        previous = f.due_date
    if outstanding != 0:
        return False
    for f, principal, component in plan:
        if component == 0:
            continue
        reason = "Interest component inferred from fixed-rate accrual and a fully reconciled principal timeline; published payment total unchanged"
        if principal == 0:
            f.event = "interest"
            f.full_redemption = False
            f.adjust(f.amount, "coupon_event_reclassified", reason)
        else:
            part = Flow("interest", f.due_date, f.record_date, f.amount, payment_date=f.payment_date,
                        adjustments=list(f.adjustments))
            part.adjust(component, "combined_installment_split", reason)
            f.adjust(principal, "combined_installment_split", reason)
            flows.append(part)
    sched.warnings.append("Combined installment components inferred from coupon accrual and principal reconciliation; all cash totals preserved")
    return True


def _split_terminal_payment(sched: Schedule, flows: list[Flow], terms: dict) -> bool:
    face, coupon, maturity = sched.face_value, sched.coupon_rate, sched.maturity_date
    if not face or sched.perpetual or any(f.event == "redemption" for f in flows):
        return False
    candidates = [f for f in flows if f.event == "interest" and f.amount is not None
                  and f.amount >= face * (1 - _FACE_TOL)]
    if not candidates:
        return False
    terminal = candidates[0]
    previous = max((f.due_date for f in flows if f.event == "interest" and f.due_date < terminal.due_date),
                   default=sched.allotment_date)
    expected = (_coupon_amount(previous, terminal.due_date, [], face, coupon, sched.day_count, terminal.due_date)
                if coupon and coupon > 0 else None)
    basis = str(terms.get("coupon_basis") or "").lower()
    supported = (len(candidates) == 1 and maturity == terminal.due_date and not sched.call_price_unsupported
                 and sched.periods_per_year in (1, 2, 4, 12)
                 and "full" in str(terms.get("redemption_type") or "").lower()
                 and not basis.startswith("variable") and "zero" not in basis
                 and "float" not in str(terms.get("coupon_type") or "").lower()
                 and not any(f.due_date > terminal.due_date for f in flows)
                 and expected is not None and terminal.amount is not None
                 and abs(terminal.amount - face - expected) <= max(0.02, expected * 0.03))
    if not supported:
        sched.input_issues.append((None, "Terminal interest may include principal but its date, amount, or terms are ambiguous; no extra principal added"))
        return True
    assert terminal.amount is not None
    total = terminal.amount
    reason = f"Split published terminal payment {total:,.2f} into principal {face:,.2f} and interest {total - face:,.2f}; total unchanged"
    principal = Flow("redemption", terminal.due_date, terminal.record_date, total,
                     terminal.estimated, terminal.payment_date)
    terminal.adjust(round(total - face, 2), "combined_payment_split", reason, estimated=False)
    principal.adjust(face, "combined_payment_split", reason, estimated=False)
    flows.append(principal)
    sched.warnings.append(reason)
    return False


def _principal_base(face: float | None, redemptions: list[Flow], today: date) -> float | None:
    if face is None or not math.isfinite(face) or face <= 0:
        return None
    if any(f.amount is None or not math.isfinite(f.amount) or f.amount < 0 for f in redemptions):
        return None
    total = sum(f.amount or 0 for f in redemptions)
    future = sum(f.amount or 0 for f in redemptions if f.due_date > today)
    if math.isclose(total, face, rel_tol=1e-6, abs_tol=0.01):
        return face
    if math.isclose(future, face, rel_tol=1e-6, abs_tol=0.01):
        return total
    if not redemptions or (len(redemptions) == 1 and redemptions[0].due_date > today
                           and (redemptions[0].amount or 0) >= face):
        return face
    return None


def _coupon_amount(start: date | None, end: date, redemptions: list[Flow], face: float,
                   coupon: float, dc: str, today: date) -> float | None:
    if start is None or start >= end or end.year >= 2500 or dc not in daycount.SUPPORTED:
        return None
    if not math.isfinite(coupon) or coupon < 0 or daycount.year_frac(start, end, dc) > 1.5:
        return None
    base = _principal_base(face, redemptions, today)
    if base is None:
        return None
    outstanding = base - sum(f.amount or 0 for f in redemptions if f.due_date <= start)
    cursor, amount = start, 0.0
    for redemption in sorted(redemptions, key=lambda f: f.due_date):
        if start < redemption.due_date < end:
            amount += max(0.0, outstanding) * coupon / 100 * daycount.year_frac(cursor, redemption.due_date, dc)
            outstanding -= redemption.amount or 0
            cursor = redemption.due_date
    amount += max(0.0, outstanding) * coupon / 100 * daycount.year_frac(cursor, end, dc)
    return round(amount, 2) if math.isfinite(amount) else None


def _months_back(d: date, months: int) -> date:
    """Same day-of-month ``months`` earlier (clamped to the month end)."""
    y, m = divmod((d.year * 12 + d.month - 1) - months, 12)
    last = calendar.monthrange(y, m + 1)[1]
    return date(y, m + 1, min(d.day, last))


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
                          record_date=r.get("record_date"), amount=amount,
                          payment_date=r.get("payment_date"),
                          principal_reported=event == "redemption" and _num(r.get("amount_redeem")) is not None,
                          full_redemption=event == "redemption" and "full" in event_raw))
    return flows


def _estimate_missing(interest: list[Flow], redemptions: list[Flow],
                      face: float, coupon_pct: float, dc: str, today: date,
                      allotment_date: date | None):
    """Fill missing interest amounts as coupon x outstanding face x period
    year-fraction. Outstanding face steps down at each partial redemption
    (staggered bonds keep paying coupons on the reduced principal)."""
    prev_date = allotment_date
    for f in sorted(interest, key=lambda f: f.due_date):
        if f.amount is None:
            if prev_date is not None:
                # Guard: a first-in-series or irregular gap > 1.5y means we
                # can't trust the estimate.
                amount = _coupon_amount(prev_date, f.due_date, redemptions, face, coupon_pct, dc, today)
                if amount is not None:
                    f.adjust(amount, "coupon_estimated",
                             f"Estimated at {coupon_pct:g}% from {prev_date} to {f.due_date}, integrating dated principal repayments")
        prev_date = f.due_date


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None  # NaN guard
