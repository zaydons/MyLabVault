"""Find and merge duplicate lab tests, panels, units and providers (Settings → Merge duplicates)."""

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import ai_parser, cleanup
from ..services.ai_parser import AIParseError
from ..logging_setup import audit

logger = logging.getLogger(__name__)
router = APIRouter()


class MergeRequest(BaseModel):
    kind: Literal["labs", "panels", "units", "providers"]
    keep_id: int
    merge_ids: List[int] = Field(..., min_length=1)
    name: Optional[str] = Field(None, max_length=255)  # Rename the kept item


class MergeBatch(BaseModel):
    merges: List[MergeRequest] = Field(..., min_length=1)


def _response(groups, inv):
    return {
        "groups": groups,
        "totals": {kind: len(inv[kind]) for kind in cleanup.KINDS},
        "ai_enabled": ai_parser.is_enabled(),
    }


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db)):
    """Likely duplicates found by simple rules (no AI)."""
    inv = cleanup.inventory(db)
    return _response(cleanup.rule_suggestions(inv), inv)


@router.post("/suggestions/ai")
async def ai_suggestions(db: Session = Depends(get_db)):
    """Likely duplicates from the rules plus Claude's review of every name (names only, no results)."""
    if not ai_parser.is_enabled():
        raise HTTPException(status_code=400, detail="AI is not enabled")
    inv = cleanup.inventory(db)
    try:
        ai_groups = await cleanup.ai_suggestions(inv)
    except AIParseError as e:
        raise HTTPException(status_code=400, detail=f"AI review failed: {e}")
    return _response(cleanup.combine(cleanup.rule_suggestions(inv), ai_groups), inv)


@router.post("/merge")
def merge(batch: MergeBatch, db: Session = Depends(get_db)):
    """Apply the merges the user confirmed, all or nothing."""
    done = []
    try:
        for m in batch.merges:
            done.append(cleanup.merge(m.kind, m.keep_id, m.merge_ids, m.name, db))
        db.commit()
    except cleanup.MergeError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Merging failed; nothing was changed.") from e

    for m, d in zip(batch.merges, done):
        audit("cleanup.merged", kind=m.kind, keep_id=d["keep_id"], merged_ids=[i for i in m.merge_ids if i != m.keep_id],
              moved=d["moved"], renamed=bool(m.name and m.name.strip()))

    from ..utils.cache import api_cache
    api_cache.clear()
    return {"success": True, "merged": done}
