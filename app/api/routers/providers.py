"""Provider management routes."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Provider as ProviderModel
from ..schemas import ProviderCreate

router = APIRouter()

def _get_provider_or_404(provider_id: int, db: Session) -> ProviderModel:
    """Get provider by ID or raise 404."""
    provider = db.query(ProviderModel).filter(ProviderModel.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider

@router.get("/")
def get_providers(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Get all healthcare providers with optional search and pagination."""
    query = db.query(ProviderModel).options(
        joinedload(ProviderModel.results)
    )

    if search:
        query = query.filter(
            ProviderModel.name.contains(search) |
            ProviderModel.specialty.contains(search)
        )

    providers = query.offset(skip).limit(limit).all()
    return [provider.to_dict() for provider in providers]

@router.get("/{provider_id}")
def get_provider(provider_id: int, db: Session = Depends(get_db)):
    """Get a specific provider by ID."""
    provider = _get_provider_or_404(provider_id, db)
    return provider.to_dict()

@router.post("/")
def create_provider(provider: ProviderCreate, db: Session = Depends(get_db)):
    """Create a new healthcare provider with duplicate name validation."""
    existing = db.query(ProviderModel).filter(ProviderModel.name == provider.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Provider with this name already exists")

    db_provider = ProviderModel(**provider.model_dump())
    db.add(db_provider)
    db.commit()
    db.refresh(db_provider)
    
    return {
        "success": True,
        "message": f"Provider '{provider.name}' created successfully",
        "data": db_provider.to_dict()
    }

@router.put("/{provider_id}")
def update_provider(provider_id: int, provider: ProviderCreate, db: Session = Depends(get_db)):
    """Update an existing provider."""
    db_provider = _get_provider_or_404(provider_id, db)

    existing = db.query(ProviderModel).filter(
        ProviderModel.name == provider.name,
        ProviderModel.id != provider_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Provider with this name already exists")

    for key, value in provider.model_dump().items():
        setattr(db_provider, key, value)

    db.commit()
    db.refresh(db_provider)
    return {
        "success": True,
        "message": f"Provider '{provider.name}' updated successfully",
        "data": db_provider.to_dict()
    }

@router.delete("/{provider_id}")
def delete_provider(provider_id: int, db: Session = Depends(get_db)):
    """Delete a healthcare provider if they have no patient connections."""
    db_provider = _get_provider_or_404(provider_id, db)

    if db_provider.get_patients():
        raise HTTPException(
            status_code=400,
            detail="Cannot delete provider with patient connections"
        )

    provider_name = db_provider.name
    db.delete(db_provider)
    db.commit()
    return {
        "success": True,
        "message": f"Provider '{provider_name}' deleted successfully"
    }

