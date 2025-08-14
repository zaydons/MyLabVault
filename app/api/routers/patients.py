"""Patient management API routes."""

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Patient
from ..schemas import PatientCreate

router = APIRouter()

def _serialize_patient_basic(patient: Patient) -> dict:
    """Serialize patient with basic information."""
    return {
        "id": patient.id,
        "name": patient.name,
        "date_of_birth": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
        "gender": patient.gender,
        "age": patient.get_age()
    }

@router.get("/")
def get_patients(db: Session = Depends(get_db)):
    """Get all patients with basic statistics."""
    patients = db.query(Patient).all()
    return [
        {
            **_serialize_patient_basic(patient),
            "result_count": patient.get_result_count(),
            "recent_results_count": len(patient.get_recent_results(30))
        }
        for patient in patients
    ]

def _get_patient_or_404(patient_id: int, db: Session) -> Patient:
    """Get patient by ID or raise 404."""
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient

@router.get("/{patient_id}")
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    """Get a specific patient by ID with detailed information."""
    patient = _get_patient_or_404(patient_id, db)
    
    return {
        **_serialize_patient_basic(patient),
        "result_count": patient.get_result_count(),
        "abnormal_results_count": len(patient.get_abnormal_results()),
        "recent_results": [
            {
                "id": result.id,
                "lab_name": result.lab.name if result.lab else "Unknown",
                "result": result.result,
                "result_text": result.result_text,
                "date_collected": result.date_collected.isoformat(),
                "is_normal": result.is_normal,
                "status": result.status
            }
            for result in patient.get_recent_results(10)
        ]
    }

@router.post("/")
def create_patient(patient: PatientCreate, db: Session = Depends(get_db)):
    """Create a new patient."""
    # Check if patient with same name already exists
    existing = db.query(Patient).filter(Patient.name == patient.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Patient with this name already exists")
    
    db_patient = Patient(
        name=patient.name,
        date_of_birth=patient.date_of_birth,
        gender=patient.gender
    )
    
    db.add(db_patient)
    db.commit()
    db.refresh(db_patient)
    
    return {
        "success": True,
        "message": f"Patient '{patient.name}' created successfully",
        "data": _serialize_patient_basic(db_patient)
    }

@router.put("/{patient_id}")
def update_patient(patient_id: int, patient: PatientCreate, db: Session = Depends(get_db)):
    """Update an existing patient."""
    db_patient = _get_patient_or_404(patient_id, db)
    
    # Check if another patient with same name exists
    existing = db.query(Patient).filter(
        Patient.name == patient.name,
        Patient.id != patient_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Another patient with this name already exists")
    
    db_patient.name = patient.name
    db_patient.date_of_birth = patient.date_of_birth
    db_patient.gender = patient.gender
    
    db.commit()
    db.refresh(db_patient)
    
    return {
        "success": True,
        "message": f"Patient '{patient.name}' updated successfully",
        "data": _serialize_patient_basic(db_patient)
    }

@router.delete("/{patient_id}")
def delete_patient(patient_id: int, db: Session = Depends(get_db)):
    """Delete a patient if they have no associated results."""
    db_patient = _get_patient_or_404(patient_id, db)
    
    if db_patient.results:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete patient with associated lab results"
        )
    
    patient_name = db_patient.name
    db.delete(db_patient)
    db.commit()
    
    return {
        "success": True,
        "message": f"Patient '{patient_name}' deleted successfully"
    }

@router.get("/{patient_id}/summary")
def get_patient_summary(patient_id: int, db: Session = Depends(get_db)):
    """Get a comprehensive summary of a patient's lab results."""
    patient = _get_patient_or_404(patient_id, db)
    
    recent_results = patient.get_recent_results(50)
    abnormal_results = patient.get_abnormal_results()
    
    # Group results by lab test
    lab_groups = {}
    for result in recent_results:
        if result.lab:
            lab_name = result.lab.name
            if lab_name not in lab_groups:
                lab_groups[lab_name] = []
            lab_groups[lab_name].append({
                "result": result.result,
                "result_text": result.result_text,
                "date_collected": result.date_collected.isoformat(),
                "is_normal": result.is_normal,
                "status": result.status
            })
    
    return {
        "patient": _serialize_patient_basic(patient),
        "statistics": {
            "total_results": len(patient.results),
            "recent_results": len(recent_results),
            "abnormal_results": len(abnormal_results),
            "unique_tests": len(lab_groups)
        },
        "lab_groups": lab_groups,
        "recent_abnormal": [
            {
                "lab_name": result.lab.name if result.lab else "Unknown",
                "result": result.result,
                "result_text": result.result_text,
                "date_collected": result.date_collected.isoformat(),
                "status": result.status
            }
            for result in abnormal_results[:10]
        ]
    }