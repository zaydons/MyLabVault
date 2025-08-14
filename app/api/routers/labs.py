"""Labs API router"""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Lab as LabModel, Panel as PanelModel, Unit as UnitModel
from ..schemas import Lab, LabCreate

router = APIRouter()

def _get_lab_or_404(lab_id: int, db: Session) -> LabModel:
    """Get lab by ID or raise 404."""
    lab = db.query(LabModel).filter(LabModel.id == lab_id).first()
    if not lab:
        raise HTTPException(status_code=404, detail="Lab not found")
    return lab


@router.get("/")
def get_labs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None),
    panel_id: Optional[int] = Query(None),
    unit_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """Get lab tests with optional filtering and pagination."""
    query = db.query(LabModel).options(
        joinedload(LabModel.unit),
        joinedload(LabModel.panel),
        joinedload(LabModel.results)
    )

    if search:
        query = query.filter(LabModel.name.contains(search))

    if panel_id:
        query = query.filter(LabModel.panel_id == panel_id)
    
    if unit_id:
        query = query.filter(LabModel.unit_id == unit_id)
    
    labs = query.offset(skip).limit(limit).all()
    return [lab.to_dict() for lab in labs]


@router.post("/")
def create_lab(lab: LabCreate, db: Session = Depends(get_db)):
    """Create a new lab test with validation."""
    panel = db.query(PanelModel).filter(PanelModel.id == lab.panel_id).first()
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")

    if lab.unit_id:
        unit = db.query(UnitModel).filter(UnitModel.id == lab.unit_id).first()
        if not unit:
            raise HTTPException(status_code=404, detail="Unit not found")

    existing = db.query(LabModel).filter(
        LabModel.name == lab.name,
        LabModel.panel_id == lab.panel_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Lab test with this name already exists in this panel")

    db_lab = LabModel(**lab.model_dump())
    db.add(db_lab)
    db.commit()
    db.refresh(db_lab)

    return {
        "success": True,
        "message": f"Lab test '{lab.name}' created successfully",
        "data": db_lab.to_dict()
    }

@router.put("/{lab_id}")
def update_lab(lab_id: int, lab: LabCreate, db: Session = Depends(get_db)):
    """Update an existing lab test with validation."""
    db_lab = _get_lab_or_404(lab_id, db)

    panel = db.query(PanelModel).filter(PanelModel.id == lab.panel_id).first()
    if not panel:
        raise HTTPException(status_code=400, detail="Panel not found")

    unit = db.query(UnitModel).filter(UnitModel.id == lab.unit_id).first()
    if not unit:
        raise HTTPException(status_code=400, detail="Unit not found")

    existing = db.query(LabModel).filter(
        LabModel.name == lab.name,
        LabModel.panel_id == lab.panel_id,
        LabModel.id != lab_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Lab with this name already exists in this panel")

    for key, value in lab.model_dump().items():
        setattr(db_lab, key, value)

    db.commit()
    db.refresh(db_lab)
    return {
        "success": True,
        "message": f"Lab test '{lab.name}' updated successfully",
        "data": db_lab.to_dict()
    }

@router.delete("/{lab_id}")
def delete_lab(lab_id: int, db: Session = Depends(get_db)):
    """Delete a lab test if it has no associated results."""
    db_lab = _get_lab_or_404(lab_id, db)

    if db_lab.results:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete lab test with associated results"
        )

    lab_name = db_lab.name
    db.delete(db_lab)
    db.commit()
    return {
        "success": True,
        "message": f"Lab test '{lab_name}' deleted successfully"
    }

@router.get("/{lab_id}")
def get_lab(lab_id: int, db: Session = Depends(get_db)):
    """Get a specific lab test by ID with full relationship details."""
    lab = db.query(LabModel).options(
        joinedload(LabModel.unit),
        joinedload(LabModel.panel)
    ).filter(LabModel.id == lab_id).first()

    if not lab:
        raise HTTPException(status_code=404, detail="Lab not found")
    return lab
