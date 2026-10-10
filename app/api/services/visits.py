"""Visits: a patient's results grouped by the day they were collected (one blood draw).

Each result on a visit also gets where it falls on its reference range (for the range bar)
and the previous result of the same test, so a visit can be read on its own.
"""

from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session, joinedload

from ..models import Lab, LabResult

OUT_OF_RANGE = ("high", "low", "abnormal")


def range_scale(value: Optional[float], low: Optional[float], high: Optional[float]) -> Optional[Dict[str, float]]:
    """Positions (0-100) of the reference range and the value on a bar, or None when there's nothing to draw.

    The bar shows the normal range in the middle with room on either side, widened so an
    out-of-range value still lands on it. A one-sided range (e.g. eGFR > 60) gets a band of
    the same scale on its open side.
    """
    if value is None or (low is None and high is None):
        return None
    if low is not None and high is not None:
        span = high - low
        if span <= 0:
            return None
        start, end = low - span * 0.5, high + span * 0.5
    elif low is not None:
        span = abs(low) or 1.0
        start, end = low - span * 0.5, low + span
    else:
        span = abs(high) or 1.0
        start, end = min(0.0, high - span), high + span * 0.5
    if value < start or value > end:
        # Leave a little room past the value so its marker isn't on the very edge
        margin = (max(end, value) - min(start, value)) * 0.05
        start, end = min(start, value - margin), max(end, value + margin)

    def pos(x: float) -> float:
        return round(max(0.0, min(100.0, (x - start) / (end - start) * 100)), 2)

    return {
        "low": pos(low) if low is not None else 0.0,
        "high": pos(high) if high is not None else 100.0,
        "value": pos(value),
    }


def _results(db: Session, patient_id: int) -> List[LabResult]:
    return (
        db.query(LabResult)
        .options(
            joinedload(LabResult.lab).joinedload(Lab.unit),
            joinedload(LabResult.lab).joinedload(Lab.panel),
            joinedload(LabResult.provider),
        )
        .filter(LabResult.patient_id == patient_id)
        .order_by(LabResult.date_collected.desc(), LabResult.id.desc())
        .all()
    )


def list_visits(db: Session, patient_id: int) -> List[dict]:
    """The patient's visits, newest first, with how many results each has and how many are out of range."""
    visits: Dict[date, dict] = {}
    for result in _results(db, patient_id):
        day = result.date_collected.date()
        visit = visits.setdefault(day, {"date": day, "count": 0, "out_of_range": 0, "providers": [], "panels": []})
        visit["count"] += 1
        if result.status in OUT_OF_RANGE:
            visit["out_of_range"] += 1
        if result.provider and result.provider.name not in visit["providers"]:
            visit["providers"].append(result.provider.name)
        panel = result.lab.panel.name if result.lab and result.lab.panel else None
        if panel and panel not in visit["panels"]:
            visit["panels"].append(panel)
    for visit in visits.values():
        visit["panels"].sort(key=str.lower)
    return list(visits.values())


def get_visit(db: Session, patient_id: int, day: date) -> Optional[dict]:
    """One visit's results grouped by panel, with range positions and previous results; None if there were none that day."""
    results = _results(db, patient_id)
    days = sorted({r.date_collected.date() for r in results}, reverse=True)
    if day not in days:
        return None

    # Earlier results of each test, newest first, for the "previous" column
    earlier = defaultdict(list)
    for r in results:
        if r.date_collected.date() < day:
            earlier[r.lab_id].append(r)

    panels: Dict[str, List[dict]] = defaultdict(list)
    counts = {"normal": 0, "high": 0, "low": 0, "abnormal": 0, "unknown": 0}
    providers, fasting = [], set()
    for r in results:
        if r.date_collected.date() != day:
            continue
        status = r.status
        counts[status] = counts.get(status, 0) + 1
        if r.provider and r.provider.name not in providers:
            providers.append(r.provider.name)
        if r.fasting is not None:
            fasting.add(r.fasting)
        previous = next((p for p in earlier[r.lab_id] if p.result is not None), None) if r.result is not None else None
        if previous is None and r.result is None:
            previous = next(iter(earlier[r.lab_id]), None)
        panel = r.lab.panel.name if r.lab and r.lab.panel else "Other"
        panels[panel].append({
            "result": r,
            "status": status,
            "unit": r.lab.unit.name if r.lab and r.lab.unit else "",
            "scale": range_scale(r.result, r.effective_ref_low, r.effective_ref_high),
            "previous": previous,
            "change": (r.result - previous.result) if previous is not None and r.result is not None and previous.result is not None else None,
        })

    for rows in panels.values():
        rows.sort(key=lambda row: (row["result"].lab.name.lower() if row["result"].lab else ""))
    index = days.index(day)
    return {
        "date": day,
        "panels": sorted(panels.items(), key=lambda item: item[0].lower()),
        "count": sum(counts.values()),
        "counts": counts,
        "out_of_range": sum(counts[s] for s in OUT_OF_RANGE),
        "providers": providers,
        "fasting": fasting.pop() if len(fasting) == 1 else None,
        "previous_date": days[index + 1] if index + 1 < len(days) else None,
        "next_date": days[index - 1] if index > 0 else None,
        "all_dates": days,
    }
