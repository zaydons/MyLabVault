"""MyLabVault API"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles


from . import __version__, __author__, __description__
from . import build_info
# Import database components with error handling
try:
    from .database import engine, init_essential_data
    from .models import Base
    DB_IMPORTS_SUCCESS = True
except Exception as e:
    print(f"❌ Database imports failed: {e}")
    DB_IMPORTS_SUCCESS = False

from .routers import providers, panels, labs, results, pdf_import, units, settings, pages, patients, vitals

def initialize_database():
    """Create tables, run migrations and seed essential data."""
    if DB_IMPORTS_SUCCESS:
        try:
            print("🔧 Initializing database...")
            Base.metadata.create_all(bind=engine)
            init_essential_data()
            print("✅ Database initialized successfully")
        except Exception as e:
            print(f"❌ Database initialization failed: {e}")
            # Ensure data directory and database file exist
            
            data_dir = Path(__file__).parent.parent / "data"
            data_dir.mkdir(exist_ok=True)
            
            db_file = data_dir / "mylabvault.db"
            if not db_file.exists():
                print("🔧 Creating database file...")
                db_file.touch()
            try:
                Base.metadata.create_all(bind=engine)
                init_essential_data()
                print("✅ Database initialized successfully on retry")
            except Exception as retry_error:
                print(f"❌ Database initialization failed on retry: {retry_error}")
    else:
        print("⚠️  Database imports failed, running without database functionality")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database before the application starts serving."""
    initialize_database()
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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add validation error handler for better debugging
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
	"""Global exception handler for Pydantic validation errors."""
	errors = jsonable_encoder(exc.errors())  # custom validators put the exception object in the error context
	logger.error(f"Validation error for {request.method} {request.url.path}: {errors}")

	return JSONResponse(
		status_code=422,
		content={"detail": errors, "error_type": "validation_error"}
	)

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
app.include_router(pdf_import.router, prefix="/api/pdf", tags=["pdf-import"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])

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
