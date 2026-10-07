"""Build information stamped into the Docker image, and a check for newer published builds."""

import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Set by the image build (see .github/workflows/build.yml); unset for local runs.
BUILD_VERSION = os.getenv("MYLABVAULT_BUILD") or "dev"
BUILD_COMMIT = os.getenv("MYLABVAULT_COMMIT") or None
BUILD_DATE = os.getenv("MYLABVAULT_BUILD_DATE") or None
REPOSITORY = os.getenv("MYLABVAULT_REPOSITORY") or "zaydons/MyLabVault"

CHECK_INTERVAL_SECONDS = 6 * 60 * 60
FAILED_CHECK_RETRY_SECONDS = 30 * 60
_cache: Dict[str, Any] = {"checked": 0.0, "latest": None}


def get_build_info() -> Dict[str, Optional[str]]:
    return {
        "build": BUILD_VERSION,
        "commit": BUILD_COMMIT[:7] if BUILD_COMMIT else None,
        "build_date": BUILD_DATE,
    }


def update_check_enabled() -> bool:
    """Only published images know their commit; MYLABVAULT_UPDATE_CHECK=false turns the check off."""
    return bool(BUILD_COMMIT) and os.getenv("MYLABVAULT_UPDATE_CHECK", "true").lower() not in ("false", "0", "no", "off")


def _fetch_latest_build() -> Optional[Dict[str, Any]]:
    """Latest successful image build on main, from the public GitHub Actions API."""
    url = (f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/build.yml/runs"
           "?branch=main&status=success&per_page=1")
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "MyLabVault-update-check",
    })
    with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - fixed https URL
        runs = json.load(response).get("workflow_runs") or []
    if not runs:
        return None
    run = runs[0]
    return {"commit": run.get("head_sha"), "build_date": run.get("created_at")}


def check_for_update() -> Dict[str, Any]:
    """Compare this build with the newest published one (cached for a few hours)."""
    if not update_check_enabled():
        return {"enabled": False, **get_build_info()}

    now = time.time()
    age = now - _cache["checked"]
    retry_after = CHECK_INTERVAL_SECONDS if _cache["latest"] else FAILED_CHECK_RETRY_SECONDS
    if age > retry_after:
        try:
            _cache["latest"] = _fetch_latest_build()
        except Exception as e:  # offline, rate limited, GitHub down
            logger.warning(f"Update check failed: {type(e).__name__}")
        _cache["checked"] = now

    latest = _cache["latest"]
    return {
        "enabled": True,
        **get_build_info(),
        "latest_commit": latest["commit"][:7] if latest and latest.get("commit") else None,
        "latest_build_date": latest.get("build_date") if latest else None,
        "update_available": bool(latest and latest.get("commit") and latest["commit"] != BUILD_COMMIT),
    }
