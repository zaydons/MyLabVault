"""Panel management routes."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Panel as PanelModel, Lab as LabModel
from ..schemas import PanelCreate

router = APIRouter()

def _get_panel_or_404(panel_id: int, db: Session) -> PanelModel:
    """Get panel by ID or raise 404."""
    panel = db.query(PanelModel).filter(PanelModel.id == panel_id).first()
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")
    return panel


@router.get("/")
def get_panels(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Get all lab test panels with optional search and pagination."""
    query = db.query(PanelModel)

    if search:
        query = query.filter(PanelModel.name.contains(search))

    panels = query.offset(skip).limit(limit).all()
    return [panel.to_dict() for panel in panels]

@router.get("/{panel_id}")
def get_panel(panel_id: int, db: Session = Depends(get_db)):
    """Get a specific panel by ID."""
    panel = _get_panel_or_404(panel_id, db)
    return panel.to_dict()

@router.post("/")
def create_panel(panel: PanelCreate, db: Session = Depends(get_db)):
    """Create a new lab test panel."""
    existing = db.query(PanelModel).filter(PanelModel.name == panel.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Panel with this name already exists")

    db_panel = PanelModel(**panel.model_dump())
    db.add(db_panel)
    db.commit()
    db.refresh(db_panel)
    
    return {
        "success": True,
        "message": f"Panel '{panel.name}' created successfully",
        "data": db_panel.to_dict()
    }

@router.put("/{panel_id}")
def update_panel(panel_id: int, panel: PanelCreate, db: Session = Depends(get_db)):
    """Update an existing panel."""
    db_panel = _get_panel_or_404(panel_id, db)

    existing = db.query(PanelModel).filter(
        PanelModel.name == panel.name,
        PanelModel.id != panel_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Panel with this name already exists")

    for key, value in panel.model_dump().items():
        setattr(db_panel, key, value)

    db.commit()
    db.refresh(db_panel)
    
    return {
        "success": True,
        "message": f"Panel '{panel.name}' updated successfully",
        "data": db_panel.to_dict()
    }

@router.delete("/{panel_id}")
def delete_panel(panel_id: int, db: Session = Depends(get_db)):
    """Delete a lab test panel if it has no associated lab tests."""
    db_panel = _get_panel_or_404(panel_id, db)

    if db_panel.labs:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete panel with associated lab tests"
        )

    panel_name = db_panel.name
    db.delete(db_panel)
    db.commit()
    return {
        "success": True,
        "message": f"Panel '{panel_name}' deleted successfully"
    }

@router.get("/{panel_id}/summary")
def get_panel_summary(panel_id: int, db: Session = Depends(get_db)):
    """Get comprehensive summary data for a specific panel."""
    panel = _get_panel_or_404(panel_id, db)

    return {
        "panel": panel.to_dict(),
        "lab_count": panel.get_lab_count(),
        "labs": [lab.to_dict() for lab in panel.labs]
    }
