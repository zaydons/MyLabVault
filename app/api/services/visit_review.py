"""Vitals and medications read from a visit summary or health summary: review rows and saving.

The AI reader returns them alongside lab results (see ai_parser). On the review screen each vital is
a row to tick, and each medication comes with a plan worked out against the patient's medication
list: add it, change its dose, mark it stopped, or nothing to do. Nothing is saved until confirmed;
the positions saved are kept in the import's parsed data (saved_vitals, saved_medications).
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Medication, Provider, Vital
from ..routers.vitals import VITAL_TYPES, convert, format_reading, preferred_units

# What the plan for a medication does, shown on the review screen
PLAN_ADD, PLAN_CHANGE, PLAN_STOP, PLAN_NONE = "add", "change_dose", "stop", "none"


def _day(raw: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat((raw or "")[:10])
    except ValueError:
        return None


# ---------- vitals ----------

def _same_reading(vital_type: str, a: Optional[float], a_unit: Optional[str], b: Optional[float], b_unit: Optional[str]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    base = next(iter(VITAL_TYPES[vital_type]["units"]))
    return abs(convert(vital_type, a, a_unit, base) - convert(vital_type, b, b_unit, base)) < 0.05


def _vital_saved(vital: Dict[str, Any], day: Optional[date], patient_id: int, db: Session) -> bool:
    """A reading of this type with the same value is already saved for this patient on this date."""
    if day is None:
        return False
    for saved in db.query(Vital).filter(Vital.patient_id == patient_id, Vital.vital_type == vital["vital_type"],
                                        func.date(Vital.measured_at) == day.isoformat()):
        if (_same_reading(vital["vital_type"], saved.value, saved.unit, vital["value"], vital["unit"])
                and _same_reading(vital["vital_type"], saved.value2, saved.unit, vital.get("value2"), vital["unit"])):
            return True
    return False


def vital_rows(parsed: Dict[str, Any], patient_id: int, db: Session) -> List[Dict[str, Any]]:
    """One row per vital read from the document, in the user's preferred units."""
    units = preferred_units(db)
    report_day = _day(parsed.get("date_collected"))
    done = set(parsed.get("saved_vitals") or [])
    rows = []
    for index, vital in enumerate(parsed.get("vitals") or []):
        config = VITAL_TYPES.get(vital.get("vital_type"))
        if not config:
            continue
        own = _day(vital.get("date"))
        imported = index in done
        rows.append({
            "index": index,
            "vital_type": vital["vital_type"],
            "label": config["label"],
            "reading": format_reading(vital["vital_type"], vital["value"], vital.get("value2"), vital.get("unit"),
                                      units[vital["vital_type"]]),
            "date": own.isoformat() if own else None,
            "already_imported": imported,
            "already_saved": not imported and _vital_saved(vital, own or report_day, patient_id, db),
        })
    return rows


def save_vitals(parsed: Dict[str, Any], indices: List[int], visit_day: Optional[date], patient_id: int,
                note: str, db: Session) -> List[Vital]:
    """Add the chosen vitals; each is dated by its own date, else the visit's."""
    vitals = parsed.get("vitals") or []
    done = set(parsed.get("saved_vitals") or [])
    saved = []
    for index in dict.fromkeys(indices):
        if index < 0 or index >= len(vitals) or index in done or vitals[index].get("vital_type") not in VITAL_TYPES:
            continue
        vital = vitals[index]
        day = _day(vital.get("date")) or visit_day
        if day is None:
            raise ValueError("Enter the visit date to save its vitals.")
        row = Vital(patient_id=patient_id, vital_type=vital["vital_type"], value=vital["value"], value2=vital.get("value2"),
                    unit=vital.get("unit"), measured_at=datetime.combine(day, datetime.min.time()), notes=note)
        db.add(row)
        saved.append(row)
        done.add(index)
    parsed["saved_vitals"] = sorted(done)
    return saved


# ---------- medications ----------

def _med_key(name: Optional[str]) -> str:
    """The drug's first word, e.g. 'sertraline' for 'Sertraline HCL 25 mg'."""
    words = re.findall(r"[a-z]+", (name or "").lower())
    return words[0] if words else ""


def _cut(text: Optional[str], length: int) -> Optional[str]:
    return (text or "").strip()[:length] or None


def _dose_key(dose: Optional[str]) -> str:
    return re.sub(r"\s+", "", (dose or "").lower())


def _find_current(med: Dict[str, Any], current: List[Medication]) -> Optional[Medication]:
    wanted = (med.get("matched_medication") or "").strip().lower()
    for m in current:
        if wanted and m.name.strip().lower() == wanted:
            return m
    key = _med_key(med.get("name"))
    return next((m for m in current if key and _med_key(m.name) == key), None)


def plan_medication(med: Dict[str, Any], current: List[Medication], visit_day: Optional[date]) -> Dict[str, Any]:
    """What confirming this medication would do to the patient's list, with a sentence saying so."""
    match = _find_current(med, current)
    action = med.get("action") or "listed"
    if action == "stop":
        if match is None:
            return {"plan": PLAN_NONE, "medication": None, "note": "Not on your medication list, so there's nothing to stop."}
        if visit_day and visit_day < match.start_date:
            return {"plan": PLAN_NONE, "medication": match, "note": f"{match.name} on your list started after this visit."}
        return {"plan": PLAN_STOP, "medication": match, "note": f"{match.name} will be marked stopped on the visit date."}
    if match is None:
        return {"plan": PLAN_ADD, "medication": None,
                "note": "Not on your medication list yet; it will be added, starting on the visit date."}
    if not med.get("dose") or _dose_key(med.get("dose")) == _dose_key(match.dose):
        return {"plan": PLAN_NONE, "medication": match, "note": f"Already on your list as {match.name}{f' {match.dose}' if match.dose else ''}."}
    if visit_day and visit_day <= match.start_date:
        return {"plan": PLAN_NONE, "medication": match, "note": f"{match.name} on your list was recorded at a dose starting after this visit."}
    return {"plan": PLAN_CHANGE, "medication": match,
            "note": f"On your list at {match.dose or 'no dose'}; the dose changes to {med['dose']} from the visit date."}


def current_medications(patient_id: int, db: Session) -> List[Medication]:
    return [m for m in db.query(Medication).filter(Medication.patient_id == patient_id)
            .order_by(Medication.start_date.desc(), Medication.id.desc()).all() if m.is_current()]


def medication_rows(parsed: Dict[str, Any], patient_id: int, db: Session) -> List[Dict[str, Any]]:
    """One row per medication read from the document, with what confirming it would do."""
    current = current_medications(patient_id, db)
    visit_day = _day(parsed.get("date_collected"))
    done = set(parsed.get("saved_medications") or [])
    rows = []
    for index, med in enumerate(parsed.get("medications") or []):
        plan = plan_medication(med, current, visit_day)
        rows.append({
            "index": index,
            "name": med.get("name"),
            "dose": med.get("dose"),
            "frequency": med.get("frequency"),
            "action": med.get("action") or "listed",
            "prescriber": med.get("prescriber"),
            "reason": med.get("reason"),
            "plan": plan["plan"],
            "note": plan["note"],
            "medication_id": plan["medication"].id if plan["medication"] else None,
            "already_imported": index in done,
        })
    return rows


def save_medications(parsed: Dict[str, Any], indices: List[int], visit_day: Optional[date], patient_id: int,
                     provider_for: Callable[[Optional[str]], Optional[Provider]], default_provider_id: Optional[int],
                     note: str, db: Session) -> List[Dict[str, Any]]:
    """Apply the plan of each chosen medication; returns what changed, e.g. {"name", "plan"}."""
    meds = parsed.get("medications") or []
    done = set(parsed.get("saved_medications") or [])
    changes = []
    for index in dict.fromkeys(indices):
        if index < 0 or index >= len(meds) or index in done:
            continue
        if visit_day is None:
            raise ValueError("Enter the visit date to update medications.")
        med = meds[index]
        plan = plan_medication(med, current_medications(patient_id, db), visit_day)
        match = plan["medication"]
        prescriber = provider_for(med.get("prescriber"))
        provider_id = prescriber.id if prescriber else default_provider_id
        if plan["plan"] == PLAN_ADD:
            db.add(Medication(patient_id=patient_id, name=med["name"][:255], kind="medication", dose=_cut(med.get("dose"), 100),
                              frequency=_cut(med.get("frequency"), 100), route=_cut(med.get("route"), 50), start_date=visit_day,
                              reason=(med.get("reason") or "")[:255] or None, provider_id=provider_id, notes=note))
        elif plan["plan"] == PLAN_STOP:
            match.end_date = visit_day
        elif plan["plan"] == PLAN_CHANGE:
            db.add(Medication(patient_id=patient_id, name=match.name, kind=match.kind, dose=_cut(med.get("dose"), 100),
                              frequency=_cut(med.get("frequency"), 100) or match.frequency, route=match.route, start_date=visit_day,
                              end_date=match.end_date, reason=match.reason, provider_id=provider_id or match.provider_id,
                              notes=note))
            match.end_date = visit_day - timedelta(days=1)
        else:
            continue
        db.flush()
        changes.append({"name": match.name if match else med["name"], "plan": plan["plan"], "dose": med.get("dose")})
        done.add(index)
    parsed["saved_medications"] = sorted(done)
    return changes
