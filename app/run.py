#!/usr/bin/env python3
"""MyLabVault Startup Script"""

import os
import uvicorn

def main():
    """Main entry point for the MyLabVault application."""
    print("🚀 Starting MyLabVault...")

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))

    # Docker containers need to bind to all interfaces
    if os.getenv("DOCKER_ENV") == "true":
        host = "0.0.0.0"  # nosec B104

    print(f"🌐 Starting FastAPI server on http://{host}:{port}")
    print("📚 API Documentation: http://localhost:8000/api/docs")

    # Docker environments disable reload to avoid file watcher issues
    reload = os.getenv("DOCKER_ENV") != "true"
    
    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info"
    )

if __name__ == "__main__":
    main()
