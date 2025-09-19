from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    max_exec_timeout_ms: int = 5_000
    max_output_bytes: int = 1_000_000  # 1MB cap per stream after execution
    cpu_time_limit_sec: int = 2        # RLIMIT_CPU for child process
    memory_limit_mb: int = 256         # RLIMIT_AS (address space)

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            max_exec_timeout_ms=_int_from_env("MAX_EXEC_TIMEOUT_MS", 5_000),
            max_output_bytes=_int_from_env("MAX_OUTPUT_BYTES", 1_000_000),
            cpu_time_limit_sec=_int_from_env("CPU_TIME_LIMIT_SEC", 2),
            memory_limit_mb=_int_from_env("MEMORY_LIMIT_MB", 256),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()

