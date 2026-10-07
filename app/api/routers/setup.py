"""First-run welcome: ask for the patient's name instead of leaving "Default Patient"."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import Patient as PatientModel, UserSettings as UserSettingsModel

router = APIRouter()

DEFAULT_PATIENT_NAME = "Default Patient"
# Paths that never redirect to the welcome screen
_EXEMPT_PREFIXES = ("/api", "/static", "/health", "/version", "/welcome", "/docs", "/redoc", "/openapi.json", "/favicon")

# Once setup is known to be done it stays done for this process (reset_setup_state undoes it)
_setup_done = False


def reset_setup_state() -> None:
    """Forget the cached answer, e.g. after all data was reset."""
    global _setup_done
    _setup_done = False


def needs_setup(db: Session) -> bool:
    """True while the only patient is still the unnamed default one and setup wasn't skipped."""
    global _setup_done
    if _setup_done:
        return False
    if UserSettingsModel.get_settings(db).get_option("setup_complete", False):
        _setup_done = True
        return False
    patients = db.query(PatientModel).limit(2).all()
    if len(patients) == 1 and patients[0].name == DEFAULT_PATIENT_NAME:
        return True
    # Installs that already have real patient names never see the welcome screen
    _setup_done = True
    return False


async def welcome_redirect_middleware(request: Request, call_next):
    """Send page requests to /welcome until the patient has been named."""
    path = request.url.path
    if request.method == "GET" and not _setup_done and not path.startswith(_EXEMPT_PREFIXES):
        db = SessionLocal()
        try:
            redirect = needs_setup(db)
        finally:
            db.close()
        if redirect:
            return RedirectResponse(url="/welcome", status_code=303)
    return await call_next(request)


class SetupRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    date_of_birth: Optional[date] = None
    skip: bool = False

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None


@router.post("/")
def complete_setup(body: SetupRequest, db: Session = Depends(get_db)):
    """Name the default patient (unless skipped) and mark first-run setup as done."""
    global _setup_done
    patient = (
        db.query(PatientModel).filter(PatientModel.name == DEFAULT_PATIENT_NAME).order_by(PatientModel.id).first()
        or db.query(PatientModel).order_by(PatientModel.id).first()
    )
    if not body.skip:
        if not body.name:
            return JSONResponse(status_code=422, content={"detail": "Enter your name."})
        if body.date_of_birth and body.date_of_birth > date.today():
            return JSONResponse(status_code=422, content={"detail": "Date of birth can't be in the future."})
        if patient is None:
            patient = PatientModel(name=body.name, date_of_birth=body.date_of_birth)
            db.add(patient)
        else:
            patient.name = body.name
            patient.date_of_birth = body.date_of_birth
        db.commit()
        db.refresh(patient)

    UserSettingsModel.update_settings(db, setup_complete=True)
    _setup_done = True

    response = JSONResponse({"success": True, "patient": patient.to_dict() if patient else None})
    if patient:
        response.set_cookie("selectedPatientId", str(patient.id), path="/", samesite="lax", max_age=60 * 60 * 24 * 365)
    return response
