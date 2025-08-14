"""SQLAlchemy models for MyLabVault."""

import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, ForeignKey, Text, func, or_, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, Session

Base = declarative_base()

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

    panel = relationship("Panel", back_populates="labs")
    unit = relationship("Unit", back_populates="labs")
    results = relationship("LabResult", back_populates="lab")

    def is_result_normal(self, value: float) -> bool:
        """Check if a result value is within normal range."""
        if value is None:
            return True
        try:
            # Handle different reference range types
            if self.ref_type == "greater" and self.ref_value is not None:
                return value > self.ref_value
            elif self.ref_type == "less" and self.ref_value is not None:
                return value < self.ref_value
            elif self.ref_type == "range" or self.ref_type is None:
                # Traditional range-based check
                ref_low = float(self.ref_low) if self.ref_low is not None else None
                ref_high = float(self.ref_high) if self.ref_high is not None else None
                if ref_low is None or ref_high is None:
                    return True
                return ref_low <= value <= ref_high
            else:
                return True
        except (ValueError, TypeError):
            return True

    def get_result_status(self, value: float) -> str:
        """Get status of a result value (normal, high, low)."""
        try:
            # Handle different reference range types
            if self.ref_type == "greater" and self.ref_value is not None:
                if value > self.ref_value:
                    return "normal"
                else:
                    return "low"
            elif self.ref_type == "less" and self.ref_value is not None:
                if value < self.ref_value:
                    return "normal"
                else:
                    return "high"
            elif self.ref_type == "range" or self.ref_type is None:
                # Traditional range-based check
                if self.ref_low is None or self.ref_high is None:
                    return "unknown"
                if value < self.ref_low:
                    return "low"
                if value > self.ref_high:
                    return "high"
                return "normal"
            else:
                return "unknown"
        except (ValueError, TypeError):
            return "unknown"


    def get_result_count(self) -> int:
        """Get total count of results for this lab."""
        return len(self.results)

    def get_abnormal_results_count(self) -> int:
        """Get count of abnormal results for this lab."""
        return sum(1 for result in self.results if not self.is_result_normal(result.result))

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

    lab = relationship("Lab", back_populates="results")
    patient = relationship("Patient", back_populates="results")
    provider = relationship("Provider", back_populates="results")
    pdf_import = relationship("PDFImportLog", foreign_keys=[pdf_import_id], primaryjoin="LabResult.pdf_import_id == cast(PDFImportLog.id, String)")

    @property
    def pdf_filename(self) -> Optional[str]:
        """Get PDF filename via relationship."""
        return self.pdf_import.filename if self.pdf_import else None

    @property
    def status(self) -> str:
        """Get the status of this result."""
        if self.lab and self.result is not None:
            return self.lab.get_result_status(self.result)
        return "unknown"

    def get_status(self) -> str:
        """Get the status of this result (backward compatibility)."""
        return self.status

    @property 
    def is_normal(self) -> bool:
        """Check if this result is normal."""
        if self.lab and self.result is not None:
            return self.lab.is_result_normal(self.result)
        return True

    @property
    def reference_range(self) -> Optional[str]:
        """Get the reference range for this result."""
        if not self.lab:
            return None

        # Handle different reference range types
        if self.lab.ref_type == "greater" and self.lab.ref_value is not None:
            return f"> {self.lab.ref_value}"
        elif self.lab.ref_type == "less" and self.lab.ref_value is not None:
            return f"< {self.lab.ref_value}"
        elif (self.lab.ref_type == "range" or self.lab.ref_type is None) and \
             self.lab.ref_low is not None and self.lab.ref_high is not None:
            return f"{self.lab.ref_low} - {self.lab.ref_high}"
        else:
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
            "date_collected": self.date_collected.isoformat() if self.date_collected is not None else None,
            "notes": self.notes,
            "status": self.status,
            "is_normal": self.is_normal,
            "reference_range": self.reference_range,
            "unit_symbol": self.lab.unit.name if self.lab and self.lab.unit else None,
            "pdf_import_id": self.pdf_import_id,
            "pdf_filename": self.pdf_filename
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
