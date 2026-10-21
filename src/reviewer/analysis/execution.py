"""Running analyser commands: where they run, how long they get, and what comes back.

Analysers never call subprocess themselves. They hand an argv to an Executor, so the
same analyser can run as a plain local process (fast, for tests and trusted code) or
inside the sandbox container (for untrusted pull request code, see sandbox.py).
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class AnalysisError(Exception):
    """Base class for anything that stops an analyser producing findings."""


class ToolTimeout(AnalysisError):
    """The tool ran past its time limit and was killed."""


class ToolFailed(AnalysisError):
    """The tool could not be started, crashed, or produced output we cannot read."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    # The checkout's path as the tool saw it, needed to turn absolute paths back into
    # repo-relative ones. Differs from the host path when the tool ran in a container.
    workdir: str
    duration_seconds: float


class Executor(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        checkout: Path,
        timeout: float,
        network: bool = False,
        stdin: str | None = None,
    ) -> ToolResult: ...


# Tools must not phone home or litter the checkout.
TOOL_ENVIRONMENT = {
    "SEMGREP_SEND_METRICS": "off",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONIOENCODING": "utf-8",
}


class LocalExecutor:
    """Runs tools as ordinary subprocesses in the checkout directory.

    This isolates nothing: no network block, no memory cap. Use it for tests and for
    code you trust. It does enforce the timeout, and kills the tool's whole process
    group when it runs out, so a tool that spawned children cannot leave them behind.
    """

    def __init__(self, tool_dirs: Sequence[Path] | None = None) -> None:
        # Prefer tools installed next to this interpreter (the project venv), even when
        # the venv is not activated.
        dirs = tool_dirs if tool_dirs is not None else [Path(sys.executable).parent]
        self._path = os.pathsep.join([*map(str, dirs), os.environ.get("PATH", "")])

    def run(
        self,
        argv: Sequence[str],
        *,
        checkout: Path,
        timeout: float,
        network: bool = False,
        stdin: str | None = None,
    ) -> ToolResult:
        env = {**os.environ, **TOOL_ENVIRONMENT, "PATH": self._path}
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=checkout,
                env=env,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                # Its own session, so the timeout can kill it and every child it started.
                start_new_session=True,
            )
        except OSError as exc:
            raise ToolFailed(f"could not start {argv[0]}: {exc}") from exc

        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ToolTimeout(f"{argv[0]} ran longer than {timeout:g}s and was killed") from None

        return ToolResult(
            argv=tuple(argv),
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            workdir=str(checkout),
            duration_seconds=time.monotonic() - started,
        )
