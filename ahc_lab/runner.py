from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    elapsed_sec: float
    timed_out: bool = False


def run_shell(command: str, cwd: Path, timeout: float | None = None) -> CommandResult:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - started
        return CommandResult(command, proc.returncode, proc.stdout, proc.stderr, elapsed)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        return CommandResult(
            command=command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"timeout after {timeout} sec",
            elapsed_sec=elapsed,
            timed_out=True,
        )


def excerpt(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...[truncated]...\n" + text[-limit // 2 :]

