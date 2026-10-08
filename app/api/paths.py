"""Where the app keeps its files. MYLABVAULT_DATA_DIR overrides the container's /app/data (used by tests)."""

import os
from pathlib import Path

DATA_DIR = Path(os.getenv("MYLABVAULT_DATA_DIR", "/app/data"))
UPLOADS_DIR = DATA_DIR / "uploads" / "pdfs"
