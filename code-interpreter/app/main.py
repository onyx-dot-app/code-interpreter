from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from importlib.metadata import version as _package_version
from shutil import which
from typing import Final

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.app_configs import (
    EXECUTOR_BACKEND,
    HOST,
    PORT,
    PYTHON_EXECUTOR_DOCKER_BIN,
    PYTHON_EXECUTOR_DOCKER_IMAGE,
    PYTHON_EXECUTOR_DOCKER_IMAGE_WATCHDOG_INTERVAL_SEC,
)
from app.image_ref import normalize_image_ref
from app.logging_config import setup_logging
from app.models.schemas import HealthResponse
from app.services.executor_factory import get_executor

SESSION_REAPER_INTERVAL_SEC = 30

# Configure logging
setup_logging()

logger = logging.getLogger(__name__)

SERVICE_VERSION: Final[str] = _package_version("code-interpreter")


def _ensure_docker_image_available(*, quiet: bool = False) -> bool:
    """Ensure the Docker executor image is available locally.

    This checks if the image exists locally, and if not, attempts to pull it.
    It runs during application startup so the image is ready before accepting
    requests, and again from the image watchdog so a host that removes the image
    while the service is running (e.g. ``docker system prune -a``) recovers
    without a restart.

    Returns ``True`` if the image was missing and had to be pulled. Raises
    ``RuntimeError`` if the pull fails or times out. With ``quiet``, the routine
    "image is present" path logs at DEBUG instead of INFO so periodic callers do
    not fill the log; pulls and failures are always logged.
    """
    present_level = logging.DEBUG if quiet else logging.INFO

    docker_bin = which(PYTHON_EXECUTOR_DOCKER_BIN)
    if not docker_bin:
        logger.warning("Docker binary not found, skipping image check")
        return False

    image_with_tag = normalize_image_ref(PYTHON_EXECUTOR_DOCKER_IMAGE)

    # Check if image exists locally
    logger.log(present_level, f"Checking for Docker image: {image_with_tag}")
    check_result = subprocess.run(
        [docker_bin, "image", "inspect", image_with_tag],
        capture_output=True,
        timeout=10,
        check=False,
    )

    if check_result.returncode == 0:
        logger.log(present_level, f"Docker image {image_with_tag} is already available locally")
        return False

    # Image doesn't exist, try to pull it
    logger.info(f"Docker image {image_with_tag} not found locally, attempting to pull...")
    try:
        pull_result = subprocess.run(
            [docker_bin, "pull", image_with_tag],
            capture_output=True,
            timeout=300,  # 5 minutes timeout for pulling
            check=False,
        )

        if pull_result.returncode == 0:
            logger.info(f"Successfully pulled {image_with_tag}")
            return True

        error_msg = (
            pull_result.stderr.decode("utf-8", errors="replace") if pull_result.stderr else ""
        )
        logger.error(f"Failed to pull {image_with_tag}: {error_msg}")
        raise RuntimeError(
            f"Docker executor image {image_with_tag} is not available locally "
            f"and could not be pulled. Error: {error_msg}"
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Timeout while pulling Docker image {image_with_tag}. "
            "This may indicate network issues or the image is very large."
        ) from e


async def _reap_expired_sessions_once() -> None:
    """Run a single reap pass via the configured executor."""
    try:
        count = await asyncio.to_thread(get_executor().reap_expired_sessions)
    except Exception:
        logger.warning("Session reaper pass failed", exc_info=True)
        return
    if count > 0:
        logger.info("Reaped %d expired session(s)", count)


async def _session_reaper_loop() -> None:
    """Periodically delete sessions whose TTL has elapsed."""
    while True:
        await asyncio.sleep(SESSION_REAPER_INTERVAL_SEC)
        await _reap_expired_sessions_once()


async def _image_watchdog_once() -> None:
    """Run a single image watchdog pass: re-pull the executor image if it went missing.

    Never raises. A failed re-pull is logged and retried on the next pass; it must
    not take down a running service that may have live sessions.
    """
    try:
        pulled = await asyncio.to_thread(_ensure_docker_image_available, quiet=True)
    except Exception:
        logger.warning(
            "Executor image watchdog could not restore the executor image; "
            "will retry on the next pass",
            exc_info=True,
        )
        return
    if pulled:
        logger.warning(
            "Executor image %s was missing from the host (e.g. removed by `docker system prune`) "
            "and has been re-pulled",
            normalize_image_ref(PYTHON_EXECUTOR_DOCKER_IMAGE),
        )


async def _image_watchdog_loop(interval_sec: int) -> None:
    """Periodically make sure the executor image is still present on the host.

    Executor containers start with ``--pull never``, so an image removed by the
    host (``docker system prune -a``, image garbage collection) would otherwise
    fail every execution until an operator restarts the service.
    """
    while True:
        await asyncio.sleep(interval_sec)
        await _image_watchdog_once()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan events."""
    # Startup: Ensure Docker executor image is available before accepting requests
    if EXECUTOR_BACKEND == "docker":
        logger.info("Ensuring Docker executor image is available...")
        _ensure_docker_image_available()
        logger.info("Docker executor image is ready")

    # Reap any sessions whose TTL elapsed while the service was down.
    await _reap_expired_sessions_once()
    background_tasks: list[asyncio.Task[None]] = [asyncio.create_task(_session_reaper_loop())]

    # Keep the executor image present for the lifetime of the service; see
    # _image_watchdog_loop. Interval 0 disables it (e.g. air-gapped hosts).
    if EXECUTOR_BACKEND == "docker" and PYTHON_EXECUTOR_DOCKER_IMAGE_WATCHDOG_INTERVAL_SEC > 0:
        logger.info(
            "Starting executor image watchdog (interval: %ds)",
            PYTHON_EXECUTOR_DOCKER_IMAGE_WATCHDOG_INTERVAL_SEC,
        )
        background_tasks.append(
            asyncio.create_task(
                _image_watchdog_loop(PYTHON_EXECUTOR_DOCKER_IMAGE_WATCHDOG_INTERVAL_SEC)
            )
        )

    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    app = FastAPI(
        title="Code Interpreter API",
        version=SERVICE_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> HealthResponse:
        """Health check that verifies the executor backend is operational."""
        result = get_executor().check_health()
        return HealthResponse(
            status=result.status,
            message=result.message,
            version=SERVICE_VERSION,
        )

    app.include_router(api_router, prefix="/v1")
    return app


app: Final[FastAPI] = create_app()


def run() -> None:
    """Run the API using Uvicorn.

    This is for local/dev usage. Production deployments should use a process manager
    and configure workers according to their environment.
    """
    import uvicorn

    # log_config=None keeps the logging configured by setup_logging(); otherwise
    # uvicorn would install its own handlers/formatters and bypass our format.
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_config=None)
