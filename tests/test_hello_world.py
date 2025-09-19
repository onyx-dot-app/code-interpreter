from __future__ import annotations

import os
from pathlib import Path
from shutil import which

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def _runtime_available() -> bool:
    runtime = os.environ.get("PYTHON_WASM_RUNTIME")
    if runtime and (os.path.isabs(runtime) or os.sep in runtime):
        candidate = os.path.abspath(runtime)
        return os.path.isfile(candidate) and os.access(candidate, os.X_OK)

    for name in filter(None, [runtime, "wasmtime"]):
        found = which(name)
        if found:
            return True

    for fallback in (
        Path.home() / ".wasmtime" / "bin" / "wasmtime",
        Path.home() / ".cargo" / "bin" / "wasmtime",
    ):
        if fallback.is_file() and os.access(fallback, os.X_OK):
            return True

    return False


def _require_wasm_environment() -> None:
    wasm_path = os.environ.get("PYTHON_WASM_PATH")
    if not wasm_path or not Path(wasm_path).exists():
        pytest.skip("Skipping WASM integration test: PYTHON_WASM_PATH not configured")

    if not _runtime_available():
        pytest.skip("Skipping WASM integration test: WASM runtime binary not available")


def test_execute_returns_expected_payload() -> None:
    _require_wasm_environment()

    client = TestClient(create_app())
    response = client.post(
        "/v1/execute",
        json={
            "code": "print('hello')",
            "stdin": None,
            "timeout_ms": 1000,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stdout"] == "hello\n"
    assert payload["stderr"] == ""
    assert payload["exit_code"] == 0
    assert payload["timed_out"] is False
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] >= 0
