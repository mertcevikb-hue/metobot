"""
Metobot API Gateway Entrypoint
This entrypoint forwards cleanly to web.api to maintain a single source of truth.
"""
import uvicorn
from web.api import app

if __name__ == "__main__":
    import os
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    uvicorn.run("web.api:app", host=host, port=port, reload=True)