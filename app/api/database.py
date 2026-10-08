"""Database configuration for MyLabVault."""

import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import logging

logger = logging.getLogger(__name__)

# Auto-configure database URL with fallback to local SQLite
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DATA_DIR = Path(__file__).parent.parent / "data"
    DATA_DIR.mkdir(exist_ok=True)
    DATABASE_URL = f"sqlite:///{DATA_DIR}/mylabvault.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency to provide database sessions with automatic cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """Run Alembic migrations if available."""
    try:
        from alembic.config import Config
        from alembic import command
        
        # Get the app directory path
        app_dir = Path(__file__).parent.parent
        alembic_cfg_path = app_dir / "alembic.ini"
        
        if not alembic_cfg_path.exists():
            logger.warning("No alembic.ini found, skipping migrations")
            return False
        
        logger.info("Running database migrations")
        
        # Create Alembic configuration
        alembic_cfg = Config(str(alembic_cfg_path))
        alembic_cfg.set_main_option("script_location", str(app_dir / "alembic"))
        alembic_cfg.attributes["app_logging"] = True  # keep the app's logging configuration
        
        # Ensure alembic_version table exists for existing databases
        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='alembic_version'
                """))
                
                if not result.fetchone():
                    # Check if database has existing data
                    result = conn.execute(text("""
                        SELECT name FROM sqlite_master 
                        WHERE type='table' AND name='patients'
                    """)).fetchone()
                    
                    if result:
                        # Existing database, create version table and start from migration 001
                        conn.execute(text("""
                            CREATE TABLE alembic_version (
                                version_num VARCHAR(32) NOT NULL PRIMARY KEY
                            )
                        """))
                        # Always start existing databases at migration 001
                        # Alembic migrations are designed to be safe for existing schemas
                        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('001')"))
                        conn.commit()
                        logger.info("Marked existing database to start at migration 001")
        except Exception as e:
            logger.warning(f"Could not check/create alembic_version: {e}")
        
        # Run migrations
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations completed")
        return True
        
    except ImportError:
        logger.warning("Alembic not available, skipping migrations")
        return False
    except Exception as e:
        logger.exception("Database migration failed")
        return False


def init_essential_data():
    """Initialize database tables and create default patient if none exists."""
    from .models import Base, Patient

    # Run migrations first if available
    run_migrations()

    # Fallback to creating tables if migrations didn't run
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.query(Patient).count() > 0:
            return

        patient = Patient(id=1, name="Default Patient")
        db.add(patient)
        db.commit()
        logger.info("Created the default patient")

    except Exception as e:
        db.rollback()
        logger.exception("Creating the default patient failed")
    finally:
        db.close()
