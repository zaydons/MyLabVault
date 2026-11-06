"""AI-assisted lab report parsing using Claude in Amazon Bedrock.

Optional: only active when AWS credentials and a region are configured. The PDF
is sent to Claude through Bedrock, which returns structured results that are
converted into the same test format PDFParser produces, so the existing
preview/confirm flow is reused.
"""

import base64
import logging
import os
import time
from datetime import date
from typing import Any, Dict, List, Literal, Optional

import anthropic
from dateutil import parser as date_parser
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# Claude Haiku 4.5 through the US cross-region inference profile (Haiku 4.5 has no on-demand
# throughput for its plain model ID). Outside the US, use the eu./apac./global. profile instead.
DEFAULT_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# Bedrock request payload limit is 20 MB and base64 adds a third, so cap the raw PDF size.
MAX_PDF_BYTES = 14 * 1024 * 1024
TOOL_NAME = "record_lab_report"
# One retry when the model does not return a valid tool call.
MAX_ATTEMPTS = 2


class AIParseError(Exception):
    """Raised when AI parsing is unavailable or does not produce usable results."""


class ExtractedTest(BaseModel):
    """One test result as printed on the report."""
    name: str = Field(description="Test name exactly as printed on the report")
    matched_lab: Optional[str] = Field(
        None,
        description="If this is the same test as one in the provided list of existing test names, "
                    "that existing name copied exactly; otherwise null",
    )
    panel: Optional[str] = Field(None, description="Panel or section heading the test is listed under, if any")
    value: Optional[float] = Field(None, description="Numeric result, without comparison signs or units")
    value_text: Optional[str] = Field(
        None,
        description="Result as printed when it is not a plain number (e.g. 'Negative', '<0.5', 'See note')",
    )
    unit: Optional[str] = Field(None, description="Unit as printed, e.g. 'mg/dL'")
    ref_low: Optional[float] = Field(None, description="Lower bound of the reference range, if any")
    ref_high: Optional[float] = Field(None, description="Upper bound of the reference range, if any")
    ref_text: Optional[str] = Field(None, description="Reference range exactly as printed, e.g. '100-199', '>59', '<5.7'")
    flag: Optional[str] = Field(None, description="Abnormal flag as printed, e.g. 'H', 'L', 'High', 'Critical'")
    comment: Optional[str] = Field(
        None,
        description="Comment or footnote the lab printed for this specific test (e.g. calculation method, "
                    "specimen issues), copied as printed; null if none",
    )
    collection_date: Optional[str] = Field(
        None,
        description="Collection date of this result as YYYY-MM-DD when the report lists results from more "
                    "than one collection date (a health summary or results history); null when every "
                    "result on the report shares one collection date",
    )


VitalKind = Literal["weight", "height", "blood_pressure", "heart_rate", "temperature", "oxygen_saturation",
                    "respiratory_rate", "blood_glucose"]


class ExtractedVital(BaseModel):
    """One vital sign measurement, e.g. from a visit summary's Vitals section."""
    kind: VitalKind = Field(description="What was measured")
    value: float = Field(description="The measurement; for blood pressure, the systolic (top) number")
    value2: Optional[float] = Field(None, description="Blood pressure only: the diastolic (bottom) number")
    unit: Optional[str] = Field(
        None,
        description="Unit, as one of: lb, kg, in, cm, mmHg, bpm, °F, °C, %, breaths/min, mg/dL, mmol/L. Convert "
                    "pounds and ounces to decimal pounds (286 lb 6.4 oz is 286.4 lb), feet and inches to inches "
                    "(5' 8\" is 68 in) and grams to kg. Null if no unit is printed",
    )
    date: Optional[str] = Field(
        None,
        description="Date measured as YYYY-MM-DD when the document lists vitals from several dates; null when "
                    "they were measured at the visit the document is about",
    )


class ExtractedMedication(BaseModel):
    """One medication or supplement listed on a visit or health summary."""
    name: str = Field(description="Medication name as printed, e.g. 'Sertraline HCL'")
    matched_medication: Optional[str] = Field(
        None,
        description="If this is the same medication as one in the provided list of the patient's current "
                    "medications (same drug, even if written differently, e.g. a salt name added), that name "
                    "copied exactly; otherwise null",
    )
    dose: Optional[str] = Field(None, description="Strength per dose as printed, e.g. '25 mg'")
    frequency: Optional[str] = Field(None, description="How often, in plain words, e.g. 'once daily', 'twice daily', 'weekly', 'as needed'")
    route: Optional[str] = Field(None, description="How it's taken, e.g. 'by mouth', 'injection', 'topical'")
    action: Literal["new", "continue", "stop", "listed"] = Field(
        description="What the document says happened at this visit: 'new' (started or prescribed), 'continue' "
                    "(keep taking), 'stop' (discontinued), or 'listed' when it's only listed without saying",
    )
    prescriber: Optional[str] = Field(None, description="Prescribing provider's name, if printed")
    reason: Optional[str] = Field(None, description="What it's for, if printed (e.g. a diagnosis name), without codes")


class ExtractedReport(BaseModel):
    """Structured content of one lab report, visit summary or health summary."""
    collection_date: Optional[str] = Field(
        None,
        description="Date the specimen was COLLECTED, as YYYY-MM-DD. Not the date of birth, "
                    "received, reported or printed date. Null if not shown.",
    )
    ordering_provider: Optional[str] = Field(None, description="Ordering physician or provider name")
    lab_company: Optional[str] = Field(None, description="Laboratory company, e.g. 'Labcorp', 'Quest Diagnostics'")
    fasting: Optional[bool] = Field(
        None,
        description="Whether the report states the patient was fasting (true) or not fasting (false); "
                    "null if the report does not say",
    )
    visit_date: Optional[str] = Field(
        None,
        description="For a visit summary (after-visit or clinical visit summary): the date of the visit, as "
                    "YYYY-MM-DD. Null for other documents",
    )
    tests: List[ExtractedTest] = Field(default_factory=list, description="Lab test results; empty if there are none")
    vitals: List[ExtractedVital] = Field(default_factory=list, description="Vital signs; empty if there are none")
    medications: List[ExtractedMedication] = Field(
        default_factory=list, description="Medications and supplements listed; empty if there are none")


SYSTEM_PROMPT = """You extract lab test results, vital signs and medications from medical documents (lab \
reports, visit summaries, health summaries) into structured data for a personal health records app. The person uploading the report is the patient or their caregiver.

Extract every test result in the report, including qualitative ones. Report values exactly as \
printed; do not convert units or infer missing values. Skip calculated commentary, interpretive \
notes, and footnotes that are not results.

For collection_date, use the specimen collection date only. Reports usually also show a date of \
birth and received/reported dates; never use those. Some documents, such as patient health \
summaries, list results from several collection dates; then give each test its own \
collection_date and include every result from every date.

For matched_lab, compare each test with the list of existing test names provided. Fill it in only \
when it is clearly the same measurement under a different spelling or wording (for example \
"Cholesterol, Total" and "Total Cholesterol"). Different tests that share a word, such as \
"Hemoglobin" and "Hemoglobin A1c", are not matches.

Documents can also be visit summaries or health summaries with vital signs and medications, and \
may have no lab results at all. Record vital signs (blood pressure, temperature, pulse, respiratory \
rate, weight, height, oxygen saturation, blood glucose) in vitals; skip derived values such as BMI, \
ideal body weight, weight in a second unit or weight change. Record every medication in medications, \
with the action the document gives it at this visit: medications under headings such as "New" or \
"Started" are new, "Continue" or "Current" are continue, "Discontinue" or "Stopped" are stop. For \
matched_medication, compare with the patient's current medications provided."""


def _inline_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Return the schema with local $defs references inlined (keeps the tool schema self-contained)."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


TOOL_INPUT_SCHEMA = _inline_refs(ExtractedReport.model_json_schema())


def is_enabled() -> bool:
    """AI parsing is enabled only when AWS credentials and a region are configured."""
    return all(os.getenv(var) for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"))


def get_model() -> str:
    return os.getenv("MYLABVAULT_AI_MODEL", DEFAULT_MODEL)


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    """Return an ISO date, or None if missing, unparseable or implausible."""
    if not raw:
        return None
    try:
        parsed = date_parser.parse(raw).date()
    except (ValueError, OverflowError):
        return None
    if parsed > date.today() or parsed.year < 1900:
        return None
    return parsed.isoformat()


def _format_number(value: float) -> str:
    return f"{value:g}"


def _to_parser_test(test: ExtractedTest, known_names: Dict[str, str]) -> Dict[str, Any]:
    """Convert an extracted test into the dict format produced by PDFParser."""
    # Only trust a match that names an existing test; otherwise keep the printed name.
    matched = known_names.get(test.matched_lab.strip().lower()) if test.matched_lab else None
    name = matched or test.name.strip()

    is_numeric = test.value is not None and not test.value_text
    result = test.value_text or (_format_number(test.value) if test.value is not None else None)

    ref_text = test.ref_text
    if not ref_text and test.ref_low is not None and test.ref_high is not None:
        ref_text = f"{_format_number(test.ref_low)}-{_format_number(test.ref_high)}"
    ref_low, ref_high = test.ref_low, test.ref_high
    stripped = (ref_text or "").strip()
    # The confirm step reads ">x" as {low: x} and "<x" as {high: x}.
    if stripped.startswith(">") and ref_low is None:
        ref_low = ref_high
        ref_high = None
    elif stripped.startswith("<") and ref_high is None:
        ref_high = ref_low
        ref_low = None

    return {
        "name": name,
        "original_name": test.name.strip(),
        "panel_name": test.panel,
        "result": result,
        "result_text": None if is_numeric else result,
        "numeric_value": test.value if is_numeric else None,
        "is_numeric": is_numeric,
        "is_qualitative": not is_numeric,
        "unit": (test.unit or "").strip(),
        "reference_range": {"low": ref_low, "high": ref_high, "text": ref_text or ""},
        "flag": test.flag,
        "lab_comment": test.comment,
        "date_collected": _normalize_date(test.collection_date),
    }


# Units the AI may print for each vital type, mapped to the app's unit and a factor to it
_VITAL_UNITS = {
    "weight": {"lb": ("lb", 1), "lbs": ("lb", 1), "pound": ("lb", 1), "pounds": ("lb", 1), "kg": ("kg", 1), "g": ("kg", 0.001)},
    "height": {"in": ("in", 1), "inch": ("in", 1), "inches": ("in", 1), '"': ("in", 1), "cm": ("cm", 1), "m": ("cm", 100)},
    "blood_pressure": {"mmhg": ("mmHg", 1), "mm[hg]": ("mmHg", 1), "mm hg": ("mmHg", 1)},
    "heart_rate": {"bpm": ("bpm", 1), "/min": ("bpm", 1), "beats/min": ("bpm", 1)},
    "temperature": {"°f": ("°F", 1), "f": ("°F", 1), "degf": ("°F", 1), "[degf]": ("°F", 1),
                    "°c": ("°C", 1), "c": ("°C", 1), "degc": ("°C", 1), "cel": ("°C", 1)},
    "oxygen_saturation": {"%": ("%", 1)},
    "respiratory_rate": {"breaths/min": ("breaths/min", 1), "/min": ("breaths/min", 1), "br/min": ("breaths/min", 1)},
    "blood_glucose": {"mg/dl": ("mg/dL", 1), "mmol/l": ("mmol/L", 1)},
}


def _to_vital(vital: ExtractedVital) -> Optional[Dict[str, Any]]:
    """A vital in the app's units, or None when it can't be saved (unknown unit, missing number)."""
    printed = (vital.unit or "").strip().lower()
    if printed:
        unit, factor = _VITAL_UNITS[vital.kind].get(printed, (None, None))
        if unit is None:
            return None
    else:
        unit, factor = next(iter(_VITAL_UNITS[vital.kind].values()))
    value = vital.value * factor
    value2 = vital.value2 * factor if vital.value2 is not None else None
    if value <= 0 or (vital.kind == "blood_pressure" and not value2):
        return None
    return {"vital_type": vital.kind, "value": round(value, 3), "value2": round(value2, 3) if value2 else None,
            "unit": unit, "date": _normalize_date(vital.date)}


def _to_medication(med: ExtractedMedication, known: Dict[str, str]) -> Dict[str, Any]:
    clean = lambda text: (text or "").strip() or None  # noqa: E731
    return {
        "name": med.name.strip(),
        # Only trust a match that names one of the patient's current medications
        "matched_medication": known.get(med.matched_medication.strip().lower()) if med.matched_medication else None,
        "dose": clean(med.dose), "frequency": clean(med.frequency), "route": clean(med.route),
        "action": med.action, "prescriber": clean(med.prescriber), "reason": clean(med.reason),
    }


async def call_tool(system: str, messages: List[Dict[str, Any]], tool_name: str, tool_description: str,
                    schema, input_schema: Optional[Dict[str, Any]] = None, max_tokens: int = 16000):
    """Ask Claude (through Bedrock) to answer by calling one tool, and return its input validated as `schema`.

    Retries once when the model doesn't return a valid tool call. Raises AIParseError with a
    message suitable for the user on any failure.
    """
    if not is_enabled():
        raise AIParseError("AI is not enabled (AWS credentials and AWS_REGION are not set)")
    tool = {"name": tool_name, "description": tool_description,
            "input_schema": input_schema or _inline_refs(schema.model_json_schema())}
    # Bedrock runtime (InvokeModel); credentials and region come from the standard AWS environment variables.
    client = anthropic.AsyncAnthropicBedrock(timeout=180.0)
    model = get_model()
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=[tool],
                tool_choice={"type": "auto"},
                messages=messages,
            )
            usage = getattr(response, "usage", None)
            logger.info(f"AI request {tool_name}: model={model} {time.perf_counter() - started:.1f}s "
                        f"stop={response.stop_reason} input_tokens={getattr(usage, 'input_tokens', '?')} "
                        f"output_tokens={getattr(usage, 'output_tokens', '?')} attempt={attempt}")
            if response.stop_reason == "refusal":
                raise AIParseError("The AI service declined to process this request")
            if response.stop_reason == "max_tokens":
                raise AIParseError("The request is too long for the AI service")
            tool_input = next(
                (block.input for block in response.content
                 if block.type == "tool_use" and block.name == tool_name),
                None,
            )
            if tool_input is not None:
                try:
                    return schema.model_validate(tool_input)
                except ValidationError as e:
                    logger.warning(f"AI attempt {attempt}: invalid tool input ({e.error_count()} errors)")
            else:
                logger.warning(f"AI attempt {attempt}: no {tool_name} call in response")
    except AIParseError:
        raise
    except anthropic.AuthenticationError:
        raise AIParseError("AWS credentials were rejected")
    except anthropic.PermissionDeniedError:
        raise AIParseError("AWS credentials are not allowed to use this model (check Bedrock model access and the IAM policy)")
    except anthropic.NotFoundError:
        raise AIParseError(f"Model {model} is not available in this AWS region")
    except anthropic.BadRequestError as e:
        # Bedrock reports an unknown model ID or a missing inference profile as a 400 ValidationException.
        if "model identifier" in str(e) or "inference profile" in str(e):
            raise AIParseError(f"Model {model} is not a valid Bedrock model or inference profile ID for this region")
        logger.error(f"AI request failed: HTTP 400 (request {e.request_id})")
        raise AIParseError("The AI service returned an error")
    except anthropic.RateLimitError:
        raise AIParseError("Bedrock rate limit reached, try again shortly")
    except anthropic.APIStatusError as e:
        logger.error(f"AI request failed: HTTP {e.status_code} (request {e.request_id})")
        raise AIParseError("The AI service returned an error")
    except anthropic.APIConnectionError:
        raise AIParseError("Could not reach the AI service")
    except Exception as e:
        # e.g. botocore credential/signing errors
        logger.error(f"AI request failed: {type(e).__name__}")
        raise AIParseError("AI request failed")
    finally:
        await client.close()
    raise AIParseError("The AI service returned an invalid result")


async def parse_pdf_with_ai(content: bytes, known_lab_names: List[str],
                            current_medications: Optional[List[str]] = None) -> Dict[str, Any]:
    """Parse a lab report, visit summary or health summary PDF with Claude.

    Returns a dict in the same shape as PDFParser.parse_pdf_content.
    Raises AIParseError when AI parsing is disabled or fails.
    """
    if not is_enabled():
        raise AIParseError("AI parsing is not enabled (AWS credentials and AWS_REGION are not set)")
    if not content:
        raise AIParseError("PDF content is empty")
    if len(content) > MAX_PDF_BYTES:
        raise AIParseError("PDF is too large for AI parsing")

    known_names = {name.strip().lower(): name for name in known_lab_names if name}
    names_list = "\n".join(f"- {name}" for name in sorted(known_names.values())) or "(none yet)"
    known_meds = {name.strip().lower(): name for name in (current_medications or []) if name}
    meds_list = "\n".join(f"- {name}" for name in sorted(known_meds.values())) or "(none recorded)"

    messages = [{
        "role": "user",
        "content": [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(content).decode("ascii"),
                },
            },
            {
                "type": "text",
                "text": f"Existing test names in the app:\n{names_list}\n\n"
                        f"The patient's current medications in the app:\n{meds_list}\n\n"
                        f"Extract the lab results, vital signs and medications from this document and record "
                        f"them with the {TOOL_NAME} tool.",
            },
        ],
    }]
    report = await call_tool(
        system=SYSTEM_PROMPT,
        messages=messages,
        tool_name=TOOL_NAME,
        tool_description="Record every lab result, vital sign and medication extracted from the document.",
        schema=ExtractedReport,
        input_schema=TOOL_INPUT_SCHEMA,
    )
    model = get_model()

    tests = [_to_parser_test(t, known_names) for t in report.tests if t.name and t.name.strip()]
    row_dates = sorted({t["date_collected"] for t in tests if t["date_collected"]})
    report_date = _normalize_date(report.collection_date) or _normalize_date(report.visit_date)
    if not report_date and row_dates and all(t["date_collected"] for t in tests):
        report_date = row_dates[-1]
    return {
        "date_collected": report_date,
        "physician": report.ordering_provider,
        "lab_company": report.lab_company,
        "fasting": report.fasting,
        "tests": tests,
        "vitals": [v for v in (_to_vital(v) for v in report.vitals) if v],
        "medications": [_to_medication(m, known_meds) for m in report.medications if m.name and m.name.strip()],
        "ordered_panels": [],
        "panels": [],
        "errors": [],
        "parser": "ai",
        "ai_model": model,
    }
