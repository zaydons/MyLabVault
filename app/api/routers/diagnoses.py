"""Diagnoses API router: a patient's conditions, ongoing (the problem list) or resolved."""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Diagnosis as DiagnosisModel, Patient as PatientModel, Provider as ProviderModel

router = APIRouter()


class DiagnosisCreate(BaseModel):
    """Diagnosis create/update schema."""
    patient_id: int
    name: str = Field(..., min_length=1, max_length=255)
    icd10_code: Optional[str] = Field(None, max_length=20)
    status: Literal["active", "resolved"] = "active"
    onset_date: Optional[date] = None
    resolved_date: Optional[date] = None
    provider_id: Optional[int] = None
    notes: Optional[str] = Field(None, max_length=2000)


def _clean(text: Optional[str]) -> Optional[str]:
    return (text or "").strip() or None


def _validate(item: DiagnosisCreate, db: Session) -> dict:
    """Check a diagnosis and return its cleaned column values."""
    if not db.query(PatientModel).filter(PatientModel.id == item.patient_id).first():
        raise HTTPException(status_code=400, detail="Patient not found")
    if item.provider_id is not None and not db.query(ProviderModel).filter(ProviderModel.id == item.provider_id).first():
        raise HTTPException(status_code=400, detail="Provider not found")
    name = item.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Enter the diagnosis")
    today = date.today()
    if (item.onset_date and item.onset_date > today) or (item.resolved_date and item.resolved_date > today):
        raise HTTPException(status_code=400, detail="Dates can't be in the future")
    if item.onset_date and item.resolved_date and item.resolved_date < item.onset_date:
        raise HTTPException(status_code=400, detail="It can't be resolved before it was diagnosed")
    data = item.model_dump()
    data["name"] = name
    data["icd10_code"] = (_clean(item.icd10_code) or "").upper() or None
    data["notes"] = _clean(item.notes)
    if item.status == "active":
        data["resolved_date"] = None
    return data


def _get_or_404(diagnosis_id: int, db: Session) -> DiagnosisModel:
    item = (db.query(DiagnosisModel).options(joinedload(DiagnosisModel.provider))
            .filter(DiagnosisModel.id == diagnosis_id).first())
    if not item:
        raise HTTPException(status_code=404, detail="Diagnosis not found")
    return item


@router.get("/")
def list_diagnoses(patient_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    """Diagnoses, ongoing first, then by most recent onset, optionally for one patient."""
    query = db.query(DiagnosisModel).options(joinedload(DiagnosisModel.provider))
    if patient_id is not None:
        query = query.filter(DiagnosisModel.patient_id == patient_id)
    items = query.all()
    items.sort(key=lambda d: (d.status != "active", -(d.onset_date or date.min).toordinal(), d.name.lower()))
    return [d.to_dict() for d in items]


@router.post("/")
def create_diagnosis(item: DiagnosisCreate, db: Session = Depends(get_db)):
    """Record a diagnosis."""
    row = DiagnosisModel(**_validate(item, db))
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "message": f"{row.name} recorded", "data": row.to_dict()}


@router.put("/{diagnosis_id}")
def update_diagnosis(diagnosis_id: int, item: DiagnosisCreate, db: Session = Depends(get_db)):
    """Change a diagnosis, e.g. mark it resolved."""
    row = _get_or_404(diagnosis_id, db)
    for key, value in _validate(item, db).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return {"success": True, "message": f"{row.name} updated", "data": row.to_dict()}


@router.delete("/{diagnosis_id}")
def delete_diagnosis(diagnosis_id: int, db: Session = Depends(get_db)):
    """Delete a diagnosis."""
    row = _get_or_404(diagnosis_id, db)
    name = row.name
    db.delete(row)
    db.commit()
    return {"success": True, "message": f"{name} deleted"}
