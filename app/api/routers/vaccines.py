"""Vaccines API router: immunizations given to a patient, one row per dose."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Immunization as ImmunizationModel, Patient as PatientModel, Provider as ProviderModel

router = APIRouter()

# Suggested on the form (with names entered before); any name is accepted
COMMON_VACCINES = [
    "COVID-19", "Influenza (flu)", "Tdap (tetanus, diphtheria, pertussis)", "Td (tetanus, diphtheria)",
    "Shingles (zoster)", "Pneumococcal", "RSV", "Hepatitis A", "Hepatitis B", "HPV", "MMR (measles, mumps, rubella)",
    "Varicella (chickenpox)", "Meningococcal ACWY", "Meningococcal B", "Polio (IPV)", "Hib", "Rotavirus",
    "Typhoid", "Yellow fever", "Japanese encephalitis", "Rabies", "Cholera", "Mpox",
]
SITES = ["left arm", "right arm", "left thigh", "right thigh", "oral", "nasal"]


class ImmunizationCreate(BaseModel):
    """Vaccine dose create/update schema."""
    patient_id: int
    vaccine: str = Field(..., min_length=1, max_length=255)
    date_given: date
    dose: Optional[str] = Field(None, max_length=50)
    manufacturer: Optional[str] = Field(None, max_length=100)
    lot_number: Optional[str] = Field(None, max_length=50)
    site: Optional[str] = Field(None, max_length=50)
    provider_id: Optional[int] = None
    location: Optional[str] = Field(None, max_length=255)
    next_due: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=2000)


def _clean(text: Optional[str]) -> Optional[str]:
    return (text or "").strip() or None


def _validate(shot: ImmunizationCreate, db: Session) -> dict:
    """Check a vaccine dose and return its cleaned column values."""
    if not db.query(PatientModel).filter(PatientModel.id == shot.patient_id).first():
        raise HTTPException(status_code=400, detail="Patient not found")
    if shot.provider_id is not None and not db.query(ProviderModel).filter(ProviderModel.id == shot.provider_id).first():
        raise HTTPException(status_code=400, detail="Provider not found")
    vaccine = shot.vaccine.strip()
    if not vaccine:
        raise HTTPException(status_code=400, detail="Enter the vaccine")
    if shot.date_given > date.today():
        raise HTTPException(status_code=400, detail="The date given is in the future; record the next dose as Next dose due instead")
    if shot.next_due and shot.next_due <= shot.date_given:
        raise HTTPException(status_code=400, detail="The next dose has to be due after this one was given")
    data = shot.model_dump()
    data["vaccine"] = vaccine
    for key in ("dose", "manufacturer", "lot_number", "site", "location", "notes"):
        data[key] = _clean(data[key])
    return data


def _get_or_404(immunization_id: int, db: Session) -> ImmunizationModel:
    shot = (db.query(ImmunizationModel).options(joinedload(ImmunizationModel.provider))
            .filter(ImmunizationModel.id == immunization_id).first())
    if not shot:
        raise HTTPException(status_code=404, detail="Vaccine not found")
    return shot


@router.get("/options")
def get_options(patient_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    """Suggestions for the form: this patient's vaccines first, then others entered before, then common vaccines."""
    entered = {v for (v,) in db.query(ImmunizationModel.vaccine).distinct()}
    mine = {v for (v,) in db.query(ImmunizationModel.vaccine).filter(ImmunizationModel.patient_id == patient_id).distinct()} if patient_id else set()
    names = sorted(entered, key=lambda v: (v not in mine, v.lower()))
    known = {v.lower() for v in names}
    return {"vaccines": names + [v for v in COMMON_VACCINES if v.lower() not in known], "sites": SITES}


@router.get("/")
def list_immunizations(patient_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    """Vaccine doses, most recent first, optionally for one patient."""
    query = db.query(ImmunizationModel).options(joinedload(ImmunizationModel.provider))
    if patient_id is not None:
        query = query.filter(ImmunizationModel.patient_id == patient_id)
    return [s.to_dict() for s in query.order_by(ImmunizationModel.date_given.desc(), ImmunizationModel.id.desc()).all()]


@router.post("/")
def create_immunization(shot: ImmunizationCreate, db: Session = Depends(get_db)):
    """Record a vaccine dose."""
    db_shot = ImmunizationModel(**_validate(shot, db))
    db.add(db_shot)
    db.commit()
    db.refresh(db_shot)
    return {"success": True, "message": f"{db_shot.vaccine} recorded", "data": db_shot.to_dict()}


@router.put("/{immunization_id}")
def update_immunization(immunization_id: int, shot: ImmunizationCreate, db: Session = Depends(get_db)):
    """Correct a vaccine dose."""
    db_shot = _get_or_404(immunization_id, db)
    for key, value in _validate(shot, db).items():
        setattr(db_shot, key, value)
    db.commit()
    db.refresh(db_shot)
    return {"success": True, "message": f"{db_shot.vaccine} updated", "data": db_shot.to_dict()}


@router.delete("/{immunization_id}")
def delete_immunization(immunization_id: int, db: Session = Depends(get_db)):
    """Delete a vaccine dose."""
    db_shot = _get_or_404(immunization_id, db)
    db.delete(db_shot)
    db.commit()
    return {"success": True, "message": "Vaccine deleted"}
