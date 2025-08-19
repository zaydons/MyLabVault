"""Template-based page routes"""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import (
    LabResult as LabResultModel, 
    Lab as LabModel,
    Patient as PatientModel, 
    Provider as ProviderModel,
    Panel as PanelModel, 
    Unit as UnitModel,
    UserSettings as UserSettingsModel,
    PDFImportLog as PDFImportLogModel
)
from sqlalchemy import func

router = APIRouter()
from pathlib import Path

# Get the correct path to templates directory
template_dir = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(template_dir))
# Disable template caching for development
templates.env.auto_reload = True
templates.env.cache_size = 0

def number_format(value):
    """Format numbers with commas."""
    if value is None:
        return "0"
    return f"{value:,}"

def get_result_status(result_value, ref_low, ref_high):
    """Determine the status of a lab result based on reference ranges."""
    if ref_low is None or ref_high is None:
        return 'normal'

    try:
        numeric_value = float(result_value)
        if numeric_value < ref_low:
            return 'low'
        elif numeric_value > ref_high:
            return 'high'
        else:
            return 'normal'
    except (ValueError, TypeError):
        return 'normal'  # Non-numeric results default to normal

def is_numeric(value):
    """Check if a value is numeric."""
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False

def get_pending_imports_count(db: Session):
    """Get count of pending PDF imports."""
    try:
        return db.query(PDFImportLogModel).filter(PDFImportLogModel.status == "pending").count()
    except Exception:
        return 0

def get_selected_patient_id(request: Request) -> int:
    """Get the selected patient ID from cookie, defaulting to 1."""
    try:
        patient_id = request.cookies.get("selectedPatientId", "1")
        return int(patient_id)
    except (ValueError, TypeError):
        return 1

def _render_simple_page(template_name: str, request: Request, db: Session):
    """Render a simple page with standard context."""
    user_settings = UserSettingsModel.get_settings(db)
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "user_settings": user_settings.to_dict(),
            "pending_imports_count": get_pending_imports_count(db)
        }
    )

# Add the filters to Jinja2
templates.env.filters['number_format'] = number_format
templates.env.filters['get_result_status'] = get_result_status
templates.env.filters['is_numeric'] = is_numeric

@router.get("/")
def index_page(request: Request, db: Session = Depends(get_db)):
    """Index page - always redirects to dashboard."""
    from fastapi.responses import RedirectResponse
    # Always redirect to dashboard (patient will be handled via cookie)
    return RedirectResponse(url="/dashboard", status_code=302)

@router.get("/dashboard")
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """Dashboard page with server-side rendering."""
    # Get user settings
    user_settings = UserSettingsModel.get_settings(db)

    # Get selected patient from cookie
    patient_id = get_selected_patient_id(request)

    # Build base queries
    results_query = db.query(LabResultModel)
    recent_results_query = (
        db.query(LabResultModel)
        .options(
            joinedload(LabResultModel.lab).joinedload(LabModel.unit),
            joinedload(LabResultModel.provider),
            joinedload(LabResultModel.patient)
        )
    )

    # Filter by patient
    results_query = results_query.filter(LabResultModel.patient_id == patient_id)
    recent_results_query = recent_results_query.filter(LabResultModel.patient_id == patient_id)

    # Get dashboard statistics
    total_results = results_query.count()
    recent_results_count = results_query.count()  # Could add date filtering
    total_labs = db.query(LabModel).count()  # Labs are shared across patients
    total_providers = db.query(ProviderModel).count()  # Providers are shared across patients

    # Get recent results
    recent_results = (
        recent_results_query.order_by(LabResultModel.date_collected.desc())
        .limit(10)
        .all()
    )

    dashboard_data = {
        "stats": {
            "total_results": total_results,
            "recent_results": recent_results_count,
            "total_labs": total_labs,
            "total_providers": total_providers
        },
        "recent_results": recent_results
    }

    return templates.TemplateResponse(
        "dashboard.html", 
        {
            "request": request, 
            "dashboard_data": dashboard_data,
            "user_settings": user_settings.to_dict(),
            "pending_imports_count": get_pending_imports_count(db)
        }
    )

@router.get("/results")
def results_page(request: Request, db: Session = Depends(get_db)):
    """Results page with server-side rendering and DataTables."""
    # Get user settings
    user_settings = UserSettingsModel.get_settings(db)

    # Get selected patient from cookie
    patient_id = get_selected_patient_id(request)

    # Build query for results with relationships
    query = (
        db.query(LabResultModel)
        .options(
            joinedload(LabResultModel.lab).joinedload(LabModel.unit),
            joinedload(LabResultModel.lab).joinedload(LabModel.panel),
            joinedload(LabResultModel.provider),
            joinedload(LabResultModel.patient)
        )
    )

    # Filter by patient
    query = query.filter(LabResultModel.patient_id == patient_id)

    # Get results with ordering and limit
    results = (
        query.order_by(LabResultModel.date_collected.desc())
        .limit(1000)  # Reasonable limit for DataTables performance
        .all()
    )

    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "results": results,
            "user_settings": user_settings.to_dict(),
            "pending_imports_count": get_pending_imports_count(db)
        }
    )

@router.get("/lab/{lab_id}")
def lab_detail_page(request: Request, lab_id: int, db: Session = Depends(get_db)):
    """Individual lab detail page with results history and chart."""
    # Get user settings
    user_settings = UserSettingsModel.get_settings(db)

    # Get selected patient from cookie
    patient_id = get_selected_patient_id(request)

    # Get the lab information
    lab_info = (
        db.query(LabModel)
        .options(
            joinedload(LabModel.unit),
            joinedload(LabModel.panel)
        )
        .filter(LabModel.id == lab_id)
        .first()
    )

    if not lab_info:
        # Lab not found, render with error state
        return templates.TemplateResponse(
            "lab.html",
            {
                "request": request,
                "lab_info": None,
                "lab_results": [],
                "user_settings": user_settings.to_dict(),
                "pending_imports_count": get_pending_imports_count(db)
            }
        )

    # Build query for results for this specific lab
    query = (
        db.query(LabResultModel)
        .options(
            joinedload(LabResultModel.patient),
            joinedload(LabResultModel.provider)
        )
        .filter(LabResultModel.lab_id == lab_id)
    )

    # Filter by patient if specified
    if patient_id:
        query = query.filter(LabResultModel.patient_id == patient_id)

    # Get lab results
    lab_results = query.order_by(LabResultModel.date_collected.desc()).all()

    return templates.TemplateResponse(
        "lab.html",
        {
            "request": request,
            "lab_info": lab_info,
            "lab_results": lab_results,
            "user_settings": user_settings.to_dict(),
            "pending_imports_count": get_pending_imports_count(db)
        }
    )

@router.get("/charts")
def charts_page(request: Request, db: Session = Depends(get_db)):
    """Charts page with panel and individual lab dropdowns."""
    # Get user settings
    user_settings = UserSettingsModel.get_settings(db)

    # Get selected patient from cookie
    patient_id = get_selected_patient_id(request)

    # Get panels with lab counts, optionally filtered by patient
    panels_query = (
        db.query(PanelModel)
        .join(LabModel, PanelModel.id == LabModel.panel_id)
    )

    if patient_id:
        # Only show panels that have labs with results for the specified patient
        panels_query = (
            panels_query.join(LabResultModel, LabModel.id == LabResultModel.lab_id)
            .filter(LabResultModel.patient_id == patient_id)
        )

    panels = (
        panels_query.add_columns(func.count(LabModel.id.distinct()).label('lab_count'))
        .group_by(PanelModel.id)
        .having(func.count(LabModel.id.distinct()) > 0)
        .all()
    )

    # Format panels with lab counts
    panels_data = []
    for panel, lab_count in panels:
        panels_data.append({
            'id': panel.id,
            'name': panel.name,
            'lab_count': lab_count
        })

    # Get grouped labs for individual dropdown
    grouped_labs = []
    for panel, lab_count in panels:
        # Build query for lab results count
        result_count_query = (
            db.query(LabModel)
            .options(joinedload(LabModel.unit))
            .filter(LabModel.panel_id == panel.id)
            .add_columns(
                func.count(LabResultModel.id).label('result_count')
            )
            .outerjoin(LabResultModel, LabModel.id == LabResultModel.lab_id)
        )

        # Filter by patient if specified
        if patient_id:
            result_count_query = result_count_query.filter(LabResultModel.patient_id == patient_id)

        labs = (
            result_count_query.group_by(LabModel.id)
            .order_by(LabModel.name)
            .all()
        )

        lab_list = []
        for lab, result_count in labs:
            lab_list.append({
                'id': lab.id,
                'name': lab.name,
                'result_count': result_count or 0
            })

        if lab_list:  # Only include panels that have labs
            grouped_labs.append({
                'name': panel.name,
                'labs': lab_list
            })

    return templates.TemplateResponse(
        "charts.html",
        {
            "request": request,
            "panels": panels_data,
            "grouped_labs": grouped_labs,
            "user_settings": user_settings.to_dict(),
            "pending_imports_count": get_pending_imports_count(db)
        }
    )

@router.get("/providers")
def providers_page(request: Request, db: Session = Depends(get_db)):
    """Providers management page."""
    return _render_simple_page("providers.html", request, db)

@router.get("/units")
def units_page(request: Request, db: Session = Depends(get_db)):
    """Units management page."""
    return _render_simple_page("units.html", request, db)

@router.get("/panels")
def panels_page(request: Request, db: Session = Depends(get_db)):
    """Panels management page."""
    return _render_simple_page("panels.html", request, db)

@router.get("/labs")
def labs_page(request: Request, db: Session = Depends(get_db)):
    """Lab Tests management page."""
    return _render_simple_page("labs.html", request, db)

@router.get("/import")
def pdf_import_page(request: Request, db: Session = Depends(get_db)):
    """Unified PDF Import page with upload and history management."""
    return _render_simple_page("pdf-import.html", request, db)

@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    """Settings management page."""
    return _render_simple_page("settings.html", request, db)

@router.get("/patients")
def patients_page(request: Request, db: Session = Depends(get_db)):
    """Patient management page."""
    return _render_simple_page("patients.html", request, db)

@router.get("/bulk-import")
def bulk_import_page(request: Request, db: Session = Depends(get_db)):
    """Bulk manual import page for entering multiple lab results."""
    return _render_simple_page("bulk-import.html", request, db)

@router.get("/result/{result_id}")
def result_detail_page(request: Request, result_id: int, db: Session = Depends(get_db)):
    """Individual result detail page for editing."""
    # Get user settings
    user_settings = UserSettingsModel.get_settings(db)

    # Get the specific result with all relationships
    result = (
        db.query(LabResultModel)
        .options(
            joinedload(LabResultModel.lab).joinedload(LabModel.unit),
            joinedload(LabResultModel.lab).joinedload(LabModel.panel),
            joinedload(LabResultModel.provider),
            joinedload(LabResultModel.patient)
        )
        .filter(LabResultModel.id == result_id)
        .first()
    )

    if not result:
        # Result not found, render with error state
        return templates.TemplateResponse(
            "result_detail.html",
            {
                "request": request,
                "result": None,
                "user_settings": user_settings.to_dict(),
                "pending_imports_count": get_pending_imports_count(db)
            }
        )

    return templates.TemplateResponse(
        "result_detail.html",
        {
            "request": request,
            "result": result,
            "user_settings": user_settings.to_dict(),
            "pending_imports_count": get_pending_imports_count(db)
        }
    )
