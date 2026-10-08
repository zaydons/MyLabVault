"""Review step of PDF import: match parsed tests to saved lab tests, flag problems, apply edits.

Shared by the preview (what the user reviews) and the confirm step (what gets saved), so both
agree on which saved test a row maps to.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Lab, LabResult

# Spellings of the same unit that labs use interchangeably. Units in the same group are
# equal for matching purposes; anything else is a real mismatch (e.g. mg/dL vs mmol/L).
_UNIT_GROUPS = [
    {"10^3/ul", "x10e3/ul", "x10^3/ul", "k/ul", "thou/ul", "10e3/ul", "10*3/ul", "10^9/l", "x10e9/l", "x10^9/l", "10*9/l", "giga/l"},
    {"10^6/ul", "x10e6/ul", "x10^6/ul", "m/ul", "mil/ul", "10e6/ul", "10*6/ul", "10^12/l", "x10e12/l", "x10^12/l", "10*12/l"},
    {"miu/l", "uiu/ml", "mu/l", "uu/ml"},
    {"iu/l", "u/l"},
    {"ng/ml", "ug/l"},
    {"pg/ml", "ng/l"},
    {"mg/l", "ug/ml"},
    {"%", "percent", "pct"},
    {"ml/min/1.73m2", "ml/min/1.73", "ml/min/1.73m^2", "ml/min/1.73sqm"},
]


def normalize_unit(unit: Optional[str]) -> str:
    """Canonical spelling of a unit for comparison."""
    u = (unit or "").strip().lower().replace(" ", "")
    u = u.replace("µ", "u").replace("μ", "u").replace("mcg", "ug")
    # A cubic millimetre is a microlitre (K/cumm = K/uL), and "gm" is grams (gm/dL = g/dL)
    u = re.sub(r"(cumm|cu\.?mm|mm3|mm\^3)$", "ul", u)
    u = re.sub(r"^gm/", "g/", u)
    for group in _UNIT_GROUPS:
        if u in group:
            return min(group)
    return u


def units_match(a: Optional[str], b: Optional[str]) -> bool:
    """True when either unit is missing or both mean the same thing."""
    if not (a or "").strip() or not (b or "").strip():
        return True
    return normalize_unit(a) == normalize_unit(b)


def find_lab(name: Optional[str], db: Session) -> Optional[Lab]:
    """Saved lab test whose name matches a printed test name.

    Exact (case-insensitive) match first, then a saved name that starts with the printed name
    followed by punctuation, e.g. "TSH" matches "TSH (Thyroid)". Never a looser partial match,
    so "Hemoglobin" doesn't match "Hemoglobin A1c".
    """
    test_name = (name or "").strip()
    if not test_name:
        return None
    lab = db.query(Lab).filter(Lab.name.ilike(test_name)).first()
    if lab:
        return lab
    wanted = test_name.lower()
    for lab in db.query(Lab).all():
        saved = lab.name.lower()
        if not saved.startswith(wanted) or len(saved) == len(wanted):
            continue
        rest = saved[len(wanted):]
        if rest[0] in "(),-" or (rest[0] == " " and len(rest) > 1 and rest[1] in "(),-"):
            return lab
    return None


def find_lab_in_unit(name: Optional[str], unit: Optional[str], db: Session) -> Optional[Lab]:
    """Saved test with this name in the same (or an equivalent) unit.

    Also finds the copy the importer made to keep another unit apart, e.g. "Glucose (mmol/L)"
    for Glucose in mmol/L when "Glucose" is saved in mg/dL. Used so a test is never saved as a
    new copy of a test that already exists in that unit.
    """
    test_name = (name or "").strip()
    if not test_name:
        return None
    unit = (unit or "").strip()
    candidates = [test_name] + ([f"{test_name} ({unit})"] if unit else [])
    for candidate in candidates:
        for lab in db.query(Lab).filter(Lab.name.ilike(candidate)).all():
            if units_match(unit, lab.unit.name if lab.unit else None):
                return lab
    return None


_NUMBER = r"[-+]?\d+(?:\.\d+)?"


def parse_range_text(text: Optional[str]) -> Dict[str, Any]:
    """Reference range dict {low, high, text} from text such as "70-99", ">59", "<5.7", "<=200"."""
    raw = (text or "").strip()
    if not raw:
        return {"low": None, "high": None, "text": ""}
    match = re.fullmatch(rf"(?P<op>[<>]=?|≤|≥)\s*(?P<v>{_NUMBER})", raw)
    if match:
        value = float(match.group("v"))
        if match.group("op") in (">", ">=", "≥"):
            return {"low": value, "high": None, "text": raw}
        return {"low": None, "high": value, "text": raw}
    match = re.fullmatch(rf"(?P<a>{_NUMBER})\s*(?:-|–|to)\s*(?P<b>{_NUMBER})", raw)
    if match:
        return {"low": float(match.group("a")), "high": float(match.group("b")), "text": raw}
    return {"low": None, "high": None, "text": raw}


def parse_result(raw: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """(numeric value, text) for a result as typed or printed; text is set when it isn't a plain number."""
    text = (raw or "").strip()
    if not text:
        return None, None
    try:
        return float(text), None
    except ValueError:
        return None, text


def _range_of(test: Dict[str, Any]) -> Dict[str, Any]:
    ref = test.get("reference_range")
    if isinstance(ref, dict):
        return {"low": ref.get("low"), "high": ref.get("high"), "text": ref.get("text") or ""}
    if isinstance(ref, str):
        return parse_range_text(ref)
    return {"low": None, "high": None, "text": ""}


def row_status(value: Optional[float], ref: Dict[str, Any], lab: Optional[Lab], flag: Optional[str]) -> str:
    """normal / high / low / abnormal / unknown, from the report's range, else the saved test's."""
    if value is not None:
        low, high = ref.get("low"), ref.get("high")
        if low is not None or high is not None:
            if low is not None and value < low:
                return "low"
            if high is not None and value > high:
                return "high"
            return "normal"
        if lab is not None:
            return lab.get_result_status(value)
    f = (flag or "").strip().lower()
    if f in ("h", "hi", "high", "hh"):
        return "high"
    if f in ("l", "lo", "low", "ll"):
        return "low"
    return "abnormal" if f else "unknown"


def _numeric(test: Dict[str, Any]) -> Optional[float]:
    if test.get("numeric_value") is not None:
        try:
            return float(test["numeric_value"])
        except (TypeError, ValueError):
            return None
    if test.get("is_numeric") and test.get("result") not in (None, ""):
        try:
            return float(test["result"])
        except (TypeError, ValueError):
            return None
    return None


def row_date(test: Dict[str, Any]) -> Optional[str]:
    """The collection date printed on this row (health summaries), as YYYY-MM-DD, else None."""
    raw = (test.get("date_collected") or "")[:10]
    return raw if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else None


def _already_saved(lab: Lab, day: Optional[str], value: Optional[float], text: str, db: Session) -> bool:
    """A result for this test with the same value is already saved for this collection date."""
    if lab is None or not day:
        return False
    query = db.query(LabResult.id).filter(LabResult.lab_id == lab.id, func.date(LabResult.date_collected) == day)
    if value is not None:
        query = query.filter(LabResult.result == value)
    elif text:
        query = query.filter(func.lower(LabResult.result_text) == text.lower())
    else:
        return False
    return query.first() is not None


def review_rows(parsed_tests: List[Dict[str, Any]], db: Session, imported: Optional[set] = None,
                report_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """One row per parsed test (in parsed order, `index` is its position) with match, status and issues."""
    imported = imported or set()
    report_day = (report_date or "")[:10] or None
    rows = []
    for index, test in enumerate(parsed_tests):
        name = (test.get("name") or "").strip()
        value = _numeric(test)
        result_display = test.get("result") if test.get("result") not in (None, "") else test.get("result_text")
        readable = bool(name) and result_display not in (None, "")
        lab = find_lab(name, db) if name else None
        ref = _range_of(test)
        unit = (test.get("unit") or "").strip()
        if lab is not None and not units_match(unit, lab.unit.name if lab.unit else None):
            # A copy of this test kept for this unit, e.g. "Glucose (mmol/L)"
            lab = find_lab_in_unit(name, unit, db) or lab
        lab_unit = lab.unit.name if lab is not None and lab.unit else None
        unit_mismatch = lab is not None and not units_match(unit, lab_unit)

        issues = []
        if not name:
            issues.append("The test name couldn't be read.")
        if result_display in (None, ""):
            issues.append("The result couldn't be read.")
        if unit_mismatch:
            issues.append(f"The report uses {unit}, but {lab.name} is saved in {lab_unit}.")
        own_date = row_date(test)
        saved = index not in imported and readable and _already_saved(
            lab, own_date or report_day, value, str(result_display or "").strip(), db)
        if saved:
            issues.append("This result is already saved for this date.")

        rows.append({
            "index": index,
            "name": name,
            "result": "" if result_display is None else str(result_display),
            "unit": unit,
            "reference_range": ref,
            "flag": test.get("flag"),
            "lab_comment": test.get("lab_comment"),
            "matched_lab_id": lab.id if lab else None,
            "matched_lab_name": lab.name if lab else None,
            "matched_lab_unit": lab_unit,
            "unit_mismatch": unit_mismatch,
            "status": row_status(value, ref, lab, test.get("flag")),
            "readable": readable,
            "already_imported": index in imported,
            "already_saved": saved,
            "date_collected": own_date,
            "issues": issues,
        })
    return rows


def apply_edit(test: Dict[str, Any], edit: Dict[str, Any]) -> Dict[str, Any]:
    """Copy of a parsed test with the user's corrections from the review screen applied."""
    test = dict(test)
    if edit.get("date_collected"):
        test["date_collected"] = edit["date_collected"].strip()
    if edit.get("name") is not None:
        test["name"] = edit["name"].strip()
    if edit.get("unit") is not None:
        test["unit"] = edit["unit"].strip()
    if edit.get("reference_range") is not None:
        test["reference_range"] = parse_range_text(edit["reference_range"])
    if edit.get("result") is not None:
        value, text = parse_result(edit["result"])
        test["result"] = edit["result"].strip()
        test["numeric_value"] = value
        test["result_text"] = text
        test["is_numeric"] = value is not None
        test["is_qualitative"] = value is None
    return test


# ---------- comparing the built-in parser's reading with the AI's ----------

def _name_key(name: Optional[str]) -> str:
    """Order-insensitive key for a test name: "Cholesterol, Total" == "Total Cholesterol"."""
    return " ".join(sorted(re.findall(r"[a-z0-9]+", (name or "").lower())))


_TITLES = {"dr", "md", "do", "np", "pa", "c", "fnp", "aprn", "rn", "phd", "mph", "facp"}


def _person_key(name: Optional[str]) -> str:
    """Provider name without titles or credentials, word order ignored: "Dr Jane Smith, MD" == "Smith, Jane"."""
    return " ".join(sorted(w for w in re.findall(r"[a-z]+", (name or "").lower()) if w not in _TITLES))


def _same_value(a: Optional[str], b: Optional[str]) -> bool:
    a, b = (a or "").strip(), (b or "").strip()
    try:
        return float(a) == float(b)
    except ValueError:
        return a.lower() == b.lower()


def _reading(test: Dict[str, Any]) -> Dict[str, str]:
    result = test.get("result") if test.get("result") not in (None, "") else test.get("result_text")
    return {
        "name": (test.get("name") or "").strip(),
        "result": "" if result is None else str(result),
        "unit": (test.get("unit") or "").strip(),
        "range": (_range_of(test).get("text") or "").strip(),
        "date": row_date(test) or "",
    }


def compare_parses(active: Dict[str, Any], other: Dict[str, Any], db: Session) -> Dict[str, Any]:
    """Differences between the built-in parser's reading of a report and the AI's.

    Rows are paired when both map to the same saved test, else by name (word order ignored).
    `active_index` is the row's position in the reading currently used for import.
    """
    ai, standard = (active, other) if active.get("parser") == "ai" else (other, active)
    active_is_ai = ai is active

    def keyed(parse):
        rows = []
        for index, test in enumerate(parse.get("tests", [])):
            lab = find_lab(test.get("name"), db)
            rows.append({"index": index, "reading": _reading(test), "lab_id": lab.id if lab else None,
                         "name_key": _name_key(test.get("name"))})
        return rows

    std_rows, ai_rows = keyed(standard), keyed(ai)
    unmatched_ai = list(ai_rows)
    pairs = []
    for s in std_rows:
        # Same test on the same date first (health summaries repeat a test once per date)
        same_day = [a for a in unmatched_ai if a["reading"]["date"] == s["reading"]["date"]]
        match = None
        for pool in (same_day, unmatched_ai):
            match = next((a for a in pool if s["lab_id"] and a["lab_id"] == s["lab_id"]), None) \
                or next((a for a in pool if s["name_key"] and a["name_key"] == s["name_key"]), None)
            if match:
                break
        if match:
            unmatched_ai.remove(match)
        pairs.append((s, match))
    pairs += [(None, a) for a in unmatched_ai]

    rows, summary = [], {"same": 0, "different": 0, "only_ai": 0, "only_standard": 0}
    for s, a in pairs:
        if s and a:
            differences = [field for field in ("result", "unit", "range", "date")
                           if not (_same_value(s["reading"][field], a["reading"][field]) if field == "result"
                                   else (normalize_unit(s["reading"][field]) == normalize_unit(a["reading"][field]) if field == "unit"
                                         else s["reading"][field].replace(" ", "") == a["reading"][field].replace(" ", "")))]
            change = "different" if differences else "same"
        else:
            differences = []
            change = "only_ai" if a else "only_standard"
        summary[change] += 1
        own = a if active_is_ai else s
        rows.append({
            "name": (a or s)["reading"]["name"] or (s or a)["reading"]["name"],
            "standard": s["reading"] if s else None,
            "ai": a["reading"] if a else None,
            "change": change,
            "differences": differences,
            "active_index": own["index"] if own else None,
        })
    order = {"different": 0, "only_ai": 1, "only_standard": 2, "same": 3}
    rows.sort(key=lambda r: order[r["change"]])

    sd, ad = (standard.get("date_collected") or "")[:10], (ai.get("date_collected") or "")[:10]
    sp, ap = standard.get("physician") or "", ai.get("physician") or ""
    fields = [
        {"field": "Collection date", "standard": sd, "ai": ad, "same": sd == ad},
        {"field": "Provider", "standard": sp, "ai": ap, "same": _person_key(sp) == _person_key(ap)},
    ]

    return {
        "active": "ai" if active_is_ai else "standard",
        "standard_count": len(std_rows),
        "ai_count": len(ai_rows),
        "standard_error": standard.get("error"),
        "summary": summary,
        "fields": fields,
        "rows": rows,
    }
