from __future__ import annotations

import os
from typing import Final

from fastapi import FastAPI

from app.api.routes import router as api_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Code Interpreter API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:  # sync + strictly typed
        return {"status": "ok"}

    app.include_router(api_router, prefix="/v1")
    return app


app: Final[FastAPI] = create_app()


def run() -> None:
    """Run the API using Uvicorn.

    This is for local/dev usage. Production deployments should use a process manager
    and configure workers according to their environment.
    """
    import uvicorn

    host: str = os.environ.get("HOST", "127.0.0.1")
    port_str: str | None = os.environ.get("PORT")
    port: int = int(port_str) if port_str else 8000
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")

