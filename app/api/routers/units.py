"""Unit management routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Unit as UnitModel
from ..schemas import UnitCreate

router = APIRouter()

def _get_unit_or_404(unit_id: int, db: Session) -> UnitModel:
    """Get unit by ID or raise 404."""
    unit = db.query(UnitModel).filter(UnitModel.id == unit_id).first()
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    return unit

@router.get("/")
def get_units(db: Session = Depends(get_db)):
    """Get all measurement units in the system."""
    units = db.query(UnitModel).options(
        joinedload(UnitModel.labs)
    ).all()
    return [unit.to_dict() for unit in units]

@router.post("/")
def create_unit(unit: UnitCreate, db: Session = Depends(get_db)):
    """Create a new measurement unit with duplicate name validation."""
    existing_unit = db.query(UnitModel).filter(UnitModel.name == unit.name).first()
    if existing_unit:
        raise HTTPException(status_code=400, detail="Unit already exists")

    db_unit = UnitModel(**unit.model_dump())
    db.add(db_unit)
    db.commit()
    db.refresh(db_unit)
    
    return {
        "success": True,
        "message": f"Unit '{unit.name}' created successfully",
        "data": db_unit.to_dict()
    }

@router.get("/{unit_id}")
def get_unit(unit_id: int, db: Session = Depends(get_db)):
    """Get a specific unit."""
    unit = _get_unit_or_404(unit_id, db)
    return unit.to_dict()

@router.put("/{unit_id}")
def update_unit(unit_id: int, unit: UnitCreate, db: Session = Depends(get_db)):
    """Update a unit."""
    db_unit = _get_unit_or_404(unit_id, db)

    for field, value in unit.model_dump(exclude_unset=True).items():
        setattr(db_unit, field, value)

    db.commit()
    db.refresh(db_unit)
    return {
        "success": True,
        "message": f"Unit '{unit.name}' updated successfully",
        "data": db_unit.to_dict()
    }

@router.delete("/{unit_id}")
def delete_unit(unit_id: int, db: Session = Depends(get_db)):
    """Delete a measurement unit."""
    db_unit = _get_unit_or_404(unit_id, db)
    
    unit_name = db_unit.name
    db.delete(db_unit)
    db.commit()
    return {
        "success": True,
        "message": f"Unit '{unit_name}' deleted successfully"
    }

