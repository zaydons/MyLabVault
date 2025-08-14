"""Settings management routes."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    LabResult as LabResultModel, Lab as LabModel,
    Patient as PatientModel, Provider as ProviderModel,
    Panel as PanelModel, PDFImportLog, Unit as UnitModel,
    UserSettings as UserSettingsModel
)
from ..schemas import APIResponse, UserSettings, UserSettingsUpdate

router = APIRouter()

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
