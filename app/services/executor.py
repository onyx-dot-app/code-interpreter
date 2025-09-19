from __future__ import annotations

import base64
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from shutil import which
from dataclasses import dataclass
from typing import Callable


try:  # POSIX resource limits (best-effort)
    import resource  # type: ignore
except Exception:  # pragma: no cover - non-POSIX
    resource = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int


def _limit_preexec(cpu_time_sec: int, memory_limit_mb: int) -> Callable[[], None]:
    def _apply() -> None:  # executed in child before exec
        if resource is None:
            return
        try:
            # CPU time hard limit
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_time_sec, cpu_time_sec))
        except Exception:
            pass
        try:
            # Address space / virtual memory limit
            bytes_limit = memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
        except Exception:
            pass
        try:
            # Prevent creating files larger than ~16MB
            resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
        except Exception:
            pass
        try:
            # Limit open files
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except Exception:
            pass
        try:
            # Limit number of processes; best-effort, may be ignored on some OSes
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except Exception:
            pass
        # Reset signal handlers and create a new process group
        os.setsid()
    return _apply


def _truncate(s: bytes, max_bytes: int) -> str:
    if len(s) <= max_bytes:
        return s.decode("utf-8", errors="replace")
    head = s[: max(0, max_bytes - 32)]
    suffix = b"\n...[truncated]"
    return (head + suffix).decode("utf-8", errors="replace")


_WASM_BOOTSTRAP_SCRIPT = "\n".join(
    [
        "import base64, os as _os, sys as _sys",
        "_prefix = _os.environ.get('PYTHON_WASM_PREFIX')",
        "if _prefix:",
        "    _sys.prefix = _sys.exec_prefix = _prefix",
        "_paths = _os.environ.get('PYTHON_WASM_STDLIB_PATHS')",
        "if _paths:",
        "    _entries = [p for p in _paths.split(':') if p]",
        "    for _entry in reversed(_entries):",
        "        if _entry not in _sys.path:",
        "            _sys.path.insert(0, _entry)",
        "_code = base64.b64decode(_os.environ['PY_CODE_B64']).decode('utf-8')",
        "_globals = {'__name__': '__main__'}",
        "exec(compile(_code, '<user_code>', 'exec'), _globals)",
    ]
)


def _locate_runtime(executable: str | None) -> str | None:
    if not executable:
        return None
    if os.path.isabs(executable) or os.sep in executable:
        candidate = os.path.abspath(executable)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        return None
    return which(executable)


def _resolve_wasm_runtime() -> tuple[str, str, list[str]]:
    runtime_path = _locate_runtime(os.environ.get("PYTHON_WASM_RUNTIME"))
    if runtime_path is None:
        runtime_path = _locate_runtime("wasmtime")
    if runtime_path is None:
        for default_path in (
            Path("/opt/homebrew/bin/wasmtime"),
            Path("/usr/local/bin/wasmtime"),
            Path.home() / ".wasmtime" / "bin" / "wasmtime",
            Path.home() / ".cargo" / "bin" / "wasmtime",
        ):
            if default_path.is_file() and os.access(default_path, os.X_OK):
                runtime_path = str(default_path)
                break
    if runtime_path is None:
        raise RuntimeError(
            "WASM runtime binary not found. Set PYTHON_WASM_RUNTIME to the CLI (e.g. wasmtime)."
        )

    wasm_module = os.environ.get("PYTHON_WASM_PATH")
    if not wasm_module:
        raise RuntimeError("PYTHON_WASM_PATH environment variable must point to python.wasm")
    if not os.path.exists(wasm_module):
        raise RuntimeError(f"WASM module not found at PYTHON_WASM_PATH: {wasm_module}")
    if os.path.isdir(wasm_module):
        raise RuntimeError(
            "PYTHON_WASM_PATH must point to a WebAssembly binary, "
            f"but a directory was provided: {wasm_module}"
        )

    try:
        with open(wasm_module, "rb") as fh:
            magic = fh.read(4)
    except OSError as exc:  # pragma: no cover - rare filesystem errors
        raise RuntimeError(
            "Unable to read the WASM module referenced by PYTHON_WASM_PATH."
        ) from exc

    if magic != b"\0asm":
        raise RuntimeError(
            "PYTHON_WASM_PATH must reference a valid WebAssembly module (magic '\\0asm'). "
            "Double-check that it points to your `python.wasm`, not the WASM runtime binary."
        )

    extra_args = shlex.split(os.environ.get("PYTHON_WASM_RUNTIME_ARGS", ""))

    return runtime_path, os.path.abspath(wasm_module), extra_args


def _discover_stdlib_paths(python_root: Path, guest_prefix: str) -> list[str]:
    lib_root = python_root / "lib"
    if not lib_root.is_dir():
        return []

    version_dir: Path | None = None
    version_pattern = re.compile(r"python\d+(?:\.\d+)?")
    for candidate in sorted(lib_root.iterdir()):
        if candidate.is_dir() and version_pattern.fullmatch(candidate.name):
            version_dir = candidate
            break

    if version_dir is None:
        return []

    guest_paths: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if path not in seen:
            guest_paths.append(path)
            seen.add(path)

    guest_base = f"{guest_prefix}/lib/{version_dir.name}"
    _add(guest_base)

    for subdir in ("lib-dynload", "site-packages"):
        if (version_dir / subdir).is_dir():
            _add(f"{guest_base}/{subdir}")

    for zip_candidate in sorted(lib_root.glob("python*.zip")):
        _add(f"{guest_prefix}/lib/{zip_candidate.name}")

    return guest_paths


def execute_python(
    *,
    code: str,
    stdin: str | None,
    timeout_ms: int,
    max_output_bytes: int,
    cpu_time_limit_sec: int | None = None,
    memory_limit_mb: int | None = None,
) -> ExecutionResult:
    """Execute Python code inside a WASM sandbox.

    Notes:
    - Requires a Python WASM module and runtime CLI (default: wasmtime).
    - No host filesystem is preopened; network access is unavailable under WASI.
    - Output is captured and truncated to `max_output_bytes` per stream.
    - Best-effort POSIX limits are applied to the host wrapper process where available.
    """
    runtime_path, wasm_module, runtime_args = _resolve_wasm_runtime()
    runtime_args = [*runtime_args]

    wasm_module_path = Path(wasm_module).resolve()
    python_root = wasm_module_path.parent
    guest_prefix = "/python"
    host_mapping = f"{python_root}::{guest_prefix.lstrip('/')}"

    def _has_mapping(args: list[str]) -> bool:
        target_suffix = f"::{guest_prefix.lstrip('/')}"
        for idx, value in enumerate(args):
            if value == "--dir":
                if idx + 1 < len(args) and args[idx + 1].endswith(target_suffix):
                    return True
            elif value.startswith("--dir=") and value[6:].endswith(target_suffix):
                return True
        return False

    if not _has_mapping(runtime_args):
        runtime_args.extend(["--dir", host_mapping])

    stdlib_paths = _discover_stdlib_paths(python_root, guest_prefix)

    start = time.perf_counter()

    encoded_code = base64.b64encode(code.encode("utf-8")).decode("ascii")

    python_args: list[str] = []
    force_isolated = os.environ.get("PYTHON_WASM_FORCE_ISOLATED")
    disable_isolated = os.environ.get("PYTHON_WASM_DISABLE_ISOLATED")
    if force_isolated is not None:
        use_isolated = force_isolated == "1"
    elif disable_isolated is not None:
        use_isolated = disable_isolated != "1"
    else:
        use_isolated = False

    if use_isolated:
        python_args.append("-I")

    python_args.extend(["-c", _WASM_BOOTSTRAP_SCRIPT])

    env: dict[str, str] = os.environ.copy()
    stdlib_path_str = ":".join(stdlib_paths)

    env_updates = {
        "PYTHONUNBUFFERED": "1",
        "PY_CODE_B64": encoded_code,
        "PYTHON_WASM_PREFIX": guest_prefix,
        "PYTHON_WASM_STDLIB_PATHS": stdlib_path_str,
        "PYTHONHOME": guest_prefix,
    }
    if stdlib_path_str:
        env_updates["PYTHONPATH"] = stdlib_path_str

    env.update(env_updates)

    def _ensure_env_arg(args: list[str], key: str, value: str) -> None:
        prefix = f"{key}="
        for idx, existing in enumerate(args):
            if existing == "--env":
                if idx + 1 < len(args) and args[idx + 1].startswith(prefix):
                    return
            elif existing.startswith("--env=") and existing[6:].startswith(prefix):
                return
        args.extend(["--env", f"{key}={value}"])

    for env_key, env_value in env_updates.items():
        _ensure_env_arg(runtime_args, env_key, env_value)

    cmd: list[str] = [
        runtime_path,
        "run",
        *runtime_args,
        wasm_module,
        *python_args,
    ]

    preexec = _limit_preexec(
        cpu_time_sec=cpu_time_limit_sec if cpu_time_limit_sec is not None else 2,
        memory_limit_mb=memory_limit_mb if memory_limit_mb is not None else 256,
    )

    with tempfile.TemporaryDirectory(prefix="exec-wasm-") as workdir:
        proc = subprocess.Popen(  # nosec: B603 (controlled argv)
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            env=env,
            text=False,
            preexec_fn=preexec if os.name == "posix" else None,
        )

        try:
            input_bytes = stdin.encode("utf-8") if stdin is not None else None
            out, err = proc.communicate(input=input_bytes, timeout=timeout_ms / 1000.0)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
            proc.kill()
            out, err = proc.communicate()

    duration_ms = int((time.perf_counter() - start) * 1000)

    stdout = _truncate(out or b"", max_output_bytes)
    stderr = _truncate(err or b"", max_output_bytes)
    exit_code = None if timed_out else proc.returncode
    return ExecutionResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
    )
