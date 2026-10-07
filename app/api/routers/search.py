"""Quick search for the top bar: lab tests by name, plus the app's pages."""

from typing import List

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Lab as LabModel, LabResult as LabResultModel
from .pages import get_selected_patient_id

router = APIRouter()

PAGES = [
    ("Dashboard", "/dashboard", "dashboard home overview"),
    ("All Results", "/results", "results all lab results history"),
    ("Charts", "/charts", "charts trends graphs"),
    ("Vitals", "/vitals", "vitals weight blood pressure heart rate"),
    ("PDF Import", "/import", "import pdf upload report"),
    ("Lab Tests", "/labs", "lab tests manage"),
    ("Panels", "/panels", "panels"),
    ("Units", "/units", "units"),
    ("Providers", "/providers", "providers doctors physicians"),
    ("Patients", "/patients", "patients"),
    ("Settings", "/settings", "settings backup export import dark mode"),
]


@router.get("/")
def search(request: Request, q: str = Query("", max_length=100), db: Session = Depends(get_db)) -> List[dict]:
    """Lab tests whose name contains every word of the query, then matching pages."""
    words = [w for w in q.lower().split() if w]
    if not words:
        return []

    patient_id = get_selected_patient_id(request)
    counts = dict(
        db.query(LabResultModel.lab_id, func.count(LabResultModel.id))
        .filter(LabResultModel.patient_id == patient_id)
        .group_by(LabResultModel.lab_id)
        .all()
    )

    query = db.query(LabModel).options(joinedload(LabModel.panel))
    for word in words:
        query = query.filter(func.lower(LabModel.name).contains(word, autoescape=True))
    labs = query.all()
    # Tests with results for this patient first, then names that start with the query
    labs.sort(key=lambda lab: (counts.get(lab.id, 0) == 0, not lab.name.lower().startswith(words[0]), lab.name.lower()))

    items = [{
        "type": "test",
        "label": lab.name,
        "detail": " · ".join(filter(None, [
            lab.panel.name if lab.panel else None,
            f"{counts.get(lab.id, 0)} result{'s' if counts.get(lab.id, 0) != 1 else ''}",
        ])),
        "url": f"/lab/{lab.id}",
    } for lab in labs[:8]]

    items += [
        {"type": "page", "label": name, "detail": "Page", "url": url}
        for name, url, keywords in PAGES
        if all(word in f"{name.lower()} {keywords}" for word in words)
    ][:4]
    return items
