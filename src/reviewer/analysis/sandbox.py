"""Run analysers inside a locked-down container.

We are running tools over strangers' code. The tools are meant to read it, not run it,
but tool config can load plugins, and tools have bugs. So assume the code gets
executed, and make sure that when it does, it cannot reach the network, write to the
checkout, exhaust the host's memory or processes, or outlive its time limit.
"""

import os
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from reviewer.analysis.execution import TOOL_ENVIRONMENT, ToolFailed, ToolResult, ToolTimeout

DEFAULT_IMAGE = "reviewer-analysers:latest"
SANDBOX_WORKDIR = "/src"
# Where the sandbox image keeps the semgrep rules (see Dockerfile.analysis).
SANDBOX_SEMGREP_RULES = "/opt/reviewer/semgrep.yml"


@dataclass(frozen=True, slots=True, kw_only=True)
class SandboxLimits:
    memory: str = "1g"
    cpus: str = "1"
    pids: int = 256
    tmp_size: str = "256m"


class DockerExecutor:
    """Executes each tool in a fresh container, removed when it exits.

    - no network (unless the analyser needs it: pip-audit, which executes nothing)
    - the checkout is mounted read-only, and the container's own filesystem is read-only
    - writable /tmp only, in memory, size-capped and not executable
    - memory, CPU and process-count caps (a fork bomb hits the pids cap)
    - all Linux capabilities dropped, no privilege escalation, not root
    - a wall-clock timeout, enforced by killing the container
    """

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        limits: SandboxLimits | None = None,
        docker: str = "docker",
    ) -> None:
        self.image = image
        self.limits = limits or SandboxLimits()
        self.docker = docker

    def command(
        self,
        argv: Sequence[str],
        *,
        checkout: Path,
        name: str,
        network: bool = False,
        interactive: bool = False,
    ) -> list[str]:
        limits = self.limits
        env = [
            arg for key, value in TOOL_ENVIRONMENT.items() for arg in ("--env", f"{key}={value}")
        ]
        return [
            self.docker,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "bridge" if network else "none",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={limits.tmp_size}",
            "--memory",
            limits.memory,
            "--memory-swap",
            limits.memory,  # same as memory: no swap to hide in
            "--cpus",
            limits.cpus,
            "--pids-limit",
            str(limits.pids),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            # The host user's ids: never root, and still able to read the checkout.
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/tmp",
            *env,
            "--volume",
            f"{checkout.resolve()}:{SANDBOX_WORKDIR}:ro",
            "--workdir",
            SANDBOX_WORKDIR,
            *(["--interactive"] if interactive else []),
            self.image,
            *argv,
        ]

    def run(
        self,
        argv: Sequence[str],
        *,
        checkout: Path,
        timeout: float,
        network: bool = False,
        stdin: str | None = None,
    ) -> ToolResult:
        name = f"reviewer-sandbox-{uuid.uuid4().hex[:12]}"
        command = self.command(
            argv, checkout=checkout, name=name, network=network, interactive=stdin is not None
        )
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise ToolFailed(f"could not start docker: {exc}") from exc

        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Killing the docker CLI would leave the container running; kill the container.
            self._kill(name)
            process.communicate()
            raise ToolTimeout(f"{argv[0]} ran longer than {timeout:g}s and was killed") from None

        if process.returncode == 125:  # docker itself failed: bad image, bad flag, no daemon
            raise ToolFailed(f"docker could not run the sandbox: {stderr.strip()[-2000:]}")
        return ToolResult(
            argv=tuple(argv),
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            workdir=SANDBOX_WORKDIR,
            duration_seconds=time.monotonic() - started,
        )

    def _kill(self, name: str) -> None:
        subprocess.run(
            [self.docker, "kill", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
