"""Logging for MyLabVault: one readable line per event, written to the container log.

    INFO  request   POST /api/pdf/confirm 200 412ms req=3f9a1c2e
    INFO  audit     import.confirmed import_id=12 saved=37 dates=3 req=3f9a1c2e
    ERROR app       Unhandled error in POST /api/pdf/upload (error ID 3f9a1c2e)
                    Traceback ...

- Every request gets an ID, returned in the X-Request-ID header and shown with server errors,
  so a message on screen can be found in the log.
- Audit lines record every change (what kind, which IDs, how many), never health values, names
  or other personal details.
- MYLABVAULT_LOG_LEVEL (DEBUG, INFO, WARNING, ERROR; default INFO) sets the detail, and
  MYLABVAULT_LOG_FORMAT=json writes one JSON object per line for log collectors.
"""

import contextvars
import json
import logging
import os
import sys
import time
import traceback
import uuid
from typing import Any, Optional

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
# Per-request state shared with the route (routes run in a copied context, so they mutate this
# dict instead of setting variables): "audited" means the route wrote its own audit line
request_state: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("request_state", default=None)

audit_log = logging.getLogger("mylabvault.audit")
request_log = logging.getLogger("mylabvault.request")

# Short names for the logger column
_SHORT = {"mylabvault.audit": "audit", "mylabvault.request": "request"}


def _short_name(name: str) -> str:
    if name in _SHORT:
        return _SHORT[name]
    if name.startswith("api."):
        return name.rsplit(".", 1)[-1]
    return name.split(".")[0]


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        rid = getattr(record, "request_id", "-")
        first, sep, rest = message.partition("\n")
        if rid != "-" and "req=" not in first:
            first += f" req={rid}"
        line = f"{record.levelname:<7} {_short_name(record.name):<10} {first}{sep}{rest}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": _short_name(record.name),
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        data.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


def configure() -> None:
    """Send all logging (the app's, uvicorn's, alembic's) through one handler and format."""
    level = os.getenv("MYLABVAULT_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(_JsonFormatter() if os.getenv("MYLABVAULT_LOG_FORMAT", "").lower() == "json" else _TextFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level if level in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO")
    for name in ("uvicorn", "uvicorn.error", "alembic"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True
        logger.disabled = False
    # Requests are logged by the app's middleware with an ID and timing, so uvicorn's own access
    # lines would only repeat them (and include query strings, which can hold search terms)
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "botocore", "urllib3", "multipart", "pdfminer", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_FRAMEWORK = ("/starlette/", "/fastapi/", "/anyio/", "/uvicorn/", "/asyncio/", "/concurrent/", "/threading.py", "/contextlib.py")


_MIDDLEWARE = ("log_requests", "welcome_redirect_middleware")


def short_traceback(exc: BaseException) -> str:
    """The exception with only the app's own frames: framework and middleware frames hide the cause.

    Follows "raise ... from" causes so a 500 raised in an except block shows what failed.
    """
    lines = []
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, BaseExceptionGroup) and exc.exceptions:  # noqa: F821 (Python 3.11+)
            exc = exc.exceptions[0]
            continue
        frames = [f for f in traceback.extract_tb(exc.__traceback__)
                  if not any(p in f.filename for p in _FRAMEWORK) and f.name not in _MIDDLEWARE]
        block = [f'  File "{f.filename}", line {f.lineno}, in {f.name}' + (f"\n    {f.line}" if f.line else "") for f in frames]
        lines.insert(0, "\n".join(block + [f"{type(exc).__name__}: {exc}"]))
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return "\n  caused the error below:\n".join(lines)


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


def audit(event: str, **fields: Any) -> None:
    """Record a change, e.g. audit("import.confirmed", import_id=12, saved=37).

    Fields should be IDs, counts and kinds; never health values, names or file contents.
    """
    state = request_state.get()
    if state is not None:
        state["audited"] = True
    parts = " ".join(f"{k}={_value(v)}" for k, v in fields.items() if v is not None)
    audit_log.info(f"{event} {parts}".rstrip(), extra={"fields": {"event": event, **fields}})


def _value(v: Any) -> str:
    if isinstance(v, (list, tuple, set)):
        return ",".join(str(i) for i in v) or "-"
    text = str(v)
    return f'"{text}"' if " " in text else text
