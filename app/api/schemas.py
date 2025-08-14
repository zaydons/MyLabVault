"""Pydantic schemas for API request/response models."""

from datetime import datetime, date
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator


class PanelBase(BaseModel):
    """Base schema for lab test panels."""
    name: str = Field(..., min_length=1, max_length=255)


class PanelCreate(PanelBase):
    pass


class Panel(PanelBase):
    """Panel response schema with database ID."""
    id: int

    class Config:
        from_attributes = True


class PatientBase(BaseModel):
    name: str
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None


class PatientCreate(PatientBase):
    pass


class Patient(PatientBase):
    id: int

    class Config:
        from_attributes = True


class ProviderBase(BaseModel):
    name: str
    specialty: Optional[str] = None


class ProviderCreate(ProviderBase):
    pass


class Provider(ProviderBase):
    id: int

    class Config:
        from_attributes = True


class UnitBase(BaseModel):
    name: str


class UnitCreate(UnitBase):
    pass


class Unit(UnitBase):
    id: int

    class Config:
        from_attributes = True


class LabBase(BaseModel):
    name: str
    panel_id: int
    unit_id: Optional[int] = None
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    ref_type: Optional[str] = "range"
    ref_value: Optional[float] = None


class LabCreate(LabBase):
    pass



class Lab(LabBase):
    id: int
    panel: Optional[Panel] = None
    unit: Optional[Unit] = None

    class Config:
        from_attributes = True


class LabResultBase(BaseModel):
    """Base lab result schema."""
    lab_id: int
    patient_id: int
    provider_id: int
    result: Optional[float] = None
    result_text: Optional[str] = None
    date_collected: datetime
    notes: Optional[str] = None
    pdf_import_id: Optional[str] = None
    @field_validator('pdf_import_id', mode='before')
    @classmethod
    def convert_pdf_import_id_to_string(cls, v):
        """Convert pdf_import_id to string if it's not already"""
        if v is not None:
            return str(v)
        return v


class LabResultCreate(LabResultBase):
    pass



class LabResult(LabResultBase):
    """Lab result response schema."""
    id: int
    status: Optional[str] = None
    is_normal: Optional[bool] = None
    reference_range: Optional[str] = None

    class Config:
        from_attributes = True


class LabResultWithDetails(LabResult):
    """Lab result with full relationship details."""
    lab: Lab
    patient: Patient
    provider: Provider

    class Config:
        from_attributes = True



class APIResponse(BaseModel):
    """Standard API response schema."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None



class PDFImportPreview(BaseModel):
    """PDF import preview response schema."""
    filename: str
    date_collected: Optional[str] = None
    total_tests_found: int
    importable_tests: List[Dict[str, Any]] = []
    problematic_tests: List[Dict[str, Any]] = []
    matched_provider: Optional[Provider] = None
    import_id: Optional[str] = None
    duplicate_warning: Optional[Dict[str, Any]] = None


class PDFImportConfirm(BaseModel):
    """PDF import confirmation schema."""
    import_id: str
    selected_tests: List[int]
    provider_id: Optional[int] = None
    patient_id: int = 1
    manual_date: Optional[str] = None


class PaginatedLabResults(BaseModel):
    """
    Paginated lab results response schema.
    
    Provides both the results data and pagination metadata
    for improved frontend pagination handling.
    """
    results: List[LabResultWithDetails]
    total_count: int = Field(..., description="Total number of results matching filters")
    page: int = Field(..., description="Current page number (1-based)")
    page_size: int = Field(..., description="Number of results per page")
    total_pages: int = Field(..., description="Total number of pages")
    has_next: bool = Field(..., description="Whether there are more pages available")
    has_prev: bool = Field(..., description="Whether there are previous pages available")
    
    class Config:
        from_attributes = True


# Settings Schemas

class UserSettingsBase(BaseModel):
    """Base schema for user settings."""
    sidebar_open: bool = Field(True, description="Whether sidebar is open")
    dark_mode: bool = Field(False, description="Whether dark mode is enabled")


class UserSettingsUpdate(BaseModel):
    """Schema for updating user settings. Allows additional fields for extensibility."""
    sidebar_open: Optional[bool] = Field(None, description="Whether sidebar is open")
    dark_mode: Optional[bool] = Field(None, description="Whether dark mode is enabled")

    class Config:
        extra = "allow"  # Allow additional fields for extensibility


class UserSettings(UserSettingsBase):
    """
    Complete user settings schema with database fields.
    
    Used for API responses and includes auto-generated database fields.
    Allows additional fields for extensibility.
    """
    id: int = Field(..., description="Settings ID")
    user_id: int = Field(..., description="User ID")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True
        extra = "allow"  # Allow additional fields for extensibility
