"""CR-09: untrusted code is contained.

The containment tests start real containers, so they are opt-in: pytest -m docker.
"""

import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from reviewer.analysis.base import Analyser
from reviewer.analysis.execution import LocalExecutor, ToolTimeout
from reviewer.analysis.linters import MypyAnalyser, RuffAnalyser
from reviewer.analysis.sandbox import (
    DEFAULT_IMAGE,
    SANDBOX_SEMGREP_RULES,
    DockerExecutor,
    SandboxLimits,
)
from reviewer.analysis.security import BanditAnalyser, PipAuditAnalyser, SemgrepAnalyser

HOSTILE = Path(__file__).parent.parent / "fixtures" / "sandbox"
REPO_ROOT = Path(__file__).parent.parent.parent


# --- the docker command, no daemon needed ---------------------------------------------


def test_command_locks_the_container_down(tmp_path: Path) -> None:
    command = DockerExecutor(limits=SandboxLimits(memory="256m", pids=64)).command(
        ["ruff", "check"], checkout=tmp_path, name="sandbox-1"
    )
    joined = " ".join(command)
    assert "--network none" in joined
    assert "--read-only" in command
    assert f"{tmp_path.resolve()}:/src:ro" in command
    assert "--memory 256m --memory-swap 256m" in joined
    assert "--pids-limit 64" in joined
    assert "--cap-drop ALL" in joined
    assert "no-new-privileges" in command
    assert "--rm" in command
    assert command[-2:] == ["ruff", "check"]


def test_network_is_only_enabled_when_asked(tmp_path: Path) -> None:
    command = DockerExecutor().command(["pip-audit"], checkout=tmp_path, name="n", network=True)
    assert "--network bridge" in " ".join(command)


# --- real containers ------------------------------------------------------------------


@pytest.fixture(scope="session")
def sandbox() -> Iterator[DockerExecutor]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    if subprocess.run(["docker", "info"], capture_output=True, check=False).returncode != 0:
        pytest.skip("the docker daemon is not running")
    image_exists = (
        subprocess.run(
            ["docker", "image", "inspect", DEFAULT_IMAGE], capture_output=True, check=False
        ).returncode
        == 0
    )
    if not image_exists:
        subprocess.run(
            ["docker", "build", "-f", "Dockerfile.analysis", "-t", DEFAULT_IMAGE, "."],
            cwd=REPO_ROOT,
            check=True,
        )
    yield DockerExecutor(limits=SandboxLimits(memory="256m", pids=64))


def _no_sandbox_containers_left() -> bool:
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=reviewer-sandbox-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return listed.strip() == ""


@pytest.mark.docker
def test_fork_bomb_is_capped_and_killed(sandbox: DockerExecutor) -> None:
    started = time.monotonic()
    with pytest.raises(ToolTimeout):
        sandbox.run(["python", "fork_bomb.py"], checkout=HOSTILE, timeout=10)
    assert time.monotonic() - started < 60
    time.sleep(1)
    assert _no_sandbox_containers_left()


@pytest.mark.docker
def test_outbound_network_call_is_blocked(sandbox: DockerExecutor) -> None:
    result = sandbox.run(["python", "phone_home.py"], checkout=HOSTILE, timeout=60)
    assert result.stdout.startswith("BLOCKED"), result.stdout + result.stderr


@pytest.mark.docker
def test_checkout_cannot_be_modified(sandbox: DockerExecutor) -> None:
    result = sandbox.run(["python", "write_checkout.py"], checkout=HOSTILE, timeout=60)
    assert result.stdout.startswith("BLOCKED"), result.stdout + result.stderr
    assert not (HOSTILE / "planted.py").exists()


@pytest.mark.docker
def test_memory_hog_is_killed(sandbox: DockerExecutor) -> None:
    result = sandbox.run(["python", "eat_memory.py"], checkout=HOSTILE, timeout=60)
    # 137 = killed by SIGKILL, which is what the kernel's OOM killer sends.
    assert result.returncode == 137


@pytest.mark.docker
def test_sandbox_runs_as_a_non_root_user(sandbox: DockerExecutor) -> None:
    result = sandbox.run(["id", "-u"], checkout=HOSTILE, timeout=60)
    assert result.stdout.strip() != "0"


@pytest.mark.docker
def test_analysers_find_the_same_things_in_the_sandbox(
    sandbox: DockerExecutor, broken_repo: Path
) -> None:
    pairs: list[tuple[Analyser, Analyser]] = [
        (RuffAnalyser(), RuffAnalyser()),
        (MypyAnalyser(), MypyAnalyser()),
        (BanditAnalyser(), BanditAnalyser()),
        (SemgrepAnalyser(), SemgrepAnalyser(SANDBOX_SEMGREP_RULES)),
    ]
    for local_analyser, sandboxed_analyser in pairs:
        local = local_analyser.analyse(["app.py"], checkout=broken_repo, executor=LocalExecutor())
        boxed = sandboxed_analyser.analyse(["app.py"], checkout=broken_repo, executor=sandbox)
        assert {(f.file, f.line, f.message) for f in boxed} == {
            (f.file, f.line, f.message) for f in local
        }, local_analyser.name


@pytest.mark.docker
@pytest.mark.network
def test_pip_audit_works_in_the_sandbox_with_network(
    sandbox: DockerExecutor, broken_repo: Path
) -> None:
    findings = PipAuditAnalyser().analyse(
        ["requirements.txt"], checkout=broken_repo, executor=sandbox
    )
    assert [f.line for f in findings] == [2]
