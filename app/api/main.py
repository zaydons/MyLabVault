"""MyLabVault API"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import logging_setup

logging_setup.configure()
logger = logging.getLogger("api.app")


from . import __version__, __author__, __description__
from . import build_info
# Import database components with error handling
try:
    from .database import engine, init_essential_data
    from .models import Base
    DB_IMPORTS_SUCCESS = True
except Exception:
    logger.exception("Database imports failed")
    DB_IMPORTS_SUCCESS = False

from .routers import providers, panels, labs, results, pdf_import, units, settings, pages, patients, vitals, medications, search, setup, cleanup

def initialize_database():
    """Create tables, run migrations and seed essential data."""
    if DB_IMPORTS_SUCCESS:
        try:
            logger.info("Initializing database")
            Base.metadata.create_all(bind=engine)
            init_essential_data()
            logger.info("Database ready")
        except Exception:
            logger.exception("Database initialization failed; retrying")
            # Ensure data directory and database file exist
            
            data_dir = Path(__file__).parent.parent / "data"
            data_dir.mkdir(exist_ok=True)
            
            db_file = data_dir / "mylabvault.db"
            if not db_file.exists():
                logger.info("Creating database file")
                db_file.touch()
            try:
                Base.metadata.create_all(bind=engine)
                init_essential_data()
                logger.info("Database ready after retry")
            except Exception:
                logger.exception("Database initialization failed on retry")
    else:
        logger.error("Database imports failed, running without database functionality")


def log_startup_summary():
    """One line with what's running and how it's configured (no secrets)."""
    from .services import ai_parser
    info = build_info.get_build_info()
    db_url = str(engine.url) if DB_IMPORTS_SUCCESS else "unavailable"
    logger.info(
        f"MyLabVault {info['build'] if info.get('build') not in (None, 'dev') else 'development build'}"
        + (f" ({info['commit'][:7]})" if info.get('commit') else "")
        + f" ready: database={db_url.replace('sqlite:///', '')}"
        + f" ai={'on model=' + ai_parser.get_model() if ai_parser.is_enabled() else 'off'}"
        + f" update_check={'on' if build_info.update_check_enabled() else 'off'}"
        + f" log_level={logging.getLevelName(logging.getLogger().level)}"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database before the application starts serving."""
    initialize_database()
    log_startup_summary()
    yield

# Initialize FastAPI application
app = FastAPI(
	title="MyLabVault API",
	description=__description__,
	version=__version__,
	docs_url="/api/docs",
	redoc_url="/api/redoc",
	lifespan=lifespan
)

# Add validation error handler for better debugging
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
	"""Global exception handler for Pydantic validation errors."""
	errors = jsonable_encoder(exc.errors())  # custom validators put the exception object in the error context
	# Field and reason only: the submitted values can be health data
	summary = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg')}" for e in errors)
	logger.warning(f"Invalid request {request.method} {_route(request)}: {summary}")

	return JSONResponse(
		status_code=422,
		content={"detail": errors, "error_type": "validation_error"}
	)

def _route(request: Request) -> str:
	"""The path with IDs shown as their names (/api/results/{result_id}), never the query string."""
	path = request.url.path
	for name, value in (request.scope.get("path_params") or {}).items():
		path = path.replace(f"/{value}", f"/{{{name}}}", 1)
	return path


@app.exception_handler(StarletteHTTPException)
async def logged_http_exception_handler(request: Request, exc: StarletteHTTPException):
	"""Errors the app reports on purpose (400 bad input, 404, ...): log why, then answer as usual."""
	if exc.status_code >= 500:
		# Routes often turn an exception into a 500; keep its traceback
		cause = exc.__cause__ or exc.__context__
		trace = f"\n{logging_setup.short_traceback(cause)}" if cause else ""
		logger.error(f"{request.method} {_route(request)} failed ({exc.status_code}): {exc.detail}{trace}")
	elif exc.status_code != 404 or request.url.path.startswith("/api/"):
		logger.warning(f"{request.method} {_route(request)} refused ({exc.status_code}): {exc.detail}")
	return await http_exception_handler(request, exc)


def _server_error(request: Request, rid: str, exc: Exception) -> JSONResponse:
	"""Unexpected errors: the traceback in the log, and an error ID the user can quote."""
	logger.error(f"Unhandled error in {request.method} {_route(request)} (error ID {rid})\n{logging_setup.short_traceback(exc)}")
	return JSONResponse(status_code=500, content={
		"detail": f"Something went wrong on the server (error ID {rid}). The details are in the app's log.",
		"error_id": rid,
	})

# Configure CORS middleware for same-origin requests
app.add_middleware(
	CORSMiddleware,
	allow_origins=["http://localhost:8000"],
	allow_credentials=True,
	allow_methods=["GET", "POST", "PUT", "DELETE"],
	allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=Path(__file__).parent.parent / "static"), name="static")

# Page routes
app.include_router(pages.router, tags=["pages"])

# API routes
app.include_router(patients.router, prefix="/api/patients", tags=["patients"])
app.include_router(providers.router, prefix="/api/providers", tags=["providers"])
app.include_router(panels.router, prefix="/api/panels", tags=["panels"])
app.include_router(labs.router, prefix="/api/labs", tags=["labs"])
app.include_router(units.router, prefix="/api/units", tags=["units"])
app.include_router(results.router, prefix="/api/results", tags=["results"])
app.include_router(vitals.router, prefix="/api/vitals", tags=["vitals"])
app.include_router(medications.router, prefix="/api/medications", tags=["medications"])
app.include_router(pdf_import.router, prefix="/api/pdf", tags=["pdf-import"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(setup.router, prefix="/api/setup", tags=["setup"])
app.include_router(cleanup.router, prefix="/api/cleanup", tags=["cleanup"])

# Until the first patient is named, page requests go to the welcome screen
app.middleware("http")(setup.welcome_redirect_middleware)

_QUIET = ("/static/", "/health", "/favicon")
_CHANGES = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def log_requests(request: Request, call_next):
	"""One line per request with its ID, result and time; an audit line for every change.

	Added last, so it runs first and also covers the welcome redirect and errors.
	"""
	rid = request.headers.get("x-request-id", "")[:32] or logging_setup.new_request_id()
	token = logging_setup.request_id.set(rid)
	state: dict = {}
	state_token = logging_setup.request_state.set(state)
	start = time.perf_counter()
	status = 500
	try:
		try:
			response = await call_next(request)
		except Exception as exc:
			response = _server_error(request, rid, exc)
		status = response.status_code
		response.headers["X-Request-ID"] = rid
		return response
	finally:
		ms = (time.perf_counter() - start) * 1000
		path = request.url.path
		quiet = path.startswith(_QUIET) or (request.method == "GET" and path in ("/api/update-check", "/api/pdf/ai-status"))
		level = logging.DEBUG if quiet and status < 400 else logging.INFO
		logging_setup.request_log.log(level, f"{request.method} {_route(request)} {status} {ms:.0f}ms")
		if request.method in _CHANGES and path.startswith("/api/") and status < 400 and not state.get("audited"):
			params = request.scope.get("path_params") or {}
			logging_setup.audit("api.change", method=request.method, route=_route(request), status=status, **params)
		logging_setup.request_state.reset(state_token)
		logging_setup.request_id.reset(token)

@app.get("/health")
async def health_check():
	"""Health check endpoint for monitoring systems."""
	return {"status": "healthy", "service": "mylabvault"}

@app.get("/version")
async def get_version():
	"""Get application version and metadata."""
	return {
		"name": "MyLabVault",
		"version": __version__,
		"author": __author__,
		"description": __description__,
		"api_version": "v1",
		**build_info.get_build_info(),
	}

@app.get("/api/update-check")
def get_update_check():
	"""Whether a newer image has been published than the one running."""
	return build_info.check_for_update()
