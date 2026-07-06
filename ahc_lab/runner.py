from __future__ import annotations

import os
import signal
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
    """Run a shell command, killing the whole process group on timeout.

    `subprocess.run` only kills the shell itself, so a hung solver spawned by
    the shell survives as an orphan and keeps polluting the machine. Each
    command runs in its own session and the group is SIGKILLed on timeout.
    """
    started = time.perf_counter()
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        elapsed = time.perf_counter() - started
        return CommandResult(command, proc.returncode, stdout, stderr, elapsed)
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - started
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        stdout, stderr = proc.communicate()
        return CommandResult(
            command=command,
            returncode=124,
            stdout=_to_text(stdout),
            stderr=_to_text(stderr) or f"timeout after {timeout} sec",
            elapsed_sec=elapsed,
            timed_out=True,
        )


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def excerpt(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...[truncated]...\n" + text[-limit // 2 :]
