"""SQLAlchemy models for MyLabVault."""

import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, ForeignKey, Text, Boolean, func, or_, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, Session

Base = declarative_base()


def _num(value) -> str:
    """A number as written on a report: 150 rather than 150.0."""
    value = float(value)
    return str(int(value)) if value.is_integer() else str(value)

class Panel(Base):
    """Lab test panel model."""
    __tablename__ = "panels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)

    labs = relationship("Lab", back_populates="panel")

    def get_lab_count(self) -> int:
        return len(self.labs)

    def get_abnormal_results_count(self) -> int:
        """Count abnormal results across all labs in this panel."""
        count = 0
        for lab in self.labs:
            for result in lab.results:
                if not lab.is_result_normal(result.result):
                    count += 1
        return count

    def get_total_results_count(self) -> int:
        return sum(len(lab.results) for lab in self.labs)

    def to_dict(self) -> Dict[str, Any]:
        """Convert panel to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "lab_count": self.get_lab_count(),
            "total_results": self.get_total_results_count(),
            "abnormal_results": self.get_abnormal_results_count()
        }

class Patient(Base):
    """Patient model."""
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(10), nullable=True)

    results = relationship("LabResult", back_populates="patient")
    vitals = relationship("Vital", back_populates="patient")
    medications = relationship("Medication", back_populates="patient")
    immunizations = relationship("Immunization", back_populates="patient")

    def get_age(self) -> Optional[int]:
        """Calculate patient age with proper leap year handling."""
        if self.date_of_birth is None:
            return None
        today = datetime.now().date()
        birth_date = self.date_of_birth
        # Subtract 1 if birthday hasn't occurred this year
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

    def get_recent_results(self, limit: int = 10) -> List[Any]:
        return sorted(self.results, key=lambda x: x.date_collected, reverse=True)[:limit]

    def get_result_count(self) -> int:
        return len(self.results)

    def get_abnormal_results(self) -> List[Any]:
        """Get all abnormal results for this patient."""
        abnormal = []
        for result in self.results:
            if result.lab and not result.lab.is_result_normal(result.result):
                abnormal.append(result)
        return abnormal


    def to_dict(self) -> Dict[str, Any]:
        """Convert patient to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "date_of_birth": self.date_of_birth.isoformat() if self.date_of_birth is not None else None,
            "gender": self.gender,
            "age": self.get_age(),
            "result_count": self.get_result_count(),
            "abnormal_results_count": len(self.get_abnormal_results())
        }

class Provider(Base):
    """Healthcare provider model."""
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    specialty = Column(String(255), nullable=True)

    results = relationship("LabResult", back_populates="provider")

    def get_result_count(self) -> int:
        """Get total count of results for this provider."""
        return len(self.results)

    def get_recent_results(self, days: int = 30) -> List[Any]:
        """Get recent results for this provider."""
        cutoff_date = datetime.now() - timedelta(days=days)
        return [result for result in self.results if result.date_collected >= cutoff_date]

    def get_patients(self) -> List[Any]:
        """Get unique patients for this provider."""
        patient_ids = set()
        patients = []
        for result in self.results:
            if result.patient_id not in patient_ids:
                patient_ids.add(result.patient_id)
                patients.append(result.patient)
        return patients


    def to_dict(self) -> Dict[str, Any]:
        """Convert provider to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "specialty": self.specialty,
            "result_count": self.get_result_count(),
            "patient_count": len(self.get_patients()),
            "recent_results_count": len(self.get_recent_results())
        }

class Unit(Base):
    """Lab test unit model."""
    __tablename__ = "units"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True, index=True)

    labs = relationship("Lab", back_populates="unit")

    def get_lab_count(self) -> int:
        """Get count of labs using this unit."""
        return len(self.labs)


    def to_dict(self) -> Dict[str, Any]:
        """Convert unit to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "symbol": self.name,  # Use name as symbol since there's no separate symbol column
            "lab_count": self.get_lab_count()
        }

class Lab(Base):
    """Lab test model."""
    __tablename__ = "labs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    panel_id = Column(Integer, ForeignKey("panels.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    ref_low = Column(Float, nullable=True)
    ref_high = Column(Float, nullable=True)
    ref_type = Column(String(10), nullable=True, default="range")  # 'range', 'greater', 'less'
    ref_value = Column(Float, nullable=True)  # Single value for greater/less than
    description = Column(Text, nullable=True)  # User's own plain-language description

    panel = relationship("Panel", back_populates="labs")
    unit = relationship("Unit", back_populates="labs")
    results = relationship("LabResult", back_populates="lab")

    @property
    def display_description(self) -> Optional[str]:
        """The user's description, else a built-in plain-language one for common tests."""
        if self.description and self.description.strip():
            return self.description.strip()
        from .services.test_descriptions import describe
        return describe(self.name)

    def reference_bounds(self) -> tuple:
        """(low, high, inclusive) for this test's reference range; a bound is None when open-ended."""
        if self.ref_type == "greater" and self.ref_value is not None:
            return float(self.ref_value), None, False
        if self.ref_type == "less" and self.ref_value is not None:
            return None, float(self.ref_value), False
        low = float(self.ref_low) if self.ref_low is not None else None
        high = float(self.ref_high) if self.ref_high is not None else None
        return low, high, True

    def is_result_normal(self, value: float) -> bool:
        """Check if a result value is within normal range (no range counts as normal)."""
        if value is None:
            return True
        return self.get_result_status(value) in ("normal", "unknown")

    def get_result_status(self, value: float) -> str:
        """Get status of a result value: normal, high, low, or unknown when there is no range."""
        try:
            value = float(value)
            low, high, inclusive = self.reference_bounds()
        except (ValueError, TypeError):
            return "unknown"
        if low is None and high is None:
            return "unknown"
        if low is not None and (value < low or (not inclusive and value == low)):
            return "low"
        if high is not None and (value > high or (not inclusive and value == high)):
            return "high"
        return "normal"


    def get_result_count(self) -> int:
        """Get total count of results for this lab."""
        return len(self.results)

    def get_abnormal_results_count(self) -> int:
        """Get count of abnormal results for this lab."""
        return sum(1 for result in self.results if not result.is_normal)

    def to_dict(self) -> Dict[str, Any]:
        """Convert lab to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "panel_id": self.panel_id,
            "panel_name": self.panel.name if self.panel else None,
            "unit_id": self.unit_id,
            "unit_name": self.unit.name if self.unit else None,
            "unit_symbol": self.unit.name if self.unit else None,
            "ref_low": self.ref_low,
            "ref_high": self.ref_high,
            "ref_type": self.ref_type,
            "ref_value": self.ref_value,
            "description": self.description,
            "display_description": self.display_description,
            "active": True,
            "result_count": self.get_result_count(),
            "abnormal_results_count": self.get_abnormal_results_count()
        }

class LabResult(Base):
    """Lab result model."""
    __tablename__ = "lab_results"

    id = Column(Integer, primary_key=True, index=True)
    lab_id = Column(Integer, ForeignKey("labs.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False, index=True)
    result = Column(Float, nullable=True)  # Nullable for qualitative tests
    result_text = Column(String(255), nullable=True)  # For qualitative results like "Negative", "Positive"
    date_collected = Column(DateTime, nullable=False, index=True)
    notes = Column(Text, nullable=True)
    pdf_import_id = Column(String(255), nullable=True, index=True)
    # Reference range printed on the report for this result; overrides the lab test's range when set
    ref_low = Column(Float, nullable=True)
    ref_high = Column(Float, nullable=True)
    ref_text = Column(String(100), nullable=True)
    flag = Column(String(20), nullable=True)  # Abnormal flag as printed by the lab (H, L, A, Critical, ...)
    lab_comment = Column(Text, nullable=True)  # Comment printed by the lab for this result
    fasting = Column(Boolean, nullable=True)  # None when unknown

    lab = relationship("Lab", back_populates="results")
    patient = relationship("Patient", back_populates="results")
    provider = relationship("Provider", back_populates="results")
    pdf_import = relationship("PDFImportLog", foreign_keys=[pdf_import_id], primaryjoin="LabResult.pdf_import_id == cast(PDFImportLog.id, String)")

    @property
    def pdf_filename(self) -> Optional[str]:
        """Get PDF filename via relationship."""
        return self.pdf_import.filename if self.pdf_import else None

    @property
    def has_own_range(self) -> bool:
        """Whether a reference range was recorded with this result."""
        return self.ref_low is not None or self.ref_high is not None

    @property
    def effective_ref_low(self) -> Optional[float]:
        """Lower reference bound used for this result (its own range, else the lab test's)."""
        if self.has_own_range:
            return self.ref_low
        if self.lab:
            return self.lab.ref_value if self.lab.ref_type == "greater" else self.lab.ref_low
        return None

    @property
    def effective_ref_high(self) -> Optional[float]:
        """Upper reference bound used for this result (its own range, else the lab test's)."""
        if self.has_own_range:
            return self.ref_high
        if self.lab:
            return self.lab.ref_value if self.lab.ref_type == "less" else self.lab.ref_high
        return None

    @property
    def flag_status(self) -> Optional[str]:
        """Map the lab's printed flag to a status, if there is one."""
        flag = (self.flag or "").strip().lower()
        if not flag:
            return None
        if flag in ("h", "hi", "high", "hh", "high critical"):
            return "high"
        if flag in ("l", "lo", "low", "ll", "low critical"):
            return "low"
        return "abnormal"

    @property
    def status(self) -> str:
        """Get the status of this result: normal, low, high, abnormal or unknown."""
        status = "unknown"
        if self.result is not None:
            if self.has_own_range:
                low, high = self.ref_low, self.ref_high
                if low is not None and high is not None:
                    status = "low" if self.result < low else "high" if self.result > high else "normal"
                elif low is not None:
                    status = "normal" if self.result > low else "low"
                else:
                    status = "normal" if self.result < high else "high"
            elif self.lab:
                status = self.lab.get_result_status(self.result)
        if status == "unknown":
            # Qualitative results, or numeric ones without a range: fall back to the lab's flag
            status = self.flag_status or "unknown"
        return status

    def get_status(self) -> str:
        """Get the status of this result (backward compatibility)."""
        return self.status

    @property
    def is_normal(self) -> bool:
        """Check if this result is normal (results without enough information count as normal)."""
        return self.status in ("normal", "unknown")

    @property
    def reference_range(self) -> Optional[str]:
        """Get the reference range for this result (as printed on its report when available)."""
        if self.ref_text:
            return self.ref_text
        if self.has_own_range:
            if self.ref_low is not None and self.ref_high is not None:
                return f"{_num(self.ref_low)} - {_num(self.ref_high)}"
            if self.ref_low is not None:
                return f"> {_num(self.ref_low)}"
            return f"< {_num(self.ref_high)}"
        if not self.lab:
            return None

        # Handle different reference range types
        if self.lab.ref_type == "greater" and self.lab.ref_value is not None:
            return f"> {_num(self.lab.ref_value)}"
        elif self.lab.ref_type == "less" and self.lab.ref_value is not None:
            return f"< {_num(self.lab.ref_value)}"
        low, high = self.lab.ref_low, self.lab.ref_high
        if low is not None and high is not None:
            return f"{_num(low)} - {_num(high)}"
        if low is not None:
            return f"≥ {_num(low)}"
        if high is not None:
            return f"≤ {_num(high)}"
        return None

    def get_reference_range(self) -> Optional[str]:
        """Get the reference range for this result (backward compatibility)."""
        return self.reference_range

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "id": self.id,
            "lab_id": self.lab_id,
            "lab_name": self.lab.name if self.lab else None,
            "panel_name": self.lab.panel.name if self.lab and self.lab.panel else None,
            "patient_id": self.patient_id,
            "patient_name": self.patient.name if self.patient else None,
            "provider_id": self.provider_id,
            "provider_name": self.provider.name if self.provider else None,
            "value": self.result,
            "result_text": self.result_text,
            "date_collected": self.date_collected.isoformat() if self.date_collected is not None else None,
            "notes": self.notes,
            "ref_low": self.ref_low,
            "ref_high": self.ref_high,
            "ref_text": self.ref_text,
            "flag": self.flag,
            "lab_comment": self.lab_comment,
            "fasting": self.fasting,
            "status": self.status,
            "is_normal": self.is_normal,
            "reference_range": self.reference_range,
            "unit_symbol": self.lab.unit.name if self.lab and self.lab.unit else None,
            "pdf_import_id": self.pdf_import_id,
            "pdf_filename": self.pdf_filename
        }

class Vital(Base):
    """A vital sign or body measurement recorded for a patient (weight, blood pressure, ...)."""
    __tablename__ = "vitals"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    vital_type = Column(String(30), nullable=False, index=True)
    value = Column(Float, nullable=False)
    value2 = Column(Float, nullable=True)  # Second value for paired readings (diastolic blood pressure)
    unit = Column(String(20), nullable=True)
    measured_at = Column(DateTime, nullable=False, index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    patient = relationship("Patient", back_populates="vitals")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "patient_id": self.patient_id,
            "vital_type": self.vital_type,
            "value": self.value,
            "value2": self.value2,
            "unit": self.unit,
            "measured_at": self.measured_at.isoformat() if self.measured_at else None,
            "notes": self.notes,
        }


MEDICATION_KINDS = ("medication", "supplement")


class Medication(Base):
    """A medication or supplement a patient takes at one dose for a period.

    A dose change ends one period and starts the next, so each row is one dose; the rows with the
    same name make up that medication's history. end_date is empty while it's still being taken.
    """
    __tablename__ = "medications"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    kind = Column(String(20), nullable=False, default="medication")
    dose = Column(String(100), nullable=True)        # as written, e.g. "100 mg" or "2 tablets"
    frequency = Column(String(100), nullable=True)   # e.g. "once daily", "weekly", "as needed"
    route = Column(String(50), nullable=True)        # e.g. "by mouth", "injection"
    start_date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, nullable=True)
    reason = Column(String(255), nullable=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)  # prescriber
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    patient = relationship("Patient", back_populates="medications")
    provider = relationship("Provider")

    def is_current(self, today=None) -> bool:
        """Still being taken: no stop date, or one that hasn't passed yet."""
        today = today or datetime.now().date()
        return self.end_date is None or self.end_date >= today

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "patient_id": self.patient_id,
            "name": self.name,
            "kind": self.kind,
            "dose": self.dose,
            "frequency": self.frequency,
            "route": self.route,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "reason": self.reason,
            "provider_id": self.provider_id,
            "provider_name": self.provider.name if self.provider else None,
            "notes": self.notes,
            "current": self.is_current(),
        }


class Immunization(Base):
    """One dose of a vaccine given to a patient, with the date the next dose is due (if any)."""
    __tablename__ = "immunizations"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    vaccine = Column(String(255), nullable=False, index=True)   # e.g. "COVID-19", "Influenza (flu)"
    date_given = Column(Date, nullable=False, index=True)
    dose = Column(String(50), nullable=True)            # e.g. "1 of 2", "booster"
    manufacturer = Column(String(100), nullable=True)   # manufacturer or brand, e.g. "Pfizer", "Shingrix"
    lot_number = Column(String(50), nullable=True)
    site = Column(String(50), nullable=True)            # e.g. "left arm"
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)  # given by a saved provider
    location = Column(String(255), nullable=True)       # or a clinic or pharmacy name
    next_due = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    patient = relationship("Patient", back_populates="immunizations")
    provider = relationship("Provider")

    DUE_SOON_DAYS = 30

    def due_status(self, today=None) -> Optional[str]:
        """'overdue', 'due' (within DUE_SOON_DAYS) or 'scheduled' for the next dose; None when none is due."""
        if self.next_due is None:
            return None
        today = today or datetime.now().date()
        if self.next_due < today:
            return "overdue"
        return "due" if (self.next_due - today).days <= self.DUE_SOON_DAYS else "scheduled"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "patient_id": self.patient_id,
            "vaccine": self.vaccine,
            "date_given": self.date_given.isoformat() if self.date_given else None,
            "dose": self.dose,
            "manufacturer": self.manufacturer,
            "lot_number": self.lot_number,
            "site": self.site,
            "provider_id": self.provider_id,
            "provider_name": self.provider.name if self.provider else None,
            "location": self.location,
            "next_due": self.next_due.isoformat() if self.next_due else None,
            "notes": self.notes,
            "due_status": self.due_status(),
        }


class PDFImportLog(Base):
    """PDF import log model."""
    __tablename__ = "pdf_import_logs"

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    file_hash = Column(String(64), nullable=True)
    batch_id = Column(String(36), nullable=True)  # UUID for batch operations
    total_tests_found = Column(Integer, default=0)
    tests_imported = Column(Integer, default=0)
    tests_skipped = Column(Integer, default=0)
    date_collected = Column(String(50), nullable=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)  # Selected provider
    status = Column(String(50), default="pending")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    file_path = Column(String(500), nullable=False)
    parsed_data = Column(Text, nullable=True)  # JSON string of parsed data

    provider = relationship("Provider")

    def to_dict(self) -> Dict[str, Any]:
        """Convert import log to dictionary."""
        return {
            "id": self.id,
            "filename": self.filename,
            "file_hash": self.file_hash,
            "batch_id": self.batch_id,
            "total_tests_found": self.total_tests_found,
            "tests_imported": self.tests_imported,
            "tests_skipped": self.tests_skipped,
            "date_collected": self.date_collected,
            "provider_name": self.provider.name if self.provider else None,
            "provider_id": self.provider_id,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at is not None else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at is not None else None,
            "file_path": self.file_path,
            "parsed_data": self.parsed_data
        }


class ImportTemplate(Base):
    """Import template model for storing user preferences."""
    __tablename__ = "import_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    default_provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)
    auto_select_tests = Column(String(5), default="true")  # JSON boolean as string for SQLite
    date_preference = Column(String(20), default="pdf_date")
    test_filters = Column(Text, nullable=True)  # JSON string for filters
    created_at = Column(DateTime, default=datetime.now)

    provider = relationship("Provider")

    def to_dict(self) -> Dict[str, Any]:
        """Convert template to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "default_provider_id": self.default_provider_id,
            "provider_name": self.provider.name if self.provider else None,
            "auto_select_tests": self.auto_select_tests == "true",
            "date_preference": self.date_preference,
            "test_filters": json.loads(self.test_filters) if self.test_filters else {},
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class UserSettings(Base):
    """User settings model for storing UI preferences in JSON format."""
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, default=1, index=True)  # Single user for now
    options = Column(Text, default='{"sidebar_open": true, "dark_mode": false}')  # JSON string
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_settings(cls, db: Session, user_id: int = 1) -> 'UserSettings':
        """
        Get user settings, create default if not exists.
        
        Args:
            db: Database session
            user_id: User ID (default: 1 for single user)
            
        Returns:
            UserSettings: User settings object
        """
        settings = db.query(cls).filter(cls.user_id == user_id).first()
        if not settings:
            # Create default settings
            default_options = {
                "sidebar_open": True,
                "dark_mode": False
            }
            settings = cls(
                user_id=user_id,
                options=json.dumps(default_options)
            )
            db.add(settings)
            db.commit()
            db.refresh(settings)
        return settings

    @classmethod
    def update_settings(cls, db: Session, user_id: int = 1, **kwargs) -> 'UserSettings':
        """
        Update user settings in JSON options.
        
        Args:
            db: Database session
            user_id: User ID (default: 1 for single user)
            **kwargs: Settings to update (sidebar_open, dark_mode, etc.)
            
        Returns:
            UserSettings: Updated settings object
        """
        settings = cls.get_settings(db, user_id)
        
        # Parse current options
        try:
            options = json.loads(settings.options) if settings.options else {}
        except (json.JSONDecodeError, TypeError):
            # If JSON is invalid, start with default options
            options = {"sidebar_open": True, "dark_mode": False}
        
        # Update provided settings
        for key, value in kwargs.items():
            options[key] = value
        
        # Save back to database
        settings.options = json.dumps(options)
        settings.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(settings)
        return settings

    def to_dict(self) -> Dict[str, Any]:
        """Convert settings to dictionary."""
        # Parse options JSON
        try:
            options = json.loads(self.options) if self.options else {}
        except (json.JSONDecodeError, TypeError):
            options = {"sidebar_open": True, "dark_mode": False}
        
        return {
            "id": self.id,
            "user_id": self.user_id,
            **options,  # Spread the options into the response
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

    def get_option(self, key: str, default=None):
        """Get a specific option value."""
        try:
            options = json.loads(self.options) if self.options else {}
            return options.get(key, default)
        except (json.JSONDecodeError, TypeError):
            return default

    def set_option(self, key: str, value, db: Session = None):
        """Set a specific option value."""
        try:
            options = json.loads(self.options) if self.options else {}
        except (json.JSONDecodeError, TypeError):
            options = {}
        
        options[key] = value
        self.options = json.dumps(options)
        self.updated_at = datetime.utcnow()
        
        if db:
            db.commit()
            db.refresh(self)

    @property
    def date_format(self) -> str:
        """Get the user's preferred date format."""
        return self.get_option('date_format', 'MM/DD/YYYY')
