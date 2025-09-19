from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.models.schemas import ExecuteRequest, ExecuteResponse
from app.services.executor import ExecutionResult, execute_python


router = APIRouter()


@router.post("/execute", response_model=ExecuteResponse, status_code=status.HTTP_200_OK)
def execute(req: ExecuteRequest) -> ExecuteResponse:
    """Execute provided Python code synchronously within a restricted subprocess.

    Note: This is not a security boundary. Do not expose publicly without
    a proper sandbox and isolation strategy.
    """
    settings = get_settings()

    if req.timeout_ms > settings.max_exec_timeout_ms:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"timeout_ms exceeds maximum of {settings.max_exec_timeout_ms} ms",
        )

    result: ExecutionResult = execute_python(
        code=req.code,
        stdin=req.stdin,
        timeout_ms=req.timeout_ms,
        max_output_bytes=settings.max_output_bytes,
        cpu_time_limit_sec=settings.cpu_time_limit_sec,
        memory_limit_mb=settings.memory_limit_mb,
    )

    return ExecuteResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_ms=result.duration_ms,
    )
