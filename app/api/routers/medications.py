"""Medications API router: medications and supplements a patient takes, one row per dose period.

A dose change ends the current period the day before the change and starts a new one with the new
dose, so the rows with the same name make up a medication's history:

    Testosterone cypionate  100 mg  weekly  2026-01-05 → 2026-06-02
    Testosterone cypionate  140 mg  weekly  2026-06-03 → (still taking)
"""

from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..logging_setup import audit
from ..models import Medication as MedicationModel, Patient as PatientModel, Provider as ProviderModel

router = APIRouter()

# Suggestions offered on the form; any text is accepted
FREQUENCIES = ["once daily", "twice daily", "three times daily", "every other day", "weekly", "twice weekly",
               "every 2 weeks", "monthly", "as needed"]
ROUTES = ["by mouth", "injection", "topical", "under the tongue", "inhaled", "nasal spray", "eye drops", "patch"]


class MedicationCreate(BaseModel):
    """Medication create/update schema (one dose period)."""
    patient_id: int
    name: str = Field(..., min_length=1, max_length=255)
    kind: Literal["medication", "supplement"] = "medication"
    dose: Optional[str] = Field(None, max_length=100)
    frequency: Optional[str] = Field(None, max_length=100)
    route: Optional[str] = Field(None, max_length=50)
    start_date: date
    end_date: Optional[date] = None
    reason: Optional[str] = Field(None, max_length=255)
    provider_id: Optional[int] = None
    notes: Optional[str] = Field(None, max_length=2000)


class DoseChange(BaseModel):
    """A new dose from change_date on; the current period ends the day before."""
    dose: Optional[str] = Field(None, max_length=100)
    frequency: Optional[str] = Field(None, max_length=100)
    change_date: date
    notes: Optional[str] = Field(None, max_length=2000)


class StopMedication(BaseModel):
    end_date: date


def _clean(text: Optional[str]) -> Optional[str]:
    return (text or "").strip() or None


def _validate(med: MedicationCreate, db: Session) -> dict:
    """Check a medication and return its cleaned column values."""
    if not db.query(PatientModel).filter(PatientModel.id == med.patient_id).first():
        raise HTTPException(status_code=400, detail="Patient not found")
    if med.provider_id is not None and not db.query(ProviderModel).filter(ProviderModel.id == med.provider_id).first():
        raise HTTPException(status_code=400, detail="Prescriber not found")
    name = med.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Enter the medication's name")
    if med.end_date and med.end_date < med.start_date:
        raise HTTPException(status_code=400, detail="The stop date is before the start date")
    data = med.model_dump()
    data["name"] = name
    for key in ("dose", "frequency", "route", "reason", "notes"):
        data[key] = _clean(data[key])
    return data


def _get_or_404(medication_id: int, db: Session) -> MedicationModel:
    med = db.query(MedicationModel).options(joinedload(MedicationModel.provider)).filter(MedicationModel.id == medication_id).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medication not found")
    return med


@router.get("/options")
def get_options(patient_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    """Suggestions for the form: names entered before (this patient's first), frequencies and routes."""
    names = {n for (n,) in db.query(MedicationModel.name).distinct()}
    mine = {n for (n,) in db.query(MedicationModel.name).filter(MedicationModel.patient_id == patient_id).distinct()} if patient_id else set()
    return {"names": sorted(names, key=lambda n: (n not in mine, n.lower())), "frequencies": FREQUENCIES, "routes": ROUTES}


@router.get("/")
def list_medications(
    patient_id: Optional[int] = Query(None),
    status: Optional[Literal["current", "past"]] = Query(None),
    db: Session = Depends(get_db),
):
    """Medication dose periods, newest start first, optionally for one patient and only current or past ones."""
    query = db.query(MedicationModel).options(joinedload(MedicationModel.provider))
    if patient_id is not None:
        query = query.filter(MedicationModel.patient_id == patient_id)
    meds = query.order_by(MedicationModel.start_date.desc(), MedicationModel.id.desc()).all()
    if status:
        meds = [m for m in meds if m.is_current() == (status == "current")]
    return [m.to_dict() for m in meds]


@router.post("/")
def create_medication(med: MedicationCreate, db: Session = Depends(get_db)):
    """Record a medication or supplement."""
    db_med = MedicationModel(**_validate(med, db))
    db.add(db_med)
    db.commit()
    db.refresh(db_med)
    return {"success": True, "message": f"{db_med.name} added", "data": db_med.to_dict()}


@router.put("/{medication_id}")
def update_medication(medication_id: int, med: MedicationCreate, db: Session = Depends(get_db)):
    """Correct a dose period (to record a new dose, use change-dose instead)."""
    db_med = _get_or_404(medication_id, db)
    for key, value in _validate(med, db).items():
        setattr(db_med, key, value)
    db.commit()
    db.refresh(db_med)
    return {"success": True, "message": f"{db_med.name} updated", "data": db_med.to_dict()}


@router.post("/{medication_id}/change-dose")
def change_dose(medication_id: int, change: DoseChange, db: Session = Depends(get_db)):
    """End this dose period the day before change_date and start a new one with the new dose.

    Example: POST /api/medications/4/change-dose {"dose": "140 mg", "change_date": "2026-06-03"}
    """
    current = _get_or_404(medication_id, db)
    if change.change_date <= current.start_date:
        raise HTTPException(status_code=400, detail="The new dose has to start after the current one started")
    if current.end_date and change.change_date > current.end_date + timedelta(days=1):
        raise HTTPException(status_code=400, detail="This dose had already stopped by then; add it as a new medication instead")
    dose, frequency = _clean(change.dose), _clean(change.frequency) or current.frequency
    if dose == current.dose and frequency == current.frequency:
        raise HTTPException(status_code=400, detail="Enter the new dose or how often it's taken")

    new = MedicationModel(
        patient_id=current.patient_id, name=current.name, kind=current.kind, dose=dose, frequency=frequency,
        route=current.route, start_date=change.change_date, end_date=current.end_date, reason=current.reason,
        provider_id=current.provider_id, notes=_clean(change.notes),
    )
    current.end_date = change.change_date - timedelta(days=1)
    db.add(new)
    db.commit()
    db.refresh(new)
    db.refresh(current)
    audit("medication.dose_changed", medication_id=current.id, new_id=new.id, patient_id=new.patient_id)
    return {"success": True, "message": f"{new.name} dose changed", "data": {"previous": current.to_dict(), "current": new.to_dict()}}


@router.post("/{medication_id}/stop")
def stop_medication(medication_id: int, stop: StopMedication, db: Session = Depends(get_db)):
    """Record the date a medication was (or will be) stopped."""
    med = _get_or_404(medication_id, db)
    if stop.end_date < med.start_date:
        raise HTTPException(status_code=400, detail="The stop date is before the start date")
    med.end_date = stop.end_date
    db.commit()
    db.refresh(med)
    return {"success": True, "message": f"{med.name} stopped", "data": med.to_dict()}


@router.delete("/{medication_id}")
def delete_medication(medication_id: int, db: Session = Depends(get_db)):
    """Delete one dose period (a mistake). Stopping it keeps the history instead."""
    med = _get_or_404(medication_id, db)
    db.delete(med)
    db.commit()
    return {"success": True, "message": "Medication deleted"}
