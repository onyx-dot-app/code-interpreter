from __future__ import annotations

from pydantic import BaseModel, Field, StrictInt, StrictStr


class ExecuteRequest(BaseModel):
    code: StrictStr = Field(..., description="Python source to execute.")
    stdin: StrictStr | None = Field(None, description="Optional stdin passed to the program.")
    timeout_ms: StrictInt = Field(2000, ge=1, description="Execution timeout in milliseconds.")


class ExecuteResponse(BaseModel):
    stdout: StrictStr
    stderr: StrictStr
    exit_code: int | None
    timed_out: bool
    duration_ms: StrictInt

