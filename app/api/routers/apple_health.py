"""Apple Health: vitals for an iPhone Shortcut to log in the Health app.

Apple Health has no web API; only apps on the iPhone write to it. A Shortcut fetches the readings
not sent yet (GET /vitals), logs each with the Log Health Sample action, then confirms them
(POST /vitals/sent) so the next run only gets new ones. Both calls need the token created in
Settings → Apple Health, sent as "Authorization: Bearer <token>". Only the token's hash is kept.
"""

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..logging_setup import audit
from ..models import Patient, UserSettings, Vital
from .vitals import convert

router = APIRouter()

OPTION = "apple_health"
BATCH = 500  # readings per call; the Shortcut runs again (or loops on "more") for the rest

# App vital type → the Health type's name (as in the Log Health Sample action) and unit, US and metric
HEALTH_TYPES = {
    "weight": ("Weight", {"us": "lb", "metric": "kg"}),
    "height": ("Height", {"us": "in", "metric": "cm"}),
    "blood_pressure": ("Blood Pressure", {"us": "mmHg", "metric": "mmHg"}),
    "heart_rate": ("Heart Rate", {"us": "bpm", "metric": "bpm"}),
    "temperature": ("Body Temperature", {"us": "°F", "metric": "°C"}),
    "oxygen_saturation": ("Oxygen Saturation", {"us": "%", "metric": "%"}),
    "respiratory_rate": ("Respiratory Rate", {"us": "breaths/min", "metric": "breaths/min"}),
    "blood_glucose": ("Blood Glucose", {"us": "mg/dL", "metric": "mmol/L"}),
}
DECIMALS = {"weight": 1, "height": 1, "temperature": 1, "blood_glucose": 1}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _config(db: Session) -> dict:
    return UserSettings.get_settings(db).get_option(OPTION) or {}


def _save(db: Session, config: Optional[dict]) -> None:
    settings = UserSettings.get_settings(db)
    if config is None:
        import json
        options = json.loads(settings.options or "{}")
        options.pop(OPTION, None)
        settings.options = json.dumps(options)
        db.commit()
    else:
        settings.set_option(OPTION, config, db)


def _authorized(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> dict:
    """The Apple Health settings, when the request carries their token."""
    config = _config(db)
    if not config.get("token_hash"):
        raise HTTPException(status_code=401, detail="Apple Health isn't set up. Create a token in Settings → Apple Health.")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(_hash(token.strip()), config["token_hash"]):
        raise HTTPException(status_code=401, detail="Missing or wrong token. Copy it again from Settings → Apple Health.")
    return config


def _pending(config: dict, db: Session):
    """The patient's readings not sent yet, oldest saved first (by id: a reading added later for an
    earlier date is still new to Health)."""
    return (db.query(Vital).filter(Vital.patient_id == config.get("patient_id"), Vital.id > (config.get("through") or 0),
                                   Vital.vital_type.in_(HEALTH_TYPES), Vital.value.isnot(None))
            .order_by(Vital.id))


def _status(db: Session) -> dict:
    config = _config(db)
    enabled = bool(config.get("token_hash"))
    patient = db.query(Patient).filter(Patient.id == config.get("patient_id")).first() if enabled else None
    return {
        "enabled": enabled,
        "patient_id": patient.id if patient else None,
        "patient_name": patient.name if patient else None,
        "created_at": config.get("created_at"),
        "last_sync": config.get("last_sync"),
        "pending": _pending(config, db).count() if enabled else 0,
    }


# ---------- set-up (Settings → Apple Health) ----------

class TokenRequest(BaseModel):
    patient_id: int


@router.get("/")
def get_status(db: Session = Depends(get_db)):
    """Whether a token is set up, for whom, when it last synced and how many readings are waiting."""
    return _status(db)


@router.post("/token")
def create_token(body: TokenRequest, db: Session = Depends(get_db)):
    """Create a new token for a patient (replacing any old one). It's returned only this once."""
    if not db.query(Patient).filter(Patient.id == body.patient_id).first():
        raise HTTPException(status_code=400, detail="Patient not found")
    token = secrets.token_urlsafe(24)
    old = _config(db)
    # A new token for the same patient keeps what was sent; another patient starts from the beginning
    through = old.get("through") if old.get("patient_id") == body.patient_id else 0
    _save(db, {"token_hash": _hash(token), "patient_id": body.patient_id, "through": through or 0,
               "created_at": datetime.now().isoformat(timespec="seconds"), "last_sync": old.get("last_sync") if through else None})
    audit("apple_health.token_created", patient_id=body.patient_id)
    return {**_status(db), "token": token}


@router.delete("/token")
def delete_token(db: Session = Depends(get_db)):
    """Turn Apple Health off: the token stops working."""
    _save(db, None)
    audit("apple_health.token_deleted")
    return _status(db)


@router.post("/resend")
def send_everything_again(db: Session = Depends(get_db)):
    """The next sync sends every reading again (e.g. after deleting them in the Health app)."""
    config = _config(db)
    if not config.get("token_hash"):
        raise HTTPException(status_code=400, detail="Apple Health isn't set up")
    _save(db, {**config, "through": 0})
    return _status(db)


@router.post("/mark-sent")
def mark_everything_sent(db: Session = Depends(get_db)):
    """Count every saved reading as sent (they're already in Health, e.g. after restoring a backup)."""
    config = _config(db)
    if not config.get("token_hash"):
        raise HTTPException(status_code=400, detail="Apple Health isn't set up")
    last = db.query(Vital.id).filter(Vital.patient_id == config.get("patient_id")).order_by(Vital.id.desc()).first()
    _save(db, {**config, "through": max(last[0] if last else 0, config.get("through") or 0)})
    return _status(db)


# ---------- for the Shortcut ----------

@router.get("/vitals")
def vitals_to_send(units: Literal["us", "metric"] = Query("us"), config: dict = Depends(_authorized),
                   db: Session = Depends(get_db)):
    """Readings not sent to Health yet, grouped by Health type (every type is always present).

    Each reading is {"value", "unit", "date"}; blood pressure is {"systolic", "diastolic", "unit", "date"}.
    Pass "through" to POST /vitals/sent once they're logged. "more" says another call has more.
    """
    rows = _pending(config, db).limit(BATCH + 1).all()
    more = len(rows) > BATCH
    rows = rows[:BATCH]
    groups = {name: [] for name, _ in HEALTH_TYPES.values()}
    for v in rows:
        name, unit_for = HEALTH_TYPES[v.vital_type]
        unit = unit_for[units]
        app_unit = unit if unit not in ("bpm", "%", "mmHg", "breaths/min") else None
        value = convert(v.vital_type, v.value, v.unit, app_unit) if app_unit else v.value
        value = round(value, DECIMALS.get(v.vital_type, 0)) if v.vital_type != "blood_pressure" else value
        date = v.measured_at.isoformat(timespec="minutes")
        if v.vital_type == "blood_pressure":
            if v.value2 is None:
                continue
            groups[name].append({"systolic": round(v.value), "diastolic": round(v.value2), "unit": unit, "date": date})
        else:
            groups[name].append({"value": value, "unit": unit, "date": date})
    patient = db.query(Patient).filter(Patient.id == config.get("patient_id")).first()
    return {
        "patient": patient.name if patient else None,
        "units": units,
        "count": sum(len(g) for g in groups.values()),
        "through": rows[-1].id if rows else (config.get("through") or 0),
        "more": more,
        **groups,
    }


class SentRequest(BaseModel):
    through: int


@router.post("/vitals/sent")
def vitals_sent(body: SentRequest, config: dict = Depends(_authorized), db: Session = Depends(get_db)):
    """Record readings up to "through" (from GET /vitals) as logged in Health."""
    newest = db.query(Vital.id).filter(Vital.patient_id == config.get("patient_id")).order_by(Vital.id.desc()).first()
    through = max(config.get("through") or 0, min(body.through, newest[0] if newest else 0))
    _save(db, {**config, "through": through, "last_sync": datetime.now().isoformat(timespec="seconds")})
    status = _status(db)
    audit("apple_health.synced", patient_id=config.get("patient_id"), pending=status["pending"])
    return {"success": True, "pending": status["pending"], "last_sync": status["last_sync"]}
