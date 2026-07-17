"""Parsers: NSDL raw JSON payloads -> flat row dicts for the parsed layer.

Pure functions, one per endpoint: payload dict -> {table_name: [row, ...]}.
The parsed tables are disposable derivations of nsdl_raw — rebuilt from
scratch by scripts/parse_nsdl.py on every run, so parser fixes here heal
the whole layer on the next rebuild.
"""
from __future__ import annotations

import re
from datetime import date, datetime

_NULLS = {"", "-", "NA", "N.A", "N.A.", "None", "null", "Not applicable",
          "NOT APPLICABLE", "Not Applicable"}

# Canonical agency names: NSDL mixes casings and legal-name variants.
_AGENCIES = (
    ("CRISIL", "CRISIL"),
    ("ICRA", "ICRA"),
    ("CARE", "CARE"),
    ("INDIA RATING", "INDIA RATINGS"),
    ("FITCH", "FITCH"),
    ("BRICKWORK", "BRICKWORK"),
    ("ACUITE", "ACUITE"),
    ("SMERA", "ACUITE"),          # Acuite's former name
    ("INFOMERICS", "INFOMERICS"),
)


def clean(v):
    """'-', 'NA', empty and friends -> None; else stripped string."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s in _NULLS else s


def to_date(v) -> date | None:
    s = clean(v)
    if not s:
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def to_num(v) -> float | None:
    s = clean(v)
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def to_int(v) -> int | None:
    n = to_num(v)
    return int(n) if n is not None else None


# Dual-rate coupons like "(7.04/7.54)%": 2012-13 tax-free issues pay the
# base rate plus an extra step-up ONLY to original retail allottees. A
# secondary-market buyer earns the base rate.
_DUAL_RATE = re.compile(
    r"^\(?\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*\)?\s*%?$")


def dual_rates(v) -> tuple[float, float] | None:
    """'(7.04/7.54)%' -> (7.04, 7.54); None if not a dual-rate string."""
    s = clean(v)
    if s is None:
        return None
    m = _DUAL_RATE.match(s)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (a, b) if a <= b else (b, a)


def fix_record_date(record: date | None, due: date | None) -> date | None:
    """Repair NSDL record dates that land AFTER the payment (impossible —
    the record date is when they snapshot who gets paid).

    The feed itself contains these errors (verified against nsdl_raw), in two
    recoverable shapes: day/month swapped (07-09 for 09-07) and wrong year.
    A repair is accepted only if it lands in the normal window — 0-60 days
    before the payment (the modal gap is 15 days). Unrepairable dates become
    None: no record date beats a wrong one.
    """
    if record is None or due is None or record <= due:
        return record
    if record.day <= 12:
        swapped = record.replace(month=record.day, day=record.month)
        if 0 <= (due - swapped).days <= 60:
            return swapped
    try:
        year_fixed = record.replace(year=due.year)
    except ValueError:  # 29 Feb into a non-leap year
        year_fixed = None
    if year_fixed and 0 <= (due - year_fixed).days <= 60:
        return year_fixed
    return None


def agency_norm(name) -> str | None:
    s = clean(name)
    if not s:
        return None
    up = s.upper()
    for needle, canon in _AGENCIES:
        if needle in up:
            return canon
    return up


# --------------------------------------------------------------------------
# per-endpoint parsers
# --------------------------------------------------------------------------

def parse_isins(isin, p):
    return {"bond_info": [{
        "isin": isin,
        "issuer_name": clean(p.get("issuerName")),
        "cin": clean(p.get("cin")),
        "lei": clean(p.get("lei")),
        "macro": clean(p.get("macro")),
        "sector": clean(p.get("sector")),
        "industry": clean(p.get("industry")),
        "basic_industry": clean(p.get("basicIndusrty")),
        "sec_type": clean(p.get("secType")),
        "sec_type_desc": clean(p.get("secTypeDesc")),
        "instrument_type": clean(p.get("instrumentType")),
        "issuer_type_owner": clean(p.get("issuerTypeOwner")),
        "issuer_type_nature": clean(p.get("issuerTypeNature")),
        "former_name_one": clean(p.get("formerNameOne")),
        "former_name_two": clean(p.get("formerNameTwo")),
        "former_name_three": clean(p.get("formerNameThree")),
        "restructured_status": clean(p.get("restructuredStatus")),
        "corporate_action_event": clean(p.get("corporateActionEvent")),
        "new_isin": clean(p.get("newIsin")),
    }]}


def parse_instruments(isin, p):
    vo = p.get("instrumentsVo") or {}
    ins = vo.get("instruments") or {}
    ac = vo.get("assetCover") or {}
    terms = {
        "isin": isin,
        "series": clean(ins.get("series")),
        "category": clean(ins.get("category")),
        "secured": clean(ins.get("secured")),
        "secured_details": clean(ins.get("securedDetails")),
        "tax_free": clean(ins.get("taxFree")),
        "perpetual": clean(ins.get("perpetualInNature")) == "Yes",
        "face_value": to_num(ins.get("faceValue")),
        "issue_price": to_num(ins.get("issuePrice")),
        "total_issue_size": to_num(ins.get("totalIssueSize")),
        "amount_raised": to_num(ins.get("amountRaised")),
        "allotment_date": to_date(ins.get("allotmentDate")),
        "redemption_date": to_date(ins.get("redemptionDate")),
        "tenure_years": to_int(ins.get("tenureYears")),
        "tenure_months": to_int(ins.get("tenureMonths")),
        "tenure_days": to_int(ins.get("tenureDays")),
        "mode_of_issue": clean(ins.get("modeOfIssue")),
        "instrument_desc": clean(ins.get("instrumentDesc")),
        "nature_of_instrument": clean(ins.get("natureOfInstrument")),
        "green_shoe": clean(ins.get("greenShoeOption")),
        "asset_coverage": clean(ac.get("assetCvge")),
        "asset_cover_percent": to_num(ac.get("assetCvrPercent")),
    }
    further = []
    for f in (vo.get("furtherIssue") or {}).get("furtherIssueVo") or []:
        row = {
            "isin": isin,
            "allotment_date": to_date(f.get("allotmentDate")),
            "allotment_quantity": to_num(f.get("allotmentQuantity")),
            "issue_price": to_num(f.get("issuePriceRs")),
            "issue_size": to_num(f.get("issueSizeRsCrs")),
            "cumulative_quantity": to_num(f.get("cumulativeQuantity")),
            "cumulative_issue_size": to_num(f.get("cummIssueSizeRsCrs")),
        }
        if any(v is not None for k, v in row.items() if k != "isin"):
            further.append(row)
    return {"_terms": [terms], "bond_further_issues": further}


def parse_coupondetail(isin, p):
    vo = p.get("coupensVo") or {}
    cd = vo.get("couponDetails") or {}
    dual = dual_rates(cd.get("couponRate"))
    coupon_rate = to_num(cd.get("couponRate"))
    if coupon_rate is None and dual:
        coupon_rate = dual[0]  # base rate: what a secondary buyer earns
    terms = {
        "isin": isin,
        "coupon_rate": coupon_rate,
        "coupon_type": clean(cd.get("couponType")),
        "coupon_basis": clean(cd.get("couponBasis")),
        "interest_payment_frequency": clean(cd.get("interestPaymentFrequency")),
        "frequency_description": clean(cd.get("frequencyDescription")),
        "day_count_convention": clean(cd.get("dayCountConvention")),
        "business_day_convention": clean(cd.get("businessDayConvention")),
        "coupon_payout_formula": clean(cd.get("couponPayoutformula")),
        "coupon_reset_process": clean(cd.get("couponResetProcess")),
    }
    flows = []
    sched = (vo.get("cashFlowScheduleDetails") or {})
    for f in sched.get("cashFlowSchedule") or []:
        due = to_date(f.get("dueDate"))
        flows.append({
            "isin": isin,
            "event": clean(f.get("cashFlowsEvent")),
            "due_date": due,
            "record_date": fix_record_date(to_date(f.get("recordDate")), due),
            "payment_date": to_date(f.get("paymentDate")),
            "amount_payable": to_num(f.get("amountPayable")),
            "amount_redeem": to_num(f.get("amountRedeem")),
            "amount_outstanding": to_num(f.get("amntOutstanding")),
            "actual_record_date": to_date(f.get("actualRecordDate")),
            "actual_payment_date": to_date(f.get("actualPaymentDate")),
            "reason_delay_payment": clean(f.get("reasonDelayPayment")),
        })
    if dual:
        _repair_dual_rate_interest(flows, *dual)
    return {"_terms": [terms], "bond_cashflows": flows}


def _repair_dual_rate_interest(flows, base, stepped):
    """For dual-rate bonds NSDL sometimes publishes interest as the SUM of
    both category rates (e.g. 145.8 = 70.4 + 75.4 per ₹1000). Detect that
    against the redemption amount (per-unit face proxy) and rewrite each
    interest flow to the base rate a secondary buyer actually receives."""
    reds = [f["amount_redeem"] or f["amount_payable"] or 0 for f in flows
            if "redemption" in (f["event"] or "").lower()]
    face = max(reds) if reds else None
    if not face:
        return
    summed = face * (base + stepped) / 100.0
    for f in flows:
        if "interest" not in (f["event"] or "").lower():
            continue
        amt = f["amount_payable"]
        if amt is not None and abs(amt - summed) <= summed * 0.02:
            f["amount_payable"] = round(face * base / 100.0, 2)


def parse_redemptions(isin, p):
    put = p.get("putOption") or {}
    call = p.get("callOption") or {}
    terms = {
        "isin": isin,
        "redemption_type": clean(p.get("redemptionType")),
        "maturity_type": clean(p.get("maturityType")),
        "net_quantity": to_num(p.get("netQuantity")),
        "defaulted_redemption": clean(p.get("defaultedRedemption")),
        "early_redemption_details": clean(p.get("earlyRedemptionDetails")),
        "redemption_premium_details": clean(p.get("redemptionPremiumDetails")),
        "put_indicator": clean(p.get("putIndicator")),
        "put_specified_dates": clean(put.get("specifiedDates")),
        "put_deadline": clean(put.get("deadlineDate")),
        "call_indicator": clean(p.get("callIndicator")),
        "call_specified_dates": clean(call.get("specifiedDates")),
        "call_deadline": clean(call.get("deadlineDate")),
    }
    events = []
    for r in p.get("redemption") or []:
        row = {
            "isin": isin,
            "redemption_date": to_date(r.get("partialRedemptionDates")),
            "value_redeemed": to_num(r.get("valueRedeemed")),
            "quantity_redeemed": to_num(r.get("quantityRedeemed")),
            "method": clean(r.get("redemptionMethod")),
            "reason": clean(r.get("reasonForRedemption")),
        }
        if any(v is not None for k, v in row.items() if k != "isin"):
            events.append(row)
    return {"_terms": [terms], "bond_redemption_events": events}


def parse_credit_ratings(isin, p):
    rows = []
    for is_current, key in ((True, "currentRatings"), (False, "earlierRatings")):
        for r in p.get(key) or []:
            rows.append({
                "isin": isin,
                "agency": agency_norm(r.get("creditRatingAgencyName")),
                "agency_name": clean(r.get("creditRatingAgencyName")),
                "rating": clean(r.get("currentRating")),
                "outlook": clean(r.get("outlook")),
                "rating_action": clean(r.get("ratingAction")),
                "rating_date": to_date(r.get("creditRatingDate")),
                "rating_change_date": to_date(r.get("ratingChangeDate")),
                "watch": clean(r.get("iciWatch")),
                "verification_status": clean(r.get("verificationStatus")),
                "press_release_link": clean(r.get("pressReleaseLink")),
                "is_current": is_current,
            })
    return {"bond_ratings": rows}


def parse_listings(isin, p):
    rows = []
    for is_current, key in ((True, "listingDetails"),
                            (False, "earlierListingDetails")):
        for r in p.get(key) or []:
            rows.append({
                "isin": isin,
                "exchange": clean(r.get("exchangeName")),
                "issue_type": clean(r.get("issueType")),
                "listing_date": to_date(r.get("listingDate")),
                "allotment_date": to_date(r.get("allotmentDate")),
                "quantity": to_num(r.get("listingQuantity")),
                "term_sheet_link": clean(r.get("termSheetLink")),
                "notification_link": clean(r.get("listingNotification")),
                "is_current": is_current,
            })
    return {"bond_listings": rows}


def parse_keydocuments(isin, p):
    rows = []
    for d in p if isinstance(p, list) else []:
        row = {
            "isin": isin,
            "doc_name": clean(d.get("docName")),
            "source": clean(d.get("source")),
            "download_link": clean(d.get("downloadLink")),
            "file_id": clean(d.get("fileId")),
        }
        if row["doc_name"] or row["download_link"]:
            rows.append(row)
    return {"bond_documents": rows}


def parse_keycontacts(isin, p):
    contact = {
        "isin": isin,
        "registrar": clean(p.get("registrar")),
        "registrar_contact": clean(p.get("regContact")),
        "registrar_contact_person": clean(p.get("regContactPerson")),
        "registrar_email": clean(p.get("regCompOffEmail")),
        "registrar_address": clean(p.get("regOffAddr")),
        "registrar_website": clean(p.get("regWebAddr")),
        "trustee_name": clean(p.get("debtTrusteeName")),
        "trustee_contact": clean(p.get("debtTrusteeContact")),
        "trustee_email": clean(p.get("debtTrusteeCompOffEmail")),
        "trustee_address": clean(p.get("debtTrusteeAddr")),
        "trustee_website": clean(p.get("debtTrusteeWebAddr")),
        "lead_manager": clean(p.get("leadName")),
        "lead_contact": clean(p.get("leadContact")),
        "lead_email": clean(p.get("leadCompOffEmail")),
        "lead_address": clean(p.get("leadAddr")),
        "lead_website": clean(p.get("leadWebAddr")),
        "issuer_reg_address": clean(p.get("issuerRegAddr")),
        "issuer_contact_name": clean(p.get("issuerCompOffName")),
        "issuer_contact_email": clean(p.get("issuerCompOffEmail")),
    }
    arrangers = []
    for a in p.get("arrangers") or []:
        row = {
            "isin": isin,
            "arranger": clean(a.get("arranger")),
            "contact_person": clean(a.get("contactPerson")),
            "contact": clean(a.get("contact")),
            "email": clean(a.get("email")),
            "website": clean(a.get("website")),
            "address": clean(a.get("regOffAddr")),
        }
        if row["arranger"]:
            arrangers.append(row)
    return {"bond_contacts": [contact], "bond_arrangers": arrangers}


def parse_defaultdetail(isin, p):
    rows = []
    for d in p if isinstance(p, list) else []:
        rows.append({
            "isin": isin,
            "sr_no": to_int(d.get("srNo")),
            "issue_nature": clean(d.get("issueNature")),
            "issue_size": to_num(d.get("issueSize")),
            "interest_due_date": to_date(d.get("interestDueDate")),
            "redemption_due_date": to_date(d.get("redemptionDueDate")),
            "default_details": clean(d.get("defaultDetails")),
            "actual_payment_details": clean(d.get("actualPaymentDetails")),
            "verification_date": to_date(d.get("verificationDate")),
            "verification_status": clean(d.get("verificationStatus")),
        })
    return {"bond_defaults": rows}


def parse_technical_default(isin, p):
    rows = []
    for d in p if isinstance(p, list) else []:
        rows.append({
            "isin": isin,
            "agency": agency_norm(d.get("craName")),
            "payment_due_date": to_date(d.get("paymtDueDate")),
            "amount_to_be_paid": to_num(d.get("amtToBePaid")),
            "amount_paid": to_num(d.get("amtPaid")),
            "amount_payment_failed": to_num(d.get("amtPaymtFailed")),
            "failure_reason": clean(d.get("paymtFailReasn")),
            "update_date": to_date(d.get("updateDt")),
            "press_release_link": clean(d.get("pressReleaseLink")),
        })
    return {"bond_technical_defaults": rows}


def parse_restructuring(isin, p):
    return {"bond_restructurings": [{
        "isin": isin,
        "event": clean(p.get("corporateActionEvent")),
        "parent_isin": clean(p.get("parentIsin")),
        "sec_type": clean(p.get("secType")),
        "category": clean(p.get("category")),
        "coupon_rate": clean(p.get("couponRate")),
        "coupon_basis": clean(p.get("couponBasis")),
        "allotment_date": to_date(p.get("allotmentDate")),
        "redemption_date": to_date(p.get("redemptionDate")),
        "interest_payment_frequency": clean(p.get("interestPaymentFrequency")),
        "parent_sec_type": clean(p.get("parentSecType")),
        "parent_category": clean(p.get("parentCategory")),
        "parent_coupon_rate": clean(p.get("parentCouponRate")),
        "parent_coupon_basis": clean(p.get("parentCouponBasis")),
        "parent_allotment_date": to_date(p.get("parentAllotmentDate")),
        "parent_redemption_date": to_date(p.get("parentRedemptionDate")),
        "parent_interest_payment_frequency":
            clean(p.get("parentInterestPaymentFrequency")),
    }]}


def parse_holdings(isin, quarter, p):
    report_date = to_date(p.get("reportGenerationDate"))
    rows = []
    for h in p.get("shareholdingData") or []:
        header = clean(h.get("headerName"))
        rows.append({
            "isin": isin, "quarter": quarter, "report_date": report_date,
            "header": header, "subheader": None,
            "holder_count": to_int(h.get("headerEmployeeCount")),
            "quantity": to_num(h.get("totalShareCountHeader")),
        })
        for s in h.get("listOfSubHeader") or []:
            cnt = to_int(s.get("subHeaderEmployeeCount"))
            qty = to_num(s.get("totalShareCountSubHeader"))
            if cnt is None and qty is None:
                continue  # skip the huge all-null category matrix
            rows.append({
                "isin": isin, "quarter": quarter, "report_date": report_date,
                "header": header, "subheader": clean(s.get("subHeaderName")),
                "holder_count": cnt, "quantity": qty,
            })
    return {"bond_holdings": rows}


#: endpoint -> parser for the rebuild loop (holdings handled separately:
#: its parser needs the quarter key).
PARSERS = {
    "isins": parse_isins,
    "instruments": parse_instruments,
    "coupondetail": parse_coupondetail,
    "redemptions": parse_redemptions,
    "credit_ratings": parse_credit_ratings,
    "listings": parse_listings,
    "keydocuments": parse_keydocuments,
    "keycontacts": parse_keycontacts,
    "defaultdetail": parse_defaultdetail,
    "technical_default": parse_technical_default,
    "restructuring": parse_restructuring,
}
