from typing import Any

from reviewer.analysis.report import (
    AnalysisReport,
    ToolRun,
    ToolStatus,
    added_lines,
    build_report,
    render_for_agents,
    restrict_to_changes,
)
from reviewer.domain.models import (
    Category,
    DiffFile,
    FileStatus,
    Finding,
    Hunk,
    Line,
    LineKind,
    PullRequest,
    Severity,
)


def pull_request() -> PullRequest:
    # app.py: line 1 is context, line 2 was added, old line 2 was removed.
    hunk = Hunk(
        old_start=1,
        old_count=2,
        new_start=1,
        new_count=2,
        lines=(
            Line(kind=LineKind.CONTEXT, content="a", old_number=1, new_number=1),
            Line(kind=LineKind.REMOVED, content="b", old_number=2, new_number=None),
            Line(kind=LineKind.ADDED, content="c", old_number=None, new_number=2),
        ),
    )
    return PullRequest(
        repo="octo/widgets",
        number=1,
        title="t",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=(
            DiffFile(
                status=FileStatus.MODIFIED, old_path="app.py", new_path="app.py", hunks=(hunk,)
            ),
        ),
    )


def tool_finding(**overrides: Any) -> Finding:
    fields: dict[str, Any] = {
        "file": "app.py",
        "line": 2,
        "severity": Severity.MEDIUM,
        "category": Category.SECURITY,
        "message": "B602: shell=True",
        "evidence": "run(cmd, shell=True)",
        "source": "bandit",
        "confidence": 0.9,
    }
    fields.update(overrides)
    return Finding(**fields)


def test_added_lines_are_new_side_numbers_of_added_lines_only() -> None:
    assert added_lines(pull_request()) == {"app.py": frozenset({2})}


def test_findings_on_untouched_lines_are_dropped() -> None:
    on_added = tool_finding(line=2)
    on_context = tool_finding(line=1)
    in_other_file = tool_finding(file="other.py")
    assert restrict_to_changes([on_added, on_context, in_other_file], pull_request()) == (on_added,)


def test_build_report_merges_tools_and_deduplicates_across_them() -> None:
    bandit = tool_finding(source="bandit", severity=Severity.MEDIUM)
    semgrep = tool_finding(source="semgrep", severity=Severity.HIGH)
    ruff = tool_finding(source="ruff", category=Category.STYLE, severity=Severity.LOW)
    runs = [
        (ToolRun(tool="bandit", status=ToolStatus.OK, findings=1), [bandit]),
        (ToolRun(tool="semgrep", status=ToolStatus.OK, findings=1), [semgrep]),
        (ToolRun(tool="ruff", status=ToolStatus.OK, findings=1), [ruff]),
    ]
    report = build_report(pull_request(), runs)
    # Same line and category from two tools is one issue; the more severe one wins.
    assert report.findings == (semgrep, ruff)
    assert [r.tool for r in report.runs] == ["bandit", "semgrep", "ruff"]


def test_report_with_a_failed_tool_is_incomplete() -> None:
    ok = AnalysisReport(findings=(), runs=(ToolRun(tool="ruff", status=ToolStatus.OK),))
    failed = AnalysisReport(
        findings=(),
        runs=(
            ToolRun(tool="ruff", status=ToolStatus.OK),
            ToolRun(tool="mypy", status=ToolStatus.TIMED_OUT, detail="ran longer than 1s"),
        ),
    )
    assert not ok.incomplete
    assert failed.incomplete


def test_render_for_agents_lists_findings_and_tool_status() -> None:
    report = AnalysisReport(
        findings=(tool_finding(),),
        runs=(
            ToolRun(tool="bandit", status=ToolStatus.OK),
            ToolRun(tool="mypy", status=ToolStatus.FAILED, detail="mypy exited with 2"),
        ),
    )
    text = render_for_agents(report)
    assert "- app.py:2 [medium/security] bandit: B602: shell=True" in text
    assert "- mypy: failed: mypy exited with 2" in text
    assert "absence of a finding is not proof" in text


def test_render_for_agents_says_when_nothing_was_found() -> None:
    report = AnalysisReport(findings=(), runs=(ToolRun(tool="ruff", status=ToolStatus.OK),))
    assert "No findings on changed lines." in render_for_agents(report)
