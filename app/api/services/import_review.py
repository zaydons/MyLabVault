"""Review step of PDF import: match parsed tests to saved lab tests, flag problems, apply edits.

Shared by the preview (what the user reviews) and the confirm step (what gets saved), so both
agree on which saved test a row maps to.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import Lab

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


def review_rows(parsed_tests: List[Dict[str, Any]], db: Session, imported: Optional[set] = None) -> List[Dict[str, Any]]:
    """One row per parsed test (in parsed order, `index` is its position) with match, status and issues."""
    imported = imported or set()
    rows = []
    for index, test in enumerate(parsed_tests):
        name = (test.get("name") or "").strip()
        value = _numeric(test)
        result_display = test.get("result") if test.get("result") not in (None, "") else test.get("result_text")
        readable = bool(name) and result_display not in (None, "")
        lab = find_lab(name, db) if name else None
        ref = _range_of(test)
        unit = (test.get("unit") or "").strip()
        lab_unit = lab.unit.name if lab is not None and lab.unit else None
        unit_mismatch = lab is not None and not units_match(unit, lab_unit)

        issues = []
        if not name:
            issues.append("The test name couldn't be read.")
        if result_display in (None, ""):
            issues.append("The result couldn't be read.")
        if unit_mismatch:
            issues.append(f"The report uses {unit}, but {lab.name} is saved in {lab_unit}.")

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
            "issues": issues,
        })
    return rows


def apply_edit(test: Dict[str, Any], edit: Dict[str, Any]) -> Dict[str, Any]:
    """Copy of a parsed test with the user's corrections from the review screen applied."""
    test = dict(test)
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
