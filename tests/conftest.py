"""Shared fixtures: a throwaway data folder and database, reset before every test.

Environment is set before the app is imported, so the app uses the temporary paths.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(tempfile.mkdtemp(prefix="mylabvault-tests-"))
os.environ["MYLABVAULT_DATA_DIR"] = str(DATA)
os.environ["DATABASE_URL"] = f"sqlite:///{DATA}/test.db"
os.environ["MYLABVAULT_UPDATE_CHECK"] = "false"
for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "MYLABVAULT_AI_MODEL"):
    os.environ.pop(var, None)
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app, initialize_database  # noqa: E402
from api import paths  # noqa: E402
from api.database import engine  # noqa: E402
from api.models import Base  # noqa: E402
from api.routers import setup as setup_router  # noqa: E402
from api.utils.cache import api_cache  # noqa: E402

initialize_database()


def reset_data():
    """Empty database, uploads folder and in-memory state, as on a fresh install."""
    Base.metadata.drop_all(bind=engine)
    initialize_database()
    setup_router.reset_setup_state()
    api_cache.clear()
    shutil.rmtree(paths.UPLOADS_DIR, ignore_errors=True)
    paths.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def fresh_client():
    """A client on an empty install (the welcome screen hasn't been completed)."""
    reset_data()
    return TestClient(app)


@pytest.fixture
def client(fresh_client):
    """A client after first-run setup, with one provider (id 1)."""
    assert fresh_client.post("/api/setup/", json={"skip": True}).status_code == 200
    assert fresh_client.post("/api/providers/", json={"name": "Dr A"}).status_code == 200
    return fresh_client


@pytest.fixture
def ai(monkeypatch):
    """AI parsing turned on, with Bedrock replaced by a canned reply.

    Set `ai.reply` to the tool input the model should return; `ai.requests` collects the
    request bodies sent.
    """
    from ai_mock import BedrockMock
    mock = BedrockMock()
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    mock.install(monkeypatch)
    return mock
