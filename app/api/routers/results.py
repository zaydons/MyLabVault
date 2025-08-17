"""Lab Results API router"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc
from typing import Optional

from ..database import get_db
from ..models import (
    LabResult as LabResultModel,
    Lab as LabModel,
    Provider as ProviderModel,
    Patient as PatientModel,
    Panel as PanelModel
)
from ..schemas import (
    LabResultCreate,
    LabResultWithDetails,
    PaginatedLabResults
)
router = APIRouter()

def _get_result_or_404(result_id: int, db: Session) -> LabResultModel:
    """Get result by ID or raise 404."""
    result = db.query(LabResultModel).options(
        joinedload(LabResultModel.lab).joinedload(LabModel.unit),
        joinedload(LabResultModel.lab).joinedload(LabModel.panel),
        joinedload(LabResultModel.patient),
        joinedload(LabResultModel.provider),
        joinedload(LabResultModel.pdf_import)
    ).filter(LabResultModel.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Lab result not found")
    return result

def _build_results_query(
    db: Session,
    lab_id: Optional[int] = None,
    provider_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    pdf_import_id: Optional[str] = None
):
    """Build filtered query for lab results with eager loading."""
    query = db.query(LabResultModel).options(
        joinedload(LabResultModel.lab).joinedload(LabModel.unit),
        joinedload(LabResultModel.lab).joinedload(LabModel.panel),
        joinedload(LabResultModel.patient),
        joinedload(LabResultModel.provider),
        joinedload(LabResultModel.pdf_import)
    )

    if lab_id:
        query = query.filter(LabResultModel.lab_id == lab_id)
    if provider_id:
        query = query.filter(LabResultModel.provider_id == provider_id)
    if date_from:
        query = query.filter(LabResultModel.date_collected >= date_from)
    if date_to:
        query = query.filter(LabResultModel.date_collected <= date_to)
    if pdf_import_id:
        query = query.filter(LabResultModel.pdf_import_id == pdf_import_id)

    return query

@router.get("/")
def get_results(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    lab_id: Optional[int] = Query(None),
    provider_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    pdf_import_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Get lab results with optional filtering and pagination."""
    base_query = _build_results_query(db, lab_id, provider_id, date_from, date_to, pdf_import_id)
    
    total_count = base_query.count()
    results = base_query.order_by(desc(LabResultModel.date_collected)).offset(skip).limit(limit).all()
    
    page = (skip // limit) + 1 if limit > 0 else 1
    total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
    has_next = skip + len(results) < total_count
    has_prev = skip > 0
    
    return PaginatedLabResults(
        results=results,
        total_count=total_count,
        page=page,
        page_size=limit,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=has_prev
    )


@router.get("/{result_id}")
def get_result(result_id: int, db: Session = Depends(get_db)):
    """Get a specific lab result by ID with full relationship details."""
    return _get_result_or_404(result_id, db)


@router.put("/{result_id}")
def update_result(result_id: int, result: LabResultCreate, db: Session = Depends(get_db)):
    """Update an existing lab result with validation."""
    db_result = _get_result_or_404(result_id, db)

    # Validate relationships
    lab = db.query(LabModel).filter(LabModel.id == result.lab_id).first()
    if not lab:
        raise HTTPException(status_code=400, detail="Lab not found")

    provider = db.query(ProviderModel).filter(ProviderModel.id == result.provider_id).first()
    if not provider:
        raise HTTPException(status_code=400, detail="Provider not found")

    patient = db.query(PatientModel).filter(PatientModel.id == result.patient_id).first()
    if not patient:
        raise HTTPException(status_code=400, detail="Patient not found")

    for key, value in result.model_dump().items():
        if key != 'pdf_filename':
            setattr(db_result, key, value)

    db.commit()
    db.refresh(db_result)
    return {
        "success": True,
        "message": "Lab result updated successfully",
        "data": db_result.to_dict()
    }

@router.delete("/{result_id}")
def delete_result(result_id: int, db: Session = Depends(get_db)):
    """Delete a lab result by ID."""
    db_result = _get_result_or_404(result_id, db)
    
    db.delete(db_result)
    db.commit()
    return {
        "success": True,
        "message": "Lab result deleted successfully"
    }



@router.get("/charts/panel/{panel_id}")
def get_panel_charts_data(panel_id: int, request: Request, db: Session = Depends(get_db)):
    """Get chart data for all labs in a panel."""
    # Get selected patient from cookie
    try:
        patient_id = int(request.cookies.get("selectedPatientId", "1"))
    except (ValueError, TypeError):
        patient_id = 1
    # Verify panel exists
    panel = db.query(PanelModel).filter(PanelModel.id == panel_id).first()
    if not panel:
        raise HTTPException(status_code=404, detail="Panel not found")
    
    # Get all labs in the panel
    labs = (
        db.query(LabModel)
        .options(
            joinedload(LabModel.unit),
            joinedload(LabModel.panel)
        )
        .filter(LabModel.panel_id == panel_id)
        .all()
    )
    
    panel_data = []
    for lab in labs:
        # Get results for this lab
        query = (
            db.query(LabResultModel)
            .filter(LabResultModel.lab_id == lab.id)
        )
        
        # Filter by patient if specified
        if patient_id:
            query = query.filter(LabResultModel.patient_id == patient_id)
        
        results = (
            query.order_by(LabResultModel.date_collected.desc())
            .limit(50)
            .all()
        )
        
        # Format results for chart
        formatted_results = []
        for result in results:
            formatted_results.append({
                'date': result.date_collected.strftime('%Y-%m-%d'),
                'value': result.result
            })
        
        panel_data.append({
            'id': lab.id,
            'name': lab.name,
            'unit': {'id': lab.unit.id, 'name': lab.unit.name} if lab.unit else None,
            'ref_low': lab.ref_low,
            'ref_high': lab.ref_high,
            'results': formatted_results
        })
    
    return {
        'panel': {
            'id': panel.id,
            'name': panel.name
        },
        'labs': panel_data
    }

@router.get("/charts/lab/{lab_id}")
def get_individual_chart_data(lab_id: int, request: Request, db: Session = Depends(get_db)):
    """Get detailed chart data for an individual lab."""
    # Get selected patient from cookie
    try:
        patient_id = int(request.cookies.get("selectedPatientId", "1"))
    except (ValueError, TypeError):
        patient_id = 1
    # Get the lab with relationships
    lab = (
        db.query(LabModel)
        .options(
            joinedload(LabModel.unit),
            joinedload(LabModel.panel)
        )
        .filter(LabModel.id == lab_id)
        .first()
    )
    
    if not lab:
        raise HTTPException(status_code=404, detail="Lab not found")
    
    # Get all results for this lab
    query = (
        db.query(LabResultModel)
        .filter(LabResultModel.lab_id == lab_id)
    )
    
    # Filter by patient if specified
    if patient_id:
        query = query.filter(LabResultModel.patient_id == patient_id)
    
    results = (
        query.order_by(LabResultModel.date_collected.desc())
        .limit(100)
        .all()
    )
    
    # Format results for chart
    formatted_results = []
    for result in results:
        formatted_results.append({
            'date': result.date_collected.strftime('%Y-%m-%d'),
            'value': result.result
        })
    
    return {
        'id': lab.id,
        'name': lab.name,
        'unit': {'id': lab.unit.id, 'name': lab.unit.name} if lab.unit else None,
        'panel': {'id': lab.panel.id, 'name': lab.panel.name} if lab.panel else None,
        'ref_low': lab.ref_low,
        'ref_high': lab.ref_high,
        'results': formatted_results
    }
