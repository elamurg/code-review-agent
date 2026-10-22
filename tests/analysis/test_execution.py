import os
import sys
import time
from pathlib import Path

import pytest

from reviewer.analysis.execution import LocalExecutor, ToolFailed, ToolTimeout


def test_runs_in_the_checkout_and_captures_output(tmp_path: Path) -> None:
    result = LocalExecutor().run(
        [sys.executable, "-c", "import os, sys; print(os.getcwd()); sys.exit(3)"],
        checkout=tmp_path,
        timeout=30,
    )
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()
    assert result.returncode == 3
    assert result.workdir == str(tmp_path)


def test_passes_stdin(tmp_path: Path) -> None:
    result = LocalExecutor().run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        checkout=tmp_path,
        timeout=30,
        stdin="pinned==1.0",
    )
    assert result.stdout.strip() == "PINNED==1.0"


def test_missing_tool_is_a_tool_failure(tmp_path: Path) -> None:
    with pytest.raises(ToolFailed, match="could not start"):
        LocalExecutor().run(["no-such-analyser-xyz"], checkout=tmp_path, timeout=5)


def test_timeout_kills_the_tool_and_its_children(tmp_path: Path) -> None:
    # The tool starts a child that would outlive it, records the child's pid, then hangs.
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open('child.pid', 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    with pytest.raises(ToolTimeout, match="was killed"):
        LocalExecutor().run([sys.executable, "-c", script], checkout=tmp_path, timeout=2)
    assert time.monotonic() - started < 15

    child_pid = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _is_running(child_pid):
        time.sleep(0.1)
    assert not _is_running(child_pid)


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child that nobody has reaped yet is a zombie: dead, but still listed.
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split()[2]
        return status != "Z"
    except OSError:
        pass
    ps = os.popen(f"ps -o stat= -p {pid}").read().strip()
    return bool(ps) and not ps.startswith("Z")
