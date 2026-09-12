"""
Bot 3: Maintenance Agent (Safe Health & Ops Service)

⚠️ SECURITY NOTE ⚠️
The legacy remote code execution endpoints have been permanently deprecated.
This service provides an operational health check and system diagnostic endpoint.
"""

import logging
from fastapi import FastAPI
import uvicorn

logger = logging.getLogger(__name__)

app = FastAPI(title="Metobot Maintenance & Diagnostics Agent", version="0.5.0")

@app.get("/health")
async def health_check():
    """Operational health check endpoint."""
    return {"status": "alive", "service": "maintenance_agent", "version": "0.5.0"}

if __name__ == "__main__":
    logger.info("Starting Bot 3 (Maintenance & Diagnostics Agent)...")
    uvicorn.run(app, host="127.0.0.1", port=8001)
