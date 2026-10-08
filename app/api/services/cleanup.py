"""Finding and merging duplicate lab tests, panels, units and providers.

Suggestions come from simple rules (the same name with a unit suffix, the same unit spelled
differently, the same words in another order) and, optionally, from Claude. Nothing is merged
until the user has reviewed a suggestion and confirmed it.
"""

import re
from collections import defaultdict
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import ImportTemplate, Lab, LabResult, Panel, PDFImportLog, Provider, Unit
from . import ai_parser
from .import_review import normalize_unit, units_match

KINDS = ("labs", "panels", "units", "providers")
KIND_LABEL = {"labs": "test", "panels": "panel", "units": "unit", "providers": "provider"}


class MergeError(ValueError):
    """A merge that can't be done; the message is shown to the user."""


# ---------- what exists ----------

def inventory(db: Session) -> Dict[str, List[Dict[str, Any]]]:
    """Every lab test, panel, unit and provider with what uses it."""
    results_per_lab = dict(db.query(LabResult.lab_id, func.count(LabResult.id)).group_by(LabResult.lab_id).all())
    results_per_provider = dict(db.query(LabResult.provider_id, func.count(LabResult.id)).group_by(LabResult.provider_id).all())
    labs_per_panel = dict(db.query(Lab.panel_id, func.count(Lab.id)).group_by(Lab.panel_id).all())
    labs_per_unit = dict(db.query(Lab.unit_id, func.count(Lab.id)).group_by(Lab.unit_id).all())
    return {
        "labs": [{"id": lab.id, "name": lab.name, "unit": lab.unit.name if lab.unit else "",
                  "panel": lab.panel.name if lab.panel else "", "count": results_per_lab.get(lab.id, 0)}
                 for lab in db.query(Lab).order_by(Lab.name).all()],
        "panels": [{"id": p.id, "name": p.name, "count": labs_per_panel.get(p.id, 0)}
                   for p in db.query(Panel).order_by(Panel.name).all()],
        "units": [{"id": u.id, "name": u.name, "count": labs_per_unit.get(u.id, 0)}
                  for u in db.query(Unit).order_by(Unit.name).all()],
        "providers": [{"id": p.id, "name": p.name, "count": results_per_provider.get(p.id, 0)}
                      for p in db.query(Provider).order_by(Provider.name).all()],
    }


# ---------- rule-based suggestions ----------

_TITLES = {"dr", "md", "do", "np", "pa", "c", "fnp", "aprn", "rn", "phd", "mph", "facp", "pac"}


def _words_key(name: str) -> str:
    """Name with punctuation, case and word order ignored: "Cholesterol, Total" == "Total Cholesterol"."""
    return " ".join(sorted(re.findall(r"[a-z0-9]+", (name or "").lower())))


def _lab_base(name: str, unit: str) -> str:
    """Lab name without a suffix the importer adds to keep tests apart: " (g/dL)" or " (2)"."""
    base = (name or "").strip()
    match = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", base)
    if match and (match.group(2).strip().isdigit() or normalize_unit(match.group(2)) == normalize_unit(unit)):
        base = match.group(1)
    return _words_key(base)


def _person_key(name: str) -> str:
    """Provider name without titles, credentials or initials: "Dustin A. Fontenot, PA" == "Dustin Fontenot"."""
    words = [w for w in re.findall(r"[a-z]+", (name or "").lower()) if w not in _TITLES and len(w) > 1]
    return " ".join(sorted(words))


def _keep(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The item to keep by default: the most used, then the shortest (cleanest) name."""
    return sorted(items, key=lambda i: (-i["count"], len(i["name"]), i["id"]))[0]


def _group(kind: str, items: List[Dict[str, Any]], reason: str, source: str,
           keep_id: Optional[int] = None, name: Optional[str] = None) -> Dict[str, Any]:
    keep = next((i for i in items if i["id"] == keep_id), None) or _keep(items)
    return {
        "key": f"{kind}:" + ",".join(str(i["id"]) for i in sorted(items, key=lambda i: i["id"])),
        "kind": kind,
        "items": items,
        "keep_id": keep["id"],
        "name": (name or "").strip() or keep["name"],
        "reason": reason,
        "source": source,
    }


def rule_suggestions(inv: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    groups = []

    # Lab tests: same name (ignoring an added unit or number suffix) and the same unit
    by_name = defaultdict(list)
    for lab in inv["labs"]:
        by_name[_lab_base(lab["name"], lab["unit"])].append(lab)
    for labs in by_name.values():
        if len(labs) < 2:
            continue
        by_unit = defaultdict(list)
        for lab in labs:
            by_unit[normalize_unit(lab["unit"])].append(lab)
        unitless = by_unit.pop("", [])
        if len(by_unit) == 1:
            # A test saved without a unit belongs with the only unit this test is measured in
            by_unit[next(iter(by_unit))].extend(unitless)
        elif not by_unit:
            by_unit[""] = unitless
        for same in by_unit.values():
            if len(same) > 1:
                units = sorted({lab["unit"] for lab in same if lab["unit"]})
                # Suggest the plain name ("Albumin" rather than "Albumin (g/dL)")
                plain = min((lab["name"] for lab in same), key=len)
                groups.append(_group("labs", same, "Same test name" + (
                    f", same unit ({' = '.join(units)})" if units else ""), "rule", name=plain))

    for kind, key, reason in (("panels", _words_key, "Same panel name"),
                              ("units", normalize_unit, "Same unit, spelled differently"),
                              ("providers", _person_key, "Same name, ignoring titles and initials")):
        buckets = defaultdict(list)
        for item in inv[kind]:
            k = key(item["name"])
            if k:
                buckets[k].append(item)
        groups += [_group(kind, items, reason, "rule") for items in buckets.values() if len(items) > 1]
    return groups


# ---------- AI suggestions ----------

class AIGroup(BaseModel):
    kind: Literal["labs", "panels", "units", "providers"]
    ids: List[int] = Field(description="IDs of the items that are the same thing (two or more, all of this kind)")
    keep_id: int = Field(description="ID of the item to keep; prefer the clearest, most standard name")
    name: Optional[str] = Field(None, description="A better name for the kept item, only if none of the names is good; else null")
    reason: str = Field(description="One short sentence saying why these are the same")


class AISuggestions(BaseModel):
    groups: List[AIGroup]


AI_SYSTEM = """You help tidy a personal lab results app. You get the lab tests, panels, units and \
providers saved in it, each with an ID. Find items that are the same thing saved more than once, so \
the user can merge them. The user reviews every suggestion before anything changes.

Rules:
- Lab tests: suggest merging only when they are the same measurement in the same (or an equivalent) \
unit, e.g. "Albumin" and "Albumin (g/dL)", "WBC" and "White Blood Cell Count", "Cholesterol, Total" \
and "Total Cholesterol". Never merge different measurements that share words (Hemoglobin vs \
Hemoglobin A1c, Free vs Total Testosterone, LDL vs HDL, a percentage vs an absolute count), and never \
merge tests in units that are not equivalent (mg/dL vs mmol/L).
- Panels: the same panel under different names (e.g. "Lipid Panel" and "Lipid Profile").
- Units: the same unit spelled differently (e.g. "mg/dl" and "mg/dL", "K/uL" and "x10E3/uL").
- Providers: the same person (e.g. "Dustin Fontenot" and "Dustin A. Fontenot, PA").
- Only suggest merges you are confident about. Return an empty list if there are none."""


def _listing(inv: Dict[str, List[Dict[str, Any]]]) -> str:
    lines = ["Lab tests (id | name | unit | panel):"]
    lines += [f"{l['id']} | {l['name']} | {l['unit'] or '-'} | {l['panel'] or '-'}" for l in inv["labs"]]
    for kind, title in (("panels", "Panels"), ("units", "Units"), ("providers", "Providers")):
        lines += ["", f"{title} (id | name):"] + [f"{i['id']} | {i['name']}" for i in inv[kind]]
    return "\n".join(lines)


async def ai_suggestions(inv: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Merge suggestions from Claude, checked against what exists. Raises AIParseError on failure."""
    answer = await ai_parser.call_tool(
        system=AI_SYSTEM,
        messages=[{"role": "user", "content": _listing(inv) + "\n\nRecord the duplicates with the record_duplicates tool."}],
        tool_name="record_duplicates",
        tool_description="Record groups of items that are the same thing and could be merged.",
        schema=AISuggestions,
        max_tokens=8000,
    )
    by_id = {kind: {i["id"]: i for i in inv[kind]} for kind in KINDS}
    groups = []
    for g in answer.groups:
        items = [by_id[g.kind][i] for i in dict.fromkeys(g.ids) if i in by_id[g.kind]]
        if len(items) < 2:
            continue
        if g.kind == "labs" and not all(units_match(a["unit"], b["unit"]) for a in items for b in items):
            continue  # different units can't be merged, whatever the model says
        groups.append(_group(g.kind, items, g.reason.strip() or "Suggested by AI", "ai", g.keep_id, g.name))
    return groups


def combine(rule_groups: List[Dict[str, Any]], ai_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rule and AI suggestions together; a group both found is listed once."""
    groups = {g["key"]: g for g in rule_groups}
    for g in ai_groups:
        if g["key"] in groups:
            groups[g["key"]]["source"] = "both"
            groups[g["key"]]["ai_reason"] = g["reason"]
        else:
            groups[g["key"]] = g
    order = {kind: i for i, kind in enumerate(KINDS)}
    return sorted(groups.values(), key=lambda g: (order[g["kind"]], g["name"].lower()))


# ---------- merging ----------

_MODELS = {"labs": Lab, "panels": Panel, "units": Unit, "providers": Provider}


def merge(kind: str, keep_id: int, merge_ids: List[int], name: Optional[str], db: Session) -> Dict[str, Any]:
    """Move everything that uses the merged items onto the kept one, then delete the merged items.

    Doesn't commit; the caller commits all merges together.
    """
    if kind not in _MODELS:
        raise MergeError(f"Unknown kind: {kind}")
    model = _MODELS[kind]
    keep = db.get(model, keep_id)
    if keep is None:
        raise MergeError(f"The {KIND_LABEL[kind]} to keep no longer exists.")
    others = [db.get(model, i) for i in dict.fromkeys(merge_ids) if i != keep_id]
    others = [o for o in others if o is not None]
    if not others:
        raise MergeError(f"Choose at least one other {KIND_LABEL[kind]} to merge into {keep.name}.")

    moved = 0
    if kind == "labs":
        keep_unit = keep.unit.name if keep.unit else None
        for other in others:
            other_unit = other.unit.name if other.unit else None
            if not units_match(keep_unit, other_unit):
                raise MergeError(f"{other.name} is in {other_unit} and {keep.name} is in {keep_unit}; "
                                 "results in different units can't be merged.")
        for other in others:
            moved += db.query(LabResult).filter(LabResult.lab_id == other.id).update({LabResult.lab_id: keep.id})
            # Keep details the kept test doesn't have yet
            if keep.unit_id is None and other.unit_id is not None:
                keep.unit_id = other.unit_id
            if keep.ref_low is None and keep.ref_high is None and keep.ref_value is None:
                keep.ref_low, keep.ref_high, keep.ref_value, keep.ref_type = other.ref_low, other.ref_high, other.ref_value, other.ref_type
            if not keep.description and other.description:
                keep.description = other.description
    elif kind == "panels":
        for other in others:
            moved += db.query(Lab).filter(Lab.panel_id == other.id).update({Lab.panel_id: keep.id})
    elif kind == "units":
        for other in others:
            moved += db.query(Lab).filter(Lab.unit_id == other.id).update({Lab.unit_id: keep.id})
    elif kind == "providers":
        for other in others:
            moved += db.query(LabResult).filter(LabResult.provider_id == other.id).update({LabResult.provider_id: keep.id})
            db.query(PDFImportLog).filter(PDFImportLog.provider_id == other.id).update({PDFImportLog.provider_id: keep.id})
            db.query(ImportTemplate).filter(ImportTemplate.default_provider_id == other.id).update(
                {ImportTemplate.default_provider_id: keep.id})

    merged_names = [o.name for o in others]
    for other in others:
        db.delete(other)
    db.flush()

    new_name = (name or "").strip()
    if new_name and new_name != keep.name:
        limit = 50 if kind == "units" else 255
        if len(new_name) > limit:
            raise MergeError(f"The name {new_name[:40]}… is too long.")
        clash = db.query(model).filter(func.lower(model.name) == new_name.lower(), model.id != keep.id).first()
        if clash:
            raise MergeError(f"Another {KIND_LABEL[kind]} is already called {clash.name}.")
        keep.name = new_name
        db.flush()

    return {"kind": kind, "kept": keep.name, "keep_id": keep.id, "merged": merged_names, "moved": moved}
