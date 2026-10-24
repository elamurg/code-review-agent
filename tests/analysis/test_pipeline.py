"""CR-10 done-when: a diff goes in, a report comes out, and no model is involved."""

from collections.abc import Sequence
from pathlib import Path

from reviewer.analysis.base import DEFAULT_TIMEOUT_SECONDS
from reviewer.analysis.execution import Executor, LocalExecutor, ToolFailed, ToolTimeout
from reviewer.analysis.linters import RuffAnalyser
from reviewer.analysis.pipeline import analyse, default_analysers
from reviewer.analysis.report import ToolStatus
from reviewer.domain.models import Finding, PullRequest
from reviewer.ingestion.diff import parse_diff


def new_file_diff(path: str, text: str) -> str:
    """A git diff that adds the file, as GitHub would send it for a new file."""
    lines = text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")
    return (
        "\n".join(
            [
                f"diff --git a/{path} b/{path}",
                "new file mode 100644",
                "--- /dev/null",
                f"+++ b/{path}",
                f"@@ -0,0 +1,{len(lines)} @@",
                *(f"+{line}" for line in lines),
            ]
        )
        + "\n"
    )


def pull_request_adding(checkout: Path, *paths: str) -> PullRequest:
    diff = "".join(new_file_diff(p, (checkout / p).read_text()) for p in paths)
    return PullRequest(
        repo="octo/widgets",
        number=7,
        title="Add app",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=parse_diff(diff),
    )


def test_diff_to_report_end_to_end(broken_repo: Path) -> None:
    pr = pull_request_adding(broken_repo, "app.py", "requirements.txt")
    report = analyse(pr, broken_repo, LocalExecutor(), default_analysers(include_network=False))

    found = {(f.line, f.source, f.message.split(":")[0]) for f in report.findings}
    assert found == {
        (3, "ruff", "F401"),
        (9, "bandit", "B105"),  # the hardcoded secret
        (13, "bandit", "B602"),
        (17, "bandit", "B301"),
        # bandit and semgrep both flag lines 21 and 25: one finding each survives.
        (21, "bandit", "B501"),
        (25, "semgrep", "sql-built-with-string-formatting"),
        # mypy's name-defined is the same issue on the same line; ruff's is more severe.
        (30, "ruff", "F821"),
    }
    assert all(f.file == "app.py" for f in report.findings)
    assert [r.status for r in report.runs] == [ToolStatus.OK] * 4
    assert not report.incomplete
    # Most severe first.
    assert [f.severity.rank for f in report.findings] == sorted(
        (f.severity.rank for f in report.findings), reverse=True
    )


class _Broken:
    name = "broken"

    def __init__(self, error: Exception) -> None:
        self.error = error

    def select(self, pull_request: PullRequest) -> tuple[str, ...]:
        return ("app.py",)

    def analyse(
        self,
        files: Sequence[str],
        *,
        checkout: Path,
        executor: Executor,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[Finding, ...]:
        raise self.error


class _NothingToDo(_Broken):
    name = "idle"

    def select(self, pull_request: PullRequest) -> tuple[str, ...]:
        return ()


def test_a_failing_tool_is_recorded_and_the_others_still_run(broken_repo: Path) -> None:
    pr = pull_request_adding(broken_repo, "app.py")
    report = analyse(
        pr,
        broken_repo,
        LocalExecutor(),
        [
            _Broken(ToolFailed("exited with 2")),
            _Broken(ToolTimeout("ran longer than 1s")),
            _NothingToDo(ToolFailed("never called")),
            RuffAnalyser(),
        ],
    )
    assert [(r.tool, r.status) for r in report.runs] == [
        ("broken", ToolStatus.FAILED),
        ("broken", ToolStatus.TIMED_OUT),
        ("idle", ToolStatus.SKIPPED),
        ("ruff", ToolStatus.OK),
    ]
    assert report.runs[0].detail == "exited with 2"
    assert report.incomplete
    assert {f.source for f in report.findings} == {"ruff"}


def test_findings_outside_the_diff_are_not_reported(broken_repo: Path, tmp_path: Path) -> None:
    # The PR only adds line 1 of a file whose later, pre-existing lines are broken.
    (tmp_path / "old.py").write_text("x = 1\nimport os\nundefined_thing\n")
    diff = (
        "\n".join(
            [
                "diff --git a/old.py b/old.py",
                "--- a/old.py",
                "+++ b/old.py",
                "@@ -1,2 +1,3 @@",
                "+x = 1",
                " import os",
                " undefined_thing",
            ]
        )
        + "\n"
    )
    pr = PullRequest(
        repo="octo/widgets",
        number=8,
        title="t",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=parse_diff(diff),
    )
    report = analyse(pr, tmp_path, LocalExecutor(), default_analysers(include_network=False))
    assert report.findings == ()
