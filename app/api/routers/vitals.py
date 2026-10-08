"""Vitals API router: weight, blood pressure and other patient measurements."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Patient as PatientModel, Vital as VitalModel

router = APIRouter()

# Supported measurement types. Each unit converts to the type's first (base) unit as
# base = value * factor + offset, so charts can combine entries recorded in different units.
# "decimals" is how precisely a value in that unit is shown.
# "normal" is a typical adult reference range in the base unit, shown for orientation only.
VITAL_TYPES = {
    "weight": {
        "label": "Weight",
        "units": {"lb": {"factor": 1, "offset": 0, "decimals": 1}, "kg": {"factor": 2.20462262, "offset": 0, "decimals": 1}},
        "normal": None,
    },
    "height": {
        "label": "Height",
        "units": {"in": {"factor": 1, "offset": 0, "decimals": 1}, "cm": {"factor": 1 / 2.54, "offset": 0, "decimals": 0}},
        "normal": None,
    },
    "blood_pressure": {
        "label": "Blood Pressure",
        "units": {"mmHg": {"factor": 1, "offset": 0, "decimals": 0}},
        "value_label": "Systolic",
        "value2_label": "Diastolic",
        "normal": {"high": 120, "high2": 80},
    },
    "heart_rate": {
        "label": "Heart Rate",
        "units": {"bpm": {"factor": 1, "offset": 0, "decimals": 0}},
        "normal": {"low": 60, "high": 100},
    },
    "temperature": {
        "label": "Temperature",
        "units": {"°F": {"factor": 1, "offset": 0, "decimals": 1}, "°C": {"factor": 9 / 5, "offset": 32, "decimals": 1}},
        "normal": {"low": 97, "high": 99},
    },
    "oxygen_saturation": {
        "label": "Oxygen Saturation",
        "units": {"%": {"factor": 1, "offset": 0, "decimals": 0}},
        "normal": {"low": 95, "high": 100},
    },
    "respiratory_rate": {
        "label": "Respiratory Rate",
        "units": {"breaths/min": {"factor": 1, "offset": 0, "decimals": 0}},
        "normal": {"low": 12, "high": 20},
    },
    "blood_glucose": {
        "label": "Blood Glucose",
        "units": {"mg/dL": {"factor": 1, "offset": 0, "decimals": 0}, "mmol/L": {"factor": 18.0, "offset": 0, "decimals": 1}},
        "normal": {"low": 70, "high": 99},
    },
}


# ---------- preferred units (Settings → General → Units) ----------

def preferred_units(db: Session) -> dict:
    """The unit to show each vital type in: the user's choice, else the type's base unit."""
    from ..models import UserSettings as UserSettingsModel
    chosen = UserSettingsModel.get_settings(db).get_option("vital_units", {}) or {}
    return {
        vital_type: chosen.get(vital_type) if chosen.get(vital_type) in config["units"] else next(iter(config["units"]))
        for vital_type, config in VITAL_TYPES.items()
    }


def check_unit_preferences(prefs: dict) -> dict:
    """Validate {vital_type: unit} from Settings; raises 400 for an unknown type or unit."""
    if not isinstance(prefs, dict):
        raise HTTPException(status_code=400, detail="vital_units must be an object of vital type to unit")
    for vital_type, unit in prefs.items():
        config = VITAL_TYPES.get(vital_type)
        if not config:
            raise HTTPException(status_code=400, detail=f"Unknown vital type '{vital_type}'")
        if unit not in config["units"]:
            raise HTTPException(status_code=400, detail=f"Unit '{unit}' is not valid for {config['label']}")
    return prefs


def convert(vital_type: str, value: Optional[float], from_unit: Optional[str], to_unit: str) -> Optional[float]:
    """A value in another unit of the same vital type (through the base unit)."""
    if value is None:
        return None
    units = VITAL_TYPES[vital_type]["units"]
    src = units.get(from_unit) or next(iter(units.values()))
    dst = units[to_unit]
    base = value * src["factor"] + src["offset"]
    return (base - dst["offset"]) / dst["factor"]


def _number(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_reading(vital_type: str, value: float, value2: Optional[float], unit: Optional[str], to_unit: str) -> str:
    """A reading shown in `to_unit`, rounded for that unit: "154.3 lb", "120/80 mmHg", "5′ 10″"."""
    decimals = VITAL_TYPES[vital_type]["units"][to_unit].get("decimals", 1)
    shown = convert(vital_type, value, unit, to_unit)
    if vital_type == "height" and to_unit == "in":
        feet, inches = divmod(round(shown), 12)
        return f"{feet}′ {inches}″"
    if value2 is not None:
        return f"{_number(shown, decimals)}/{_number(convert(vital_type, value2, unit, to_unit), decimals)} {to_unit}"
    return f"{_number(shown, decimals)} {to_unit}"


def bp_category(systolic: float, diastolic: float) -> dict:
    """American Heart Association blood pressure category for an adult reading.

    Returns {"name", "level"} where level is normal, elevated, stage1, stage2 or crisis.
    """
    if systolic > 180 or diastolic > 120:
        return {"name": "Hypertensive crisis", "level": "crisis"}
    if systolic >= 140 or diastolic >= 90:
        return {"name": "Stage 2 hypertension", "level": "stage2"}
    if systolic >= 130 or diastolic >= 80:
        return {"name": "Stage 1 hypertension", "level": "stage1"}
    if systolic >= 120:
        return {"name": "Elevated", "level": "elevated"}
    return {"name": "Normal", "level": "normal"}


class VitalCreate(BaseModel):
    """Vital measurement create/update schema."""
    patient_id: int
    vital_type: str
    value: float = Field(gt=0, lt=100000)
    value2: Optional[float] = Field(None, gt=0, lt=100000)
    unit: Optional[str] = None
    measured_at: datetime
    notes: Optional[str] = Field(None, max_length=2000)


def _validate(vital: VitalCreate, db: Session) -> dict:
    """Validate a vital against its type and return the cleaned column values."""
    config = VITAL_TYPES.get(vital.vital_type)
    if not config:
        raise HTTPException(status_code=400, detail=f"Unknown vital type '{vital.vital_type}'")
    if not db.query(PatientModel).filter(PatientModel.id == vital.patient_id).first():
        raise HTTPException(status_code=400, detail="Patient not found")

    unit = vital.unit or next(iter(config["units"]))
    if unit not in config["units"]:
        raise HTTPException(status_code=400, detail=f"Unit '{unit}' is not valid for {config['label']}")

    paired = "value2_label" in config
    if paired and vital.value2 is None:
        raise HTTPException(status_code=400, detail=f"{config['label']} needs both {config['value_label']} and {config['value2_label']}")

    data = vital.model_dump()
    data["unit"] = unit
    data["value2"] = vital.value2 if paired else None
    data["notes"] = (vital.notes or "").strip() or None
    return data


def _get_vital_or_404(vital_id: int, db: Session) -> VitalModel:
    vital = db.query(VitalModel).filter(VitalModel.id == vital_id).first()
    if not vital:
        raise HTTPException(status_code=404, detail="Vital not found")
    return vital


@router.get("/types")
def get_vital_types(db: Session = Depends(get_db)):
    """Supported vital types with their units, typical normal ranges and the preferred unit to show."""
    preferred = preferred_units(db)
    return {vital_type: {**config, "preferred_unit": preferred[vital_type]} for vital_type, config in VITAL_TYPES.items()}


@router.get("/")
def list_vitals(
    patient_id: Optional[int] = Query(None),
    vital_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List vitals, newest first, optionally filtered by patient and type."""
    query = db.query(VitalModel)
    if patient_id is not None:
        query = query.filter(VitalModel.patient_id == patient_id)
    if vital_type:
        query = query.filter(VitalModel.vital_type == vital_type)
    return [v.to_dict() for v in query.order_by(VitalModel.measured_at.desc(), VitalModel.id.desc()).all()]


@router.post("/")
def create_vital(vital: VitalCreate, db: Session = Depends(get_db)):
    """Record a vital measurement."""
    db_vital = VitalModel(**_validate(vital, db))
    db.add(db_vital)
    db.commit()
    db.refresh(db_vital)
    return {"success": True, "message": "Vital recorded", "data": db_vital.to_dict()}


@router.put("/{vital_id}")
def update_vital(vital_id: int, vital: VitalCreate, db: Session = Depends(get_db)):
    """Update a vital measurement."""
    db_vital = _get_vital_or_404(vital_id, db)
    for key, value in _validate(vital, db).items():
        setattr(db_vital, key, value)
    db.commit()
    db.refresh(db_vital)
    return {"success": True, "message": "Vital updated", "data": db_vital.to_dict()}


@router.delete("/{vital_id}")
def delete_vital(vital_id: int, db: Session = Depends(get_db)):
    """Delete a vital measurement."""
    db_vital = _get_vital_or_404(vital_id, db)
    db.delete(db_vital)
    db.commit()
    return {"success": True, "message": "Vital deleted"}
