Code Interpreter API (FastAPI, Sync, Typed)

Overview
- Sync FastAPI service that executes short Python 3.11 snippets.
- Strict typing (mypy strict) and basic resource limits.
- Not a security boundary. Do not expose to untrusted users without proper isolation (e.g., containers, seccomp, firejail, gVisor, microVMs).

Quick Start
- Install: `pip install -e .` (or `uv pip install -e .`)
- Provision a WASI Python runtime (e.g. `python.wasm`) and set `PYTHON_WASM_PATH` to its location.
- Ensure a WASM CLI runtime is available (default: `wasmtime`). Override with `PYTHON_WASM_RUNTIME` if needed.
- Run: `code-interpreter-api` (uses `HOST` and `PORT` env vars; defaults: 127.0.0.1:8000)
- Docs: open `http://127.0.0.1:8000/docs`

API
- `POST /v1/execute`
  - Request JSON:
    - `code` (str): Python source.
    - `stdin` (str, optional): Data piped to the program.
    - `timeout_ms` (int): Max wall time; capped by server.
  - Response JSON:
    - `stdout` (str), `stderr` (str), `exit_code` (int|null), `timed_out` (bool), `duration_ms` (int)

Environment Settings
- `MAX_EXEC_TIMEOUT_MS` (default 5000)
- `MAX_OUTPUT_BYTES` (default 1_000_000)
- `CPU_TIME_LIMIT_SEC` (default 2)
- `MEMORY_LIMIT_MB` (default 256)

Implementation Notes
- Executor launches a WASI sandbox (`wasmtime run python.wasm -- -I -c <bootstrap>`) with no preopened host directories.
- Best-effort POSIX `resource` limits: CPU, address space, file size, open files, processes.
- Output is captured and truncated per stream to `MAX_OUTPUT_BYTES`.

WASM Runtime Configuration
- `PYTHON_WASM_PATH` (**required**): path to the Python WASM module.
- `PYTHON_WASM_RUNTIME` (optional): CLI executable to run the module (default `wasmtime`).
- `PYTHON_WASM_RUNTIME_ARGS` (optional): extra CLI args inserted after `run` (space-separated string).
- `PYTHON_WASM_DISABLE_ISOLATED` (optional): set to `1` to skip passing `-I` to the Python runtime if unsupported.
- If `wasmtime` lives outside `PATH` (e.g., `/opt/homebrew/bin/wasmtime`), either expose it via `PATH` or point `PYTHON_WASM_RUNTIME` to the absolute binary path.

Placeholders are not bundled; obtain a WASI-compatible Python build separately (e.g., CPython WASI or MicroPython WASM) and keep it free from host filesystem capabilities.

Local Development
- Type check: `mypy .`
- Lint (optional): configure Ruff locally (`ruff check .`).

Security Considerations
- This service is not safe against malicious code by default.
- Use OS-level and container isolation, drop privileges, network namespaces, cgroups, and syscall filtering for real deployments.
