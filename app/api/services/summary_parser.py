"""Results tables in patient health summaries that span several collection dates.

Patient portals (for example athenahealth's "Data Portability" / Ambulatory Summary export) list
every result on record in one table, each row with its own date:

    Created Date | Observation Date | Name | Description | Value | Unit | Range | Abnormal Flag | ...

The table has no ruling lines, so columns are found from the header words' positions and each
row is read from the words that fall under them. A row starts where a date appears in the first
column and continues (wrapped text, address lines) until the next one.
"""

import io
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import pdfplumber

_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_HEADER = ("created", "observation", "name", "description", "value", "unit", "range")
# Header word -> field. Columns not listed (note, organization, ...) are read but ignored.
_FIELDS = {
    "created": "created", "observation": "observed", "name": "panel", "description": "description",
    "value": "value", "unit": "unit", "range": "range", "abnormal": "flag", "note": "note",
    "lastmodifiedby": "ignore", "organization": "ignore", "lastmodifiedtime": "ignore",
}
# Values start a little left of their header word
_SLACK = 6


def _iso(match: re.Match) -> Optional[str]:
    try:
        return datetime(int(match.group(3)), int(match.group(1)), int(match.group(2))).date().isoformat()
    except ValueError:
        return None


def _lines(words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Words grouped into lines by vertical position, top to bottom, each sorted left to right."""
    lines: List[List[Dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - word["top"]) < 2.5:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _header_columns(line: List[Dict[str, Any]]) -> Optional[List[tuple]]:
    """(start x, field) per column when this line is the results table header."""
    texts = [w["text"].lower() for w in line]
    if not all(h in texts for h in _HEADER):
        return None
    columns = []
    previous_end = 0.0
    for word in line:
        field = _FIELDS.get(word["text"].lower())
        if field is None:
            continue
        start = 0.0 if not columns else max(word["x0"] - _SLACK, previous_end + 1)
        columns.append((start, field))
        previous_end = word["x1"]
    return columns


def _column(x0: float, columns: List[tuple]) -> str:
    field = columns[0][1]
    for start, name in columns:
        if x0 >= start:
            field = name
    return field


def _split_by_column(word: Dict[str, Any], columns: List[tuple]) -> List[Dict[str, Any]]:
    """A word split where its letters cross into the next column.

    Neighbouring cells sometimes print with no gap ("TESTOSTERONE,testosterone", "K/cumm137-397"),
    which reads as one word.
    """
    parts: List[Dict[str, Any]] = []
    for char in word.get("chars") or []:
        field = _column(char["x0"], columns)
        if parts and parts[-1]["field"] == field:
            parts[-1]["text"] += char["text"]
        else:
            parts.append({"field": field, "text": char["text"], "x0": char["x0"], "top": word["top"]})
    return parts or [{"field": _column(word["x0"], columns), "text": word["text"], "x0": word["x0"], "top": word["top"]}]


def is_health_summary(text: str) -> bool:
    """True when the text has a multi-date results table this module can read."""
    # Text extraction may put each header cell on its own line ("Created\nDate\nObservation\nDate")
    flat = " ".join(text.split()).lower()
    return bool(re.search(r"created (date )?observation (date )?name description value unit range", flat))


def parse_results(content: bytes, parser) -> Optional[Dict[str, Any]]:
    """Read every row of the results table. Each test carries its own `date_collected`.

    `parser` is a PDFParser, used for its value and range helpers so rows look like its own.
    Returns None when no results table is found.
    """
    rows: List[Dict[str, List[str]]] = []
    columns = None
    done = False
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            if done:
                break
            for line in _lines(page.extract_words(return_chars=True)):
                header = _header_columns(line)
                if header:
                    columns = header
                    continue
                if columns is None:
                    continue
                first = line[0]
                starts_row = _column(first["x0"], columns) in ("created", "observed") and _DATE.search(first["text"])
                if starts_row:
                    rows.append({})
                elif first["x0"] < columns[2][0] and first["text"].lower() not in ("date", "flag", "detail"):
                    # Text at the left margin that isn't a date ends the table ("Result Notes", "Problems")
                    done = True
                    break
                if not rows:
                    continue
                for word in line:
                    for part in _split_by_column(word, columns):
                        rows[-1].setdefault(part["field"], []).append(part["text"])
    if columns is None:
        return None

    tests = []
    for cells in rows:
        tests.append(_to_test(cells, parser))
    dates = sorted({t["date_collected"] for t in tests if t.get("date_collected")})
    return {
        "date_collected": dates[-1] if dates else None,
        "physician": None,
        "tests": tests,
        "ordered_panels": [],
        "panels": [],
        "errors": [],
        "multi_date": len(dates) > 1,
    }


def _join_range(parts: List[str]) -> str:
    text = ""
    for part in parts:
        text += part if (not text or text.endswith("-") or part.startswith("-")) else " " + part
    return text


def _to_test(cells: Dict[str, List[str]], parser) -> Dict[str, Any]:
    dates = [d for d in (_iso(m) for m in _DATE.finditer(" ".join(cells.get("created", []) + cells.get("observed", [])))) if d]
    panel = " ".join(cells.get("panel", []))
    name = " ".join(cells.get("description", [])) or panel
    result = " ".join(cells.get("value", []))
    unit = " ".join(cells.get("unit", []))
    range_text = _join_range(cells.get("range", []))
    flag = " ".join(cells.get("flag", [])) or None

    numeric = parser.parse_numeric_result(result) if result else None
    qualitative = bool(result) and parser.is_qualitative_result(result)
    if qualitative:
        numeric = None
    return {
        "name": name,
        "panel_name": panel or None,
        "result": result,
        "result_text": parser.standardize_qualitative_result(result) if qualitative else None,
        "unit": unit,
        "reference_range": parser.parse_reference_range(range_text),
        "flag": flag,
        "is_numeric": numeric is not None,
        "is_qualitative": qualitative,
        "numeric_value": numeric,
        # Created date: when the specimen was taken. The observation date that follows it is
        # when the lab reported the result (it matches the row's last-modified time).
        "date_collected": dates[0] if dates else None,
    }
