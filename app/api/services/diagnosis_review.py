"""Diagnoses read from a visit summary, health summary or records export: review rows and saving.

The AI reader returns them alongside lab results (see ai_parser): the conditions addressed at a
visit (Today's Diagnosis, Assessment) and the ongoing problem list. Each is compared with the
patient's diagnoses: a new one is added, one the document marks resolved can be marked resolved,
and anything already recorded needs no change. Nothing is saved until confirmed; the positions
saved are kept in the import's parsed data (saved_diagnoses).
"""

import re
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import Diagnosis

PLAN_ADD, PLAN_ADD_RESOLVED, PLAN_RESOLVE, PLAN_NONE = "add", "add_resolved", "resolve", "none"


def _day(raw: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat((raw or "")[:10])
    except ValueError:
        return None


def _when(d: Optional[date]) -> str:
    return f"{d:%b} {d.day}, {d.year}" if d else ""


def _code(raw: Optional[str]) -> Optional[str]:
    """An ICD-10 code in its usual form, e.g. 'E78.5', or None."""
    code = re.sub(r"\s+", "", (raw or "").upper()).rstrip(".")
    return code[:20] if re.fullmatch(r"[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?", code) else None


def _key(name: Optional[str]) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


def all_diagnoses(patient_id: int, db: Session) -> List[Diagnosis]:
    return db.query(Diagnosis).filter(Diagnosis.patient_id == patient_id).order_by(Diagnosis.id).all()


def _find(item: Dict[str, Any], recorded: List[Diagnosis]) -> Optional[Diagnosis]:
    """The recorded diagnosis a document row names: by the AI's match, the same ICD-10 code, or the same name."""
    wanted = _key(item.get("matched_diagnosis"))
    code = _code(item.get("icd10_code"))
    name = _key(item.get("name"))
    for test in (lambda d: wanted and _key(d.name) == wanted,
                 lambda d: code and _code(d.icd10_code) == code,
                 lambda d: name and _key(d.name) == name):
        found = [d for d in recorded if test(d)]
        if found:
            # An ongoing one before a resolved one of the same name
            return sorted(found, key=lambda d: d.status != "active")[0]
    return None


def plan_diagnosis(item: Dict[str, Any], recorded: List[Diagnosis], visit_day: Optional[date]) -> Dict[str, Any]:
    """What confirming this diagnosis would do to the patient's diagnoses, with a sentence saying so."""
    match = _find(item, recorded)
    resolved = item.get("status") == "resolved" or bool(item.get("resolved_date"))
    if match is None:
        if resolved:
            when = _day(item.get("resolved_date"))
            return {"plan": PLAN_ADD_RESOLVED, "diagnosis": None,
                    "note": f"Not on your list; it will be added as resolved{f' on {_when(when)}' if when else ''}."}
        onset = _day(item.get("onset_date")) or visit_day
        return {"plan": PLAN_ADD, "diagnosis": None,
                "note": f"Not on your list yet; it will be added as ongoing{f', diagnosed {_when(onset)}' if onset else ''}."}
    if match.status == "active" and resolved:
        when = _day(item.get("resolved_date")) or visit_day
        if when and match.onset_date and when < match.onset_date:
            return {"plan": PLAN_NONE, "diagnosis": match,
                    "note": f"On your list as ongoing since {_when(match.onset_date)}, after this document's resolved date."}
        return {"plan": PLAN_RESOLVE, "diagnosis": match,
                "note": f"On your list as ongoing; it will be marked resolved{f' on {_when(when)}' if when else ''}."}
    if match.status == "resolved" and not resolved:
        return {"plan": PLAN_NONE, "diagnosis": match,
                "note": f"On your list as resolved{f' on {_when(match.resolved_date)}' if match.resolved_date else ''}. "
                        "If it's back, change it on the Diagnoses page."}
    return {"plan": PLAN_NONE, "diagnosis": match,
            "note": f"Already on your list as {match.name}{f' ({match.icd10_code})' if match.icd10_code else ''}."}


def diagnosis_rows(parsed: Dict[str, Any], patient_id: int, db: Session) -> List[Dict[str, Any]]:
    """One row per diagnosis read from the document, with what confirming it would do."""
    recorded = all_diagnoses(patient_id, db)
    visit_day = _day(parsed.get("date_collected"))
    done = set(parsed.get("saved_diagnoses") or [])
    rows = []
    for index, item in enumerate(parsed.get("diagnoses") or []):
        if not (item.get("name") or "").strip():
            continue
        plan = plan_diagnosis(item, recorded, visit_day)
        rows.append({
            "index": index,
            "name": item["name"].strip(),
            "icd10_code": _code(item.get("icd10_code")),
            "context": item.get("context") or "listed",
            "status": item.get("status") or "active",
            "onset_date": item.get("onset_date"),
            "resolved_date": item.get("resolved_date"),
            "plan": plan["plan"],
            "note": plan["note"],
            "diagnosis_id": plan["diagnosis"].id if plan["diagnosis"] else None,
            "already_imported": index in done,
        })
    return rows


def save_diagnoses(parsed: Dict[str, Any], indices: List[int], visit_day: Optional[date], patient_id: int,
                   provider_id: Optional[int], note: str, db: Session) -> List[Dict[str, Any]]:
    """Apply the plan of each chosen diagnosis; returns what changed, e.g. {"name", "plan"}."""
    items = parsed.get("diagnoses") or []
    done = set(parsed.get("saved_diagnoses") or [])
    today = date.today()
    changes = []
    for index in dict.fromkeys(indices):
        if index < 0 or index >= len(items) or index in done or not (items[index].get("name") or "").strip():
            continue
        item = items[index]
        plan = plan_diagnosis(item, all_diagnoses(patient_id, db), visit_day)
        onset = _day(item.get("onset_date"))
        resolved_on = _day(item.get("resolved_date"))
        onset = onset if onset and onset <= today else None
        resolved_on = resolved_on if resolved_on and resolved_on <= today else None
        if plan["plan"] in (PLAN_ADD, PLAN_ADD_RESOLVED):
            resolved = plan["plan"] == PLAN_ADD_RESOLVED
            if not resolved:
                onset = onset or visit_day
            if onset and resolved_on and resolved_on < onset:
                onset = None
            db.add(Diagnosis(patient_id=patient_id, name=item["name"].strip()[:255], icd10_code=_code(item.get("icd10_code")),
                             status="resolved" if resolved else "active", onset_date=onset,
                             resolved_date=resolved_on if resolved else None, provider_id=provider_id, notes=note))
            name = item["name"].strip()
        elif plan["plan"] == PLAN_RESOLVE:
            match = plan["diagnosis"]
            match.status = "resolved"
            match.resolved_date = resolved_on or visit_day
            match.icd10_code = match.icd10_code or _code(item.get("icd10_code"))
            name = match.name
        else:
            continue
        db.flush()
        changes.append({"name": name, "plan": plan["plan"]})
        done.add(index)
    parsed["saved_diagnoses"] = sorted(done)
    return changes
