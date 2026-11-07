"""Vaccines read from an immunization record or a visit or health summary: review rows and saving.

The AI reader returns them alongside lab results (see ai_parser). On the review screen each dose is
a row to tick. A dose already recorded for the same vaccine on the same date is marked and left
unticked; a dose without a date can't be saved until one is entered. Nothing is saved until
confirmed; the positions saved are kept in the import's parsed data (saved_immunizations).
"""

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import Immunization
from ..routers.vaccines import COMMON_VACCINES


def _day(raw: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat((raw or "")[:10])
    except ValueError:
        return None


def _cut(text: Optional[str], length: int) -> Optional[str]:
    return (text or "").strip()[:length] or None


def known_vaccines(patient_id: int, db: Session) -> List[str]:
    """Names the AI reader can match a printed vaccine to: this patient's, then the common ones."""
    mine = sorted({v for (v,) in db.query(Immunization.vaccine).filter(Immunization.patient_id == patient_id).distinct()},
                  key=str.lower)
    taken = {v.lower() for v in mine}
    return mine + [v for v in COMMON_VACCINES if v.lower() not in taken]


def vaccine_name(shot: Dict[str, Any]) -> str:
    """The name a dose is saved under: the matched name when there is one, else as printed."""
    return ((shot.get("matched_vaccine") or "").strip() or (shot.get("vaccine") or "").strip())[:255]


def _already_saved(name: str, day: Optional[date], patient_id: int, db: Session) -> bool:
    """A dose of this vaccine is already recorded for this patient on this date."""
    if day is None or not name:
        return False
    return any(v.strip().lower() == name.lower() for (v,) in db.query(Immunization.vaccine).filter(
        Immunization.patient_id == patient_id, Immunization.date_given == day))


def immunization_rows(parsed: Dict[str, Any], patient_id: int, db: Session) -> List[Dict[str, Any]]:
    """One row per vaccine dose read from the document."""
    done = set(parsed.get("saved_immunizations") or [])
    rows = []
    for index, shot in enumerate(parsed.get("immunizations") or []):
        name = vaccine_name(shot)
        if not name:
            continue
        given = _day(shot.get("date_given"))
        imported = index in done
        rows.append({
            "index": index,
            "vaccine": (shot.get("vaccine") or "").strip() or name,
            "name": name,
            "date_given": given.isoformat() if given else None,
            "dose": shot.get("dose"),
            "manufacturer": shot.get("manufacturer"),
            "lot_number": shot.get("lot_number"),
            "site": shot.get("site"),
            "location": shot.get("location"),
            "next_due": shot.get("next_due"),
            "already_imported": imported,
            "already_saved": not imported and _already_saved(name, given, patient_id, db),
        })
    return rows


def check_immunizations(parsed: Dict[str, Any], indices: List[int], dates: Dict[str, str],
                        today: Optional[date] = None) -> List[str]:
    """Problems that stop the chosen doses being saved, e.g. a dose with no date entered."""
    today = today or date.today()
    shots = parsed.get("immunizations") or []
    done = set(parsed.get("saved_immunizations") or [])
    problems = []
    for index in dict.fromkeys(indices):
        if index < 0 or index >= len(shots) or index in done:
            continue
        name = vaccine_name(shots[index]) or f"Vaccine {index + 1}"
        given = _day(dates.get(str(index))) or _day(shots[index].get("date_given"))
        if given is None:
            problems.append(f"{name}: enter the date it was given")
        elif given > today:
            problems.append(f"{name}: the date given is in the future")
    return problems


def save_immunizations(parsed: Dict[str, Any], indices: List[int], dates: Dict[str, str], patient_id: int,
                       note: str, db: Session) -> List[Dict[str, Any]]:
    """Add the chosen doses (call check_immunizations first); a dose already recorded is not added twice.

    dates: dates entered on the review for doses the document gives none, keyed by position.
    Returns what was saved, e.g. {"name", "date_given"}.
    """
    shots = parsed.get("immunizations") or []
    done = set(parsed.get("saved_immunizations") or [])
    saved = []
    for index in dict.fromkeys(indices):
        if index < 0 or index >= len(shots) or index in done:
            continue
        shot = shots[index]
        name = vaccine_name(shot)
        given = _day(dates.get(str(index))) or _day(shot.get("date_given"))
        if not name or given is None:
            continue
        done.add(index)
        if _already_saved(name, given, patient_id, db):
            continue
        next_due = _day(shot.get("next_due"))
        db.add(Immunization(patient_id=patient_id, vaccine=name, date_given=given, dose=_cut(shot.get("dose"), 50),
                            manufacturer=_cut(shot.get("manufacturer"), 100), lot_number=_cut(shot.get("lot_number"), 50),
                            site=_cut(shot.get("site"), 50), location=_cut(shot.get("location"), 255),
                            next_due=next_due if next_due and next_due > given else None, notes=note))
        db.flush()
        saved.append({"name": name, "date_given": given.isoformat()})
    parsed["saved_immunizations"] = sorted(done)
    return saved
