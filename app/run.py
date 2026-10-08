#!/usr/bin/env python3
"""MyLabVault Startup Script"""

import os
import uvicorn

def main():
    """Main entry point for the MyLabVault application."""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))

    # Docker containers need to bind to all interfaces
    if os.getenv("DOCKER_ENV") == "true":
        host = "0.0.0.0"  # nosec B104

    # Docker environments disable reload to avoid file watcher issues
    reload = os.getenv("DOCKER_ENV") != "true"

    # The app configures logging itself (api/logging_setup.py); log_config=None keeps uvicorn
    # from installing its own handlers and format
    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_config=None,
    )

if __name__ == "__main__":
    main()
