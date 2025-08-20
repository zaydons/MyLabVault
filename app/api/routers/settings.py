"""Settings management routes."""

import json
import shutil
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import (
    LabResult as LabResultModel, Lab as LabModel,
    Patient as PatientModel, Provider as ProviderModel,
    Panel as PanelModel, PDFImportLog, Unit as UnitModel,
    UserSettings as UserSettingsModel
)
from ..schemas import APIResponse, UserSettings, UserSettingsUpdate

router = APIRouter()

# Export-related models
class DateRange(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None

class ExportConfiguration(BaseModel):
    patients: Union[List[str], List[int]]
    include_pdfs: bool = False
    date_range: Optional[DateRange] = None
    format: str = "json"

class ExportPreviewResponse(BaseModel):
    patients_count: int
    lab_results_count: int
    labs_count: int
    providers_count: int
    panels_count: int
    units_count: int
    pdf_files_count: int
    estimated_size: str
    include_pdfs: bool

# Import-related models
class ImportPreviewResponse(BaseModel):
    file_type: str
    export_version: Optional[str] = None
    patients_count: int
    lab_results_count: int
    labs_count: int
    providers_count: int
    panels_count: int
    units_count: int
    pdf_files_count: int
    file_size: str
    conflicts: List[str] = []

class ImportResponse(BaseModel):
    success: bool
    imported_records: int
    imported_pdfs: int
    conflicts_resolved: int
    warnings: List[str] = []

# Database-backed settings (replaced in-memory storage)

@router.get("/user")
def get_user_settings(db: Session = Depends(get_db)):
    """Get user settings from database."""
    try:
        settings = UserSettingsModel.get_settings(db)
        return UserSettings.model_validate(settings.to_dict())
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to get user settings: {str(e)}'
        ) from e

@router.put("/user")
def update_user_settings(
    settings_update: UserSettingsUpdate,
    db: Session = Depends(get_db)
):
    """Update user settings in database."""
    try:
        update_data = {k: v for k, v in settings_update.model_dump().items() if v is not None}
        settings = UserSettingsModel.update_settings(db, **update_data)
        return UserSettings.model_validate(settings.to_dict())
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to update user settings: {str(e)}'
        ) from e

@router.get("/")
def get_settings(db: Session = Depends(get_db)):
    """Get current application settings and preferences (legacy endpoint)."""
    try:
        user_settings = UserSettingsModel.get_settings(db)
        return {
            "dark_mode": user_settings.get_option('dark_mode', False)
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to get settings: {str(e)}'
        ) from e

@router.post("/dark-mode")
def update_dark_mode(
    enabled: bool,
    db: Session = Depends(get_db)
):
    """Update dark mode setting via POST request (legacy endpoint)."""
    try:
        UserSettingsModel.update_settings(db, dark_mode=enabled)
        return APIResponse(
            success=True,
            message=f"Dark mode {'enabled' if enabled else 'disabled'}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to update dark mode: {str(e)}'
        ) from e

class DarkModeUpdate(BaseModel):
    value: str

@router.put("/dark-mode")
def update_dark_mode_put(
    update_data: DarkModeUpdate,
    db: Session = Depends(get_db)
):
    """Update dark mode setting (PUT method, legacy endpoint)."""
    try:
        enabled = update_data.value.lower() == 'true'
        UserSettingsModel.update_settings(db, dark_mode=enabled)
        return APIResponse(
            success=True,
            message=f"Dark mode {'enabled' if enabled else 'disabled'}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to update dark mode: {str(e)}'
        ) from e

@router.post("/reset-data")
def reset_data(db: Session = Depends(get_db)):
    """Reset all application data to initial state (DESTRUCTIVE OPERATION)."""
    try:
        # Delete all data in correct dependency order to avoid foreign key constraint errors
        # 1. First delete LabResults (depends on Lab, Patient, Provider)
        db.query(LabResultModel).delete()
        db.commit()  # Commit this deletion first

        # 2. Then delete Labs (depends on Panel, Unit)  
        db.query(LabModel).delete()
        db.commit()  # Commit this deletion

        # 3. Finally delete the remaining entities (no interdependencies)
        db.query(PatientModel).delete()
        db.query(ProviderModel).delete()
        db.query(PanelModel).delete()
        db.query(UnitModel).delete()
        db.query(PDFImportLog).delete()
        db.query(UserSettingsModel).delete()  # Also reset user settings
        db.commit()

        # Clean up uploaded PDF files
        pdf_dir = Path("/app/data/uploads/pdfs")
        if pdf_dir.exists():
            for pdf_file in pdf_dir.glob("*.pdf"):
                try:
                    pdf_file.unlink()
                except Exception:
                    continue  # Continue if file can't be deleted

        # Recreate default patient with ID 1
        default_patient = PatientModel(id=1, name="Default Patient")
        db.add(default_patient)
        
        # Create default settings with JSON string for options
        import json
        default_settings = UserSettingsModel(
            id=1,  # Ensure ID matches default patient
            options=json.dumps({
                "dark_mode": False,
                "sidebar_open": True
            })
        )
        db.add(default_settings)

        db.commit()

        # Clear all cache after data reset
        from ..utils.cache import api_cache
        api_cache.clear()

        return APIResponse(
            success=True,
            message="All data has been reset successfully"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f'Failed to reset data: {str(e)}'
        ) from e

@router.get("/data-counts")
def get_data_counts(db: Session = Depends(get_db)):
    """Get comprehensive counts of all data types in the system."""
    try:
        lab_results_count = db.query(LabResultModel).count()
        labs_count = db.query(LabModel).count()
        panels_count = db.query(PanelModel).count()
        patients_count = db.query(PatientModel).count()
        providers_count = db.query(ProviderModel).count()
        units_count = db.query(UnitModel).count()
        pdf_imports_count = db.query(PDFImportLog).count()

        # Count PDF files in the uploads directory
        pdf_dir = Path("/app/data/uploads/pdfs")
        pdf_files_count = 0
        if pdf_dir.exists():
            pdf_files_count = len(list(pdf_dir.glob("*.pdf")))

        total_items = (
            lab_results_count + labs_count + panels_count + patients_count + providers_count + units_count + pdf_imports_count + pdf_files_count
        )

        return {
            "success": True,
            "data": {
                "lab_results": lab_results_count,
                "labs": labs_count,
                "panels": panels_count,
                "providers": providers_count,
                "patients": patients_count,
                "units": units_count,
                "pdf_imports": pdf_imports_count,
                "pdf_files": pdf_files_count
            },
            "total_items": total_items
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to get data counts: {str(e)}'
        ) from e

@router.post("/export-preview")
def get_export_preview(
    config: ExportConfiguration,
    db: Session = Depends(get_db)
):
    """Generate a preview of what will be exported."""
    try:
        # Determine patient IDs
        patient_ids = _get_patient_ids(config.patients, db)
        
        # Build base queries
        lab_results_query = db.query(LabResultModel)
        
        # Apply patient filter
        if patient_ids and 'all' not in config.patients:
            lab_results_query = lab_results_query.filter(LabResultModel.patient_id.in_(patient_ids))
        
        # Apply date range filter
        if config.date_range:
            if config.date_range.start:
                lab_results_query = lab_results_query.filter(
                    LabResultModel.date_collected >= config.date_range.start
                )
            if config.date_range.end:
                lab_results_query = lab_results_query.filter(
                    LabResultModel.date_collected <= config.date_range.end
                )
        
        # Get counts
        lab_results_count = lab_results_query.count()
        
        # Get related data counts
        patients_count = len(patient_ids) if patient_ids and 'all' not in config.patients else db.query(PatientModel).count()
        labs_count = db.query(LabModel).count()
        providers_count = db.query(ProviderModel).count()
        panels_count = db.query(PanelModel).count()
        units_count = db.query(UnitModel).count()
        
        # Count PDF files
        pdf_files_count = 0
        if config.include_pdfs:
            pdf_dir = Path("/app/data/uploads/pdfs")
            if pdf_dir.exists():
                pdf_files_count = len(list(pdf_dir.glob("*.pdf")))
        
        # Estimate size
        estimated_size = _estimate_export_size(
            lab_results_count, patients_count, labs_count, 
            providers_count, panels_count, units_count, 
            pdf_files_count, config.include_pdfs
        )
        
        return ExportPreviewResponse(
            patients_count=patients_count,
            lab_results_count=lab_results_count,
            labs_count=labs_count,
            providers_count=providers_count,
            panels_count=panels_count,
            units_count=units_count,
            pdf_files_count=pdf_files_count,
            estimated_size=estimated_size,
            include_pdfs=config.include_pdfs
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to generate export preview: {str(e)}'
        ) from e

@router.post("/export")
def export_data(
    config: ExportConfiguration,
    db: Session = Depends(get_db)
):
    """Export data based on configuration."""
    try:
        # Generate export data
        export_data = _generate_export_data(config, db)
        
        # Create filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        patient_suffix = "all" if 'all' in config.patients else f"{len(config.patients)}-patients"
        
        if config.include_pdfs:
            # Create ZIP file
            zip_buffer = BytesIO()
            filename = f"mylabvault_export_{timestamp}_{patient_suffix}.zip"
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Add JSON data
                zip_file.writestr("data.json", json.dumps(export_data, indent=2, default=str))
                
                # Add PDF files
                pdf_dir = Path("/app/data/uploads/pdfs")
                if pdf_dir.exists():
                    for pdf_file in pdf_dir.glob("*.pdf"):
                        try:
                            zip_file.write(pdf_file, f"pdfs/{pdf_file.name}")
                        except Exception:
                            continue  # Skip files that can't be read
            
            zip_buffer.seek(0)
            
            return StreamingResponse(
                BytesIO(zip_buffer.read()),
                media_type="application/zip",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        else:
            # Return JSON file
            filename = f"mylabvault_export_{timestamp}_{patient_suffix}.json"
            json_data = json.dumps(export_data, indent=2, default=str)
            
            return Response(
                content=json_data,
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
            
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to export data: {str(e)}'
        ) from e

@router.post("/import-preview")
def preview_import_file(
    import_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Preview what will be imported from a file."""
    try:
        # Read file content
        file_content = import_file.file.read()
        file_size = len(file_content)
        import_file.file.seek(0)  # Reset file pointer
        
        # Determine file type
        file_type = "unknown"
        export_data = None
        pdf_files_count = 0
        
        if import_file.filename.lower().endswith('.json'):
            file_type = "JSON"
            try:
                export_data = json.loads(file_content.decode('utf-8'))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON format")
                
        elif import_file.filename.lower().endswith('.zip'):
            file_type = "ZIP"
            try:
                with zipfile.ZipFile(BytesIO(file_content), 'r') as zip_file:
                    # Look for data.json in the ZIP
                    if 'data.json' in zip_file.namelist():
                        json_content = zip_file.read('data.json')
                        export_data = json.loads(json_content.decode('utf-8'))
                    else:
                        raise HTTPException(status_code=400, detail="ZIP file does not contain data.json")
                    
                    # Count PDF files
                    pdf_files = [f for f in zip_file.namelist() if f.startswith('pdfs/') and f.endswith('.pdf')]
                    pdf_files_count = len(pdf_files)
                    
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="Invalid ZIP file format")
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type. Please upload a JSON or ZIP file.")
        
        if not export_data:
            raise HTTPException(status_code=400, detail="No valid export data found in file")
        
        # Validate export data structure
        if 'export_info' not in export_data:
            raise HTTPException(status_code=400, detail="Invalid export format: missing export_info")
        
        # Extract counts from export data
        export_info = export_data.get('export_info', {})
        patients_count = len(export_data.get('patients', []))
        lab_results_count = len(export_data.get('lab_results', []))
        labs_count = len(export_data.get('labs', []))
        providers_count = len(export_data.get('providers', []))
        panels_count = len(export_data.get('panels', []))
        units_count = len(export_data.get('units', []))
        
        # Check for potential conflicts
        conflicts = _check_import_conflicts(export_data, db)
        
        # Format file size
        if file_size < 1024:
            file_size_str = f"{file_size} bytes"
        elif file_size < 1024 * 1024:
            file_size_str = f"{file_size / 1024:.1f} KB"
        else:
            file_size_str = f"{file_size / (1024 * 1024):.1f} MB"
        
        return ImportPreviewResponse(
            file_type=file_type,
            export_version=export_info.get('version'),
            patients_count=patients_count,
            lab_results_count=lab_results_count,
            labs_count=labs_count,
            providers_count=providers_count,
            panels_count=panels_count,
            units_count=units_count,
            pdf_files_count=pdf_files_count,
            file_size=file_size_str,
            conflicts=conflicts
        )
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=500,
            detail=f'Failed to preview import file: {str(e)}'
        ) from e

@router.post("/import")
def import_data(
    import_file: UploadFile = File(...),
    merge_data: bool = Form(True),
    validate_data: bool = Form(True),
    start_from_scratch: bool = Form(False),
    db: Session = Depends(get_db)
):
    """Import data from exported file."""
    try:
        # Read and validate file
        file_content = import_file.file.read()
        export_data = None
        pdf_files = {}
        
        if import_file.filename.lower().endswith('.json'):
            try:
                export_data = json.loads(file_content.decode('utf-8'))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON format")
                
        elif import_file.filename.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(BytesIO(file_content), 'r') as zip_file:
                    # Extract data.json
                    if 'data.json' in zip_file.namelist():
                        json_content = zip_file.read('data.json')
                        export_data = json.loads(json_content.decode('utf-8'))
                    else:
                        raise HTTPException(status_code=400, detail="ZIP file does not contain data.json")
                    
                    # Extract PDF files
                    for file_path in zip_file.namelist():
                        if file_path.startswith('pdfs/') and file_path.endswith('.pdf'):
                            pdf_content = zip_file.read(file_path)
                            pdf_filename = Path(file_path).name
                            pdf_files[pdf_filename] = pdf_content
                            
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="Invalid ZIP file format")
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
        
        if not export_data:
            raise HTTPException(status_code=400, detail="No valid export data found")
        
        # Validate data if requested
        if validate_data:
            _validate_import_data(export_data)
        
        # Perform the import
        import_result = _perform_data_import(export_data, pdf_files, merge_data, start_from_scratch, db)
        
        return ImportResponse(
            success=True,
            imported_records=import_result['imported_records'],
            imported_pdfs=import_result['imported_pdfs'],
            conflicts_resolved=import_result['conflicts_resolved'],
            warnings=import_result['warnings']
        )
        
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=500,
            detail=f'Failed to import data: {str(e)}'
        ) from e

def _get_patient_ids(patients: Union[List[str], List[int]], db: Session) -> List[int]:
    """Convert patient selection to list of patient IDs."""
    if 'all' in patients:
        # Get all patient IDs
        return [p.id for p in db.query(PatientModel.id).all()]
    else:
        # Convert to integers if needed
        return [int(p) if isinstance(p, str) else p for p in patients]

def _generate_export_data(config: ExportConfiguration, db: Session) -> dict:
    """Generate the complete export data structure."""
    patient_ids = _get_patient_ids(config.patients, db)
    
    # Build lab results query with relationships
    lab_results_query = db.query(LabResultModel).options(
        joinedload(LabResultModel.lab).joinedload(LabModel.unit),
        joinedload(LabResultModel.lab).joinedload(LabModel.panel),
        joinedload(LabResultModel.provider),
        joinedload(LabResultModel.patient)
    )
    
    # Apply filters
    if patient_ids and 'all' not in config.patients:
        lab_results_query = lab_results_query.filter(LabResultModel.patient_id.in_(patient_ids))
    
    if config.date_range:
        if config.date_range.start:
            lab_results_query = lab_results_query.filter(
                LabResultModel.date_collected >= config.date_range.start
            )
        if config.date_range.end:
            lab_results_query = lab_results_query.filter(
                LabResultModel.date_collected <= config.date_range.end
            )
    
    # Get data
    lab_results = lab_results_query.all()
    
    # Get related data
    if patient_ids and 'all' not in config.patients:
        patients = db.query(PatientModel).filter(PatientModel.id.in_(patient_ids)).all()
    else:
        patients = db.query(PatientModel).all()
    
    providers = db.query(ProviderModel).all()
    panels = db.query(PanelModel).all()
    units = db.query(UnitModel).all()
    labs = db.query(LabModel).options(
        joinedload(LabModel.unit),
        joinedload(LabModel.panel)
    ).all()
    
    # Convert to dictionaries
    export_data = {
        "export_info": {
            "timestamp": datetime.now().isoformat(),
            "version": "1.0",
            "patient_selection": "all" if 'all' in config.patients else "selected",
            "selected_patients": patient_ids if 'all' not in config.patients else None,
            "date_range": config.date_range.model_dump() if config.date_range else None,
            "include_pdfs": config.include_pdfs,
            "total_records": len(lab_results)
        },
        "patients": [_patient_to_dict(p) for p in patients],
        "providers": [_provider_to_dict(p) for p in providers],
        "panels": [_panel_to_dict(p) for p in panels],
        "units": [_unit_to_dict(u) for u in units],
        "labs": [_lab_to_dict(l) for l in labs],
        "lab_results": [_lab_result_to_dict(lr) for lr in lab_results]
    }
    
    return export_data

def _estimate_export_size(
    lab_results_count: int, patients_count: int, labs_count: int,
    providers_count: int, panels_count: int, units_count: int,
    pdf_files_count: int, include_pdfs: bool
) -> str:
    """Estimate the size of the export."""
    # Rough estimates (in bytes)
    json_size = (
        lab_results_count * 500 +  # ~500 bytes per lab result
        patients_count * 100 +     # ~100 bytes per patient
        labs_count * 200 +         # ~200 bytes per lab
        providers_count * 100 +    # ~100 bytes per provider
        panels_count * 50 +        # ~50 bytes per panel
        units_count * 30           # ~30 bytes per unit
    )
    
    # Add PDF size estimate if included
    total_size = json_size
    if include_pdfs:
        pdf_size = pdf_files_count * 2 * 1024 * 1024  # Assume ~2MB per PDF
        total_size += pdf_size
    
    # Convert to human readable
    if total_size < 1024:
        return f"{total_size} bytes"
    elif total_size < 1024 * 1024:
        return f"{total_size / 1024:.1f} KB"
    elif total_size < 1024 * 1024 * 1024:
        return f"{total_size / (1024 * 1024):.1f} MB"
    else:
        return f"{total_size / (1024 * 1024 * 1024):.1f} GB"

def _patient_to_dict(patient: PatientModel) -> dict:
    """Convert patient model to dictionary."""
    return {
        "id": patient.id,
        "name": patient.name,
        "date_of_birth": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
        "gender": patient.gender
    }

def _provider_to_dict(provider: ProviderModel) -> dict:
    """Convert provider model to dictionary."""
    return {
        "id": provider.id,
        "name": provider.name,
        "specialty": provider.specialty,
        "created_at": getattr(provider, 'created_at', None).isoformat() if hasattr(provider, 'created_at') and getattr(provider, 'created_at') else None
    }

def _panel_to_dict(panel: PanelModel) -> dict:
    """Convert panel model to dictionary."""
    return {
        "id": panel.id,
        "name": panel.name,
        "created_at": getattr(panel, 'created_at', None).isoformat() if hasattr(panel, 'created_at') and getattr(panel, 'created_at') else None
    }

def _unit_to_dict(unit: UnitModel) -> dict:
    """Convert unit model to dictionary."""
    return {
        "id": unit.id,
        "name": unit.name,
        "created_at": getattr(unit, 'created_at', None).isoformat() if hasattr(unit, 'created_at') and getattr(unit, 'created_at') else None
    }

def _lab_to_dict(lab: LabModel) -> dict:
    """Convert lab model to dictionary."""
    return {
        "id": lab.id,
        "name": lab.name,
        "panel_id": lab.panel_id,
        "panel_name": lab.panel.name if lab.panel else None,
        "unit_id": lab.unit_id,
        "unit_name": lab.unit.name if lab.unit else None,
        "ref_low": lab.ref_low,
        "ref_high": lab.ref_high,
        "ref_value": lab.ref_value,
        "ref_type": lab.ref_type,
        "created_at": getattr(lab, 'created_at', None).isoformat() if hasattr(lab, 'created_at') and getattr(lab, 'created_at') else None
    }

def _lab_result_to_dict(lab_result: LabResultModel) -> dict:
    """Convert lab result model to dictionary."""
    return {
        "id": lab_result.id,
        "patient_id": lab_result.patient_id,
        "patient_name": lab_result.patient.name if lab_result.patient else None,
        "lab_id": lab_result.lab_id,
        "lab_name": lab_result.lab.name if lab_result.lab else None,
        "provider_id": lab_result.provider_id,
        "provider_name": lab_result.provider.name if lab_result.provider else None,
        "result": lab_result.result,
        "result_text": lab_result.result_text,
        "date_collected": lab_result.date_collected.isoformat() if lab_result.date_collected else None,
        "notes": lab_result.notes,
        "created_at": getattr(lab_result, 'created_at', None).isoformat() if hasattr(lab_result, 'created_at') and getattr(lab_result, 'created_at') else None,
        "lab_details": {
            "unit_name": lab_result.lab.unit.name if lab_result.lab and lab_result.lab.unit else None,
            "panel_name": lab_result.lab.panel.name if lab_result.lab and lab_result.lab.panel else None,
            "reference_range": {
                "low": lab_result.lab.ref_low if lab_result.lab else None,
                "high": lab_result.lab.ref_high if lab_result.lab else None,
                "value": lab_result.lab.ref_value if lab_result.lab else None,
                "type": lab_result.lab.ref_type if lab_result.lab else None
            }
        }
    }

def _check_import_conflicts(export_data: dict, db: Session) -> List[str]:
    """Check for potential conflicts when importing data."""
    conflicts = []
    
    # Check for existing patients with same names
    existing_patients = {p.name for p in db.query(PatientModel).all()}
    import_patients = {p['name'] for p in export_data.get('patients', [])}
    patient_conflicts = existing_patients.intersection(import_patients)
    if patient_conflicts:
        conflicts.extend([f"Patient already exists: {name}" for name in patient_conflicts])
    
    # Check for existing providers with same names
    existing_providers = {p.name for p in db.query(ProviderModel).all()}
    import_providers = {p['name'] for p in export_data.get('providers', [])}
    provider_conflicts = existing_providers.intersection(import_providers)
    if provider_conflicts:
        conflicts.extend([f"Provider already exists: {name}" for name in provider_conflicts])
    
    # Check for existing panels/units/labs
    existing_panels = {p.name for p in db.query(PanelModel).all()}
    import_panels = {p['name'] for p in export_data.get('panels', [])}
    panel_conflicts = existing_panels.intersection(import_panels)
    if panel_conflicts:
        conflicts.extend([f"Panel already exists: {name}" for name in panel_conflicts])
    
    return conflicts

def _validate_import_data(export_data: dict):
    """Validate the structure and content of import data."""
    required_sections = ['export_info', 'patients', 'providers', 'panels', 'units', 'labs', 'lab_results']
    
    for section in required_sections:
        if section not in export_data:
            raise HTTPException(status_code=400, detail=f"Missing required section: {section}")
    
    # Validate export info
    export_info = export_data['export_info']
    if 'version' not in export_info:
        raise HTTPException(status_code=400, detail="Missing export version information")
    
    # Basic data structure validation
    if not isinstance(export_data['patients'], list):
        raise HTTPException(status_code=400, detail="Invalid patients data format")
    
    if not isinstance(export_data['lab_results'], list):
        raise HTTPException(status_code=400, detail="Invalid lab results data format")

def _perform_data_import(export_data: dict, pdf_files: dict, merge_data: bool, start_from_scratch: bool, db: Session) -> dict:
    """Perform the actual data import operation."""
    imported_records = 0
    imported_pdfs = 0
    conflicts_resolved = 0
    warnings = []
    
    try:
        # If not merging, clear existing data
        if not merge_data or start_from_scratch:
            # Delete all lab results first (foreign key constraints)
            db.query(LabResultModel).delete()
            db.query(LabModel).delete()
            db.query(PanelModel).delete()
            db.query(UnitModel).delete()
            db.query(ProviderModel).delete()
            
            # For start from scratch, delete ALL patients
            # For regular replace mode, keep default patient
            if start_from_scratch:
                db.query(PatientModel).delete()
                # Also clear PDF files directory
                pdf_dir = Path("/app/data/uploads/pdfs")
                if pdf_dir.exists():
                    shutil.rmtree(pdf_dir)
                    pdf_dir.mkdir(parents=True, exist_ok=True)
            else:
                db.query(PatientModel).filter(PatientModel.id != 1).delete()
            
            db.commit()
        
        # Create ID mapping for imported entities
        patient_id_map = {}
        provider_id_map = {}
        panel_id_map = {}
        unit_id_map = {}
        lab_id_map = {}
        
        # Import patients
        for patient_data in export_data.get('patients', []):
            existing_patient = db.query(PatientModel).filter(PatientModel.name == patient_data['name']).first()
            if existing_patient and merge_data:
                patient_id_map[patient_data['id']] = existing_patient.id
                conflicts_resolved += 1
            else:
                new_patient = PatientModel(
                    name=patient_data['name'],
                    date_of_birth=datetime.fromisoformat(patient_data['date_of_birth']).date() if patient_data.get('date_of_birth') else None,
                    gender=patient_data.get('gender')
                )
                db.add(new_patient)
                db.flush()
                patient_id_map[patient_data['id']] = new_patient.id
                imported_records += 1
        
        # Import providers
        for provider_data in export_data.get('providers', []):
            existing_provider = db.query(ProviderModel).filter(ProviderModel.name == provider_data['name']).first()
            if existing_provider and merge_data:
                provider_id_map[provider_data['id']] = existing_provider.id
                conflicts_resolved += 1
            else:
                new_provider = ProviderModel(
                    name=provider_data['name'],
                    specialty=provider_data.get('specialty')
                )
                db.add(new_provider)
                db.flush()
                provider_id_map[provider_data['id']] = new_provider.id
                imported_records += 1
        
        # Import panels
        for panel_data in export_data.get('panels', []):
            existing_panel = db.query(PanelModel).filter(PanelModel.name == panel_data['name']).first()
            if existing_panel and merge_data:
                panel_id_map[panel_data['id']] = existing_panel.id
                conflicts_resolved += 1
            else:
                new_panel = PanelModel(name=panel_data['name'])
                db.add(new_panel)
                db.flush()
                panel_id_map[panel_data['id']] = new_panel.id
                imported_records += 1
        
        # Import units
        for unit_data in export_data.get('units', []):
            existing_unit = db.query(UnitModel).filter(UnitModel.name == unit_data['name']).first()
            if existing_unit and merge_data:
                unit_id_map[unit_data['id']] = existing_unit.id
                conflicts_resolved += 1
            else:
                new_unit = UnitModel(name=unit_data['name'])
                db.add(new_unit)
                db.flush()
                unit_id_map[unit_data['id']] = new_unit.id
                imported_records += 1
        
        # Import labs
        for lab_data in export_data.get('labs', []):
            existing_lab = db.query(LabModel).filter(LabModel.name == lab_data['name']).first()
            if existing_lab and merge_data:
                lab_id_map[lab_data['id']] = existing_lab.id
                conflicts_resolved += 1
            else:
                new_lab = LabModel(
                    name=lab_data['name'],
                    panel_id=panel_id_map.get(lab_data.get('panel_id')),
                    unit_id=unit_id_map.get(lab_data.get('unit_id')),
                    ref_low=lab_data.get('ref_low'),
                    ref_high=lab_data.get('ref_high'),
                    ref_value=lab_data.get('ref_value'),
                    ref_type=lab_data.get('ref_type')
                )
                db.add(new_lab)
                db.flush()
                lab_id_map[lab_data['id']] = new_lab.id
                imported_records += 1
        
        # Import lab results
        for result_data in export_data.get('lab_results', []):
            # Skip if we can't map the required IDs
            if (result_data.get('patient_id') not in patient_id_map or 
                result_data.get('lab_id') not in lab_id_map):
                warnings.append(f"Skipped lab result due to missing patient or lab mapping")
                continue
                
            new_result = LabResultModel(
                patient_id=patient_id_map[result_data['patient_id']],
                lab_id=lab_id_map[result_data['lab_id']],
                provider_id=provider_id_map.get(result_data.get('provider_id')),
                result=result_data.get('result'),
                result_text=result_data.get('result_text'),
                date_collected=datetime.fromisoformat(result_data['date_collected']).date() if result_data.get('date_collected') else None,
                notes=result_data.get('notes')
            )
            db.add(new_result)
            imported_records += 1
        
        # Import PDF files
        if pdf_files:
            pdf_dir = Path("/app/data/uploads/pdfs")
            pdf_dir.mkdir(parents=True, exist_ok=True)
            
            for filename, content in pdf_files.items():
                pdf_path = pdf_dir / filename
                # Only write if file doesn't exist or we're not merging
                if not pdf_path.exists() or not merge_data:
                    with open(pdf_path, 'wb') as f:
                        f.write(content)
                    imported_pdfs += 1
                else:
                    conflicts_resolved += 1
        
        # Commit all changes
        db.commit()
        
        # Clear cache after import
        from ..utils.cache import api_cache
        api_cache.clear()
        
        return {
            'imported_records': imported_records,
            'imported_pdfs': imported_pdfs,
            'conflicts_resolved': conflicts_resolved,
            'warnings': warnings
        }
        
    except Exception as e:
        db.rollback()
        raise e
