"""Re-checking an import that already has saved results against a fresh AI reading of its PDF.

The fresh reading doesn't replace the original one (saved results are tied to its row positions);
it's kept on the import as parsed_data["recheck"] and compared with what's saved:

    same        the saved result matches what the AI read
    different   saved with another value; can be corrected to the AI's
    missing     on the report but not saved (never imported, or deleted since); can be added
    only_saved  saved from this import, but the AI didn't find it (shown for information)

Nothing changes until apply_recheck is called with the rows to apply.

The other things a document holds (vitals, medications, vaccines, diagnoses) are reviewed on the
import's review card instead. merge_sections gives each one with nothing saved from it yet the fresh
reading, so documents imported before the AI reader could read them can be backfilled.
"""

from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..models import Lab, LabResult, PDFImportLog
from .import_review import _numeric, _range_of, find_lab, find_lab_in_unit, row_date

APPLICABLE = ("different", "missing")
# Sections of a reading reviewed on the import's review card; saved positions are kept as saved_<section>
SECTIONS = ("vitals", "medications", "immunizations", "diagnoses")


def merge_sections(parsed: Dict[str, Any], reading: Dict[str, Any]) -> List[str]:
    """Take the fresh reading's sections that have nothing saved yet; returns the sections replaced.

    A section with saved rows keeps its original reading: the saved positions point into it.
    """
    replaced = []
    for section in SECTIONS:
        if parsed.get(f"saved_{section}") or section not in reading:
            continue
        if (parsed.get(section) or []) != (reading.get(section) or []):
            replaced.append(section)
        parsed[section] = reading.get(section) or []
    # A visit date found this time dates the vitals and medication changes
    if not parsed.get("date_collected") and reading.get("date_collected"):
        parsed["date_collected"] = reading["date_collected"]
    return replaced


def _lab_for(test: Dict[str, Any], db: Session) -> Optional[Lab]:
    """The saved test a row belongs to: same name in the same unit, else same name."""
    name, unit = test.get("name"), test.get("unit")
    return find_lab_in_unit(name, unit, db) or find_lab(name, db)


def _value(test: Dict[str, Any]):
    """(number, text) the AI read for a row; both None when it has no result."""
    number = _numeric(test)
    if number is not None:
        return number, None
    text = (test.get("result_text") or str(test.get("result") or "")).strip()
    return None, (text or None)


def _shown(number: Optional[float], text: Optional[str]) -> str:
    return f"{number:g}" if number is not None else (text or "")


def _same(result: LabResult, number: Optional[float], text: Optional[str]) -> bool:
    if number is not None:
        return result.result is not None and abs(result.result - number) < 1e-9
    return result.result is None and (result.result_text or "").strip().lower() == (text or "").lower()


def _day(raw: Optional[str]) -> Optional[str]:
    raw = (raw or "")[:10]
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _patient_of(import_log: PDFImportLog, db: Session, fallback: int) -> int:
    """The patient this import's results were saved for (the selected one if none are left)."""
    row = db.query(LabResult.patient_id).filter(LabResult.pdf_import_id == str(import_log.id)).first()
    return row[0] if row else fallback


def compare_with_saved(import_log: PDFImportLog, reading: Dict[str, Any], patient_id: int, db: Session) -> Dict[str, Any]:
    """Each row of the fresh reading against the saved results, plus saved results it didn't find."""
    patient_id = _patient_of(import_log, db, patient_id)
    report_day = _day(reading.get("date_collected")) or _day(import_log.date_collected)
    saved = (db.query(LabResult).options(joinedload(LabResult.lab).joinedload(Lab.unit))
             .filter(LabResult.pdf_import_id == str(import_log.id)).all())
    claimed = set()
    rows = []
    for index, test in enumerate(reading.get("tests") or []):
        name = (test.get("name") or "").strip()
        number, text = _value(test)
        if not name or (number is None and not text):
            continue
        day = row_date(test) or report_day
        lab = _lab_for(test, db)
        match = None
        if lab is not None and day:
            match = next((r for r in saved if r.id not in claimed and r.lab_id == lab.id
                          and r.date_collected.date().isoformat() == day), None)
            if match is None:
                # Saved some other way (another report, or by hand) for the same day
                match = (db.query(LabResult).filter(LabResult.patient_id == patient_id, LabResult.lab_id == lab.id,
                                                    func.date(LabResult.date_collected) == day).first())
        if match is not None:
            claimed.add(match.id)
        status = "missing" if match is None else ("same" if _same(match, number, text) else "different")
        rows.append({
            "index": index,
            "name": name,
            "lab_name": lab.name if lab else None,
            "unit": (test.get("unit") or "").strip(),
            "date": day,
            "ai_value": _shown(number, text),
            "saved_value": _shown(match.result, match.result_text) if match else None,
            "result_id": match.id if match else None,
            "reference_range": _range_of(test).get("text") or "",
            "status": status,
        })
    only_saved = [{
        "result_id": r.id, "name": r.lab.name if r.lab else "", "value": _shown(r.result, r.result_text),
        "unit": r.lab.unit.name if r.lab and r.lab.unit else "", "date": r.date_collected.date().isoformat(),
    } for r in saved if r.id not in claimed]
    summary = {s: sum(1 for r in rows if r["status"] == s) for s in ("same", "different", "missing")}
    summary["only_saved"] = len(only_saved)
    return {
        "import_id": import_log.id,
        "filename": import_log.filename,
        "date_collected": report_day,
        "rows": rows,
        "only_saved": only_saved,
        "summary": summary,
        "checked_at": reading.get("checked_at"),
        "ai_model": reading.get("ai_model"),
    }


def _details(test: Dict[str, Any]) -> Dict[str, Any]:
    ref = _range_of(test)
    text = (ref.get("text") or "").strip()
    flag = (test.get("flag") or "").strip()
    comment = (test.get("lab_comment") or "").strip()
    return {"ref_low": ref.get("low"), "ref_high": ref.get("high"), "ref_text": text[:100] or None,
            "flag": flag[:20] or None, "lab_comment": comment or None}


def apply_recheck(import_log: PDFImportLog, reading: Dict[str, Any], indices: List[int], provider_id: Optional[int],
                  patient_id: int, create_lab: Callable[[Dict[str, Any], Session], Lab], db: Session) -> Dict[str, Any]:
    """Correct the chosen 'different' rows and add the chosen 'missing' ones. Returns what changed."""
    comparison = compare_with_saved(import_log, reading, patient_id, db)
    patient_id = _patient_of(import_log, db, patient_id)
    by_index = {r["index"]: r for r in comparison["rows"] if r["status"] in APPLICABLE}
    tests = reading.get("tests") or []
    updated, added = [], []
    for index in dict.fromkeys(indices):
        row = by_index.get(index)
        if row is None:
            continue
        test = tests[index]
        number, text = _value(test)
        if row["status"] == "different":
            result = db.query(LabResult).filter(LabResult.id == row["result_id"]).first()
            result.result, result.result_text = number, (None if number is not None else text)
            for key, value in _details(test).items():
                if value is not None:
                    setattr(result, key, value)
            updated.append({"name": row["lab_name"] or row["name"], "from": row["saved_value"], "to": row["ai_value"]})
        else:
            if not row["date"]:
                raise ValueError(f"{row['name']}: the report's collection date is unknown")
            if not provider_id:
                raise ValueError("Choose the provider for the results being added")
            lab = _lab_for(test, db) or create_lab(test, db)
            db.add(LabResult(lab_id=lab.id, patient_id=patient_id, provider_id=provider_id, result=number,
                             result_text=None if number is not None else text,
                             date_collected=datetime.fromisoformat(row["date"]), pdf_import_id=str(import_log.id),
                             notes=f"Added by an AI re-check of {import_log.filename} (AI row {index})", **_details(test)))
            db.flush()
            added.append({"name": lab.name, "value": row["ai_value"], "date": row["date"]})
    return {"updated": updated, "added": added}
