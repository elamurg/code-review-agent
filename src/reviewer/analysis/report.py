"""One normalised report from all analysers, ready to hand to agents as context."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from reviewer.domain.models import Finding, LineKind, PullRequest
from reviewer.domain.suppression import deduplicate, sort_key


class ToolStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"  # no relevant files changed
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolRun:
    tool: str
    status: ToolStatus
    files: tuple[str, ...] = ()
    findings: int = 0
    detail: str = ""
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisReport:
    findings: tuple[Finding, ...]
    runs: tuple[ToolRun, ...]

    @property
    def incomplete(self) -> bool:
        """True if any tool failed, so silence may mean "not checked" rather than "clean"."""
        return any(r.status in (ToolStatus.FAILED, ToolStatus.TIMED_OUT) for r in self.runs)


def added_lines(pull_request: PullRequest) -> dict[str, frozenset[int]]:
    """New-side line numbers of every added line, per file: the lines this PR wrote."""
    return {
        f.path: frozenset(
            line.new_number
            for hunk in f.hunks
            for line in hunk.lines
            if line.kind is LineKind.ADDED and line.new_number is not None
        )
        for f in pull_request.files
    }


def restrict_to_changes(
    findings: Iterable[Finding], pull_request: PullRequest
) -> tuple[Finding, ...]:
    """Keep only findings on lines the PR added.

    Analysers see whole files, so they also report problems that were there before the
    PR. Those are not this author's doing, and commenting on them is noise.
    """
    lines = added_lines(pull_request)
    return tuple(f for f in findings if f.line in lines.get(f.file, frozenset()))


def build_report(
    pull_request: PullRequest, results: Sequence[tuple[ToolRun, Sequence[Finding]]]
) -> AnalysisReport:
    """Merge every tool's findings, keep the ones on changed lines, and deduplicate.

    Deduplication matters across tools: bandit and semgrep can flag the same line for
    the same reason, and that is one issue.
    """
    merged = [finding for _, findings in results for finding in findings]
    kept = deduplicate(restrict_to_changes(merged, pull_request))
    return AnalysisReport(
        findings=tuple(sorted(kept, key=sort_key)),
        runs=tuple(run for run, _ in results),
    )


def render_for_agents(report: AnalysisReport) -> str:
    """Plain text an agent can read: what the tools found, and which tools did not run."""
    lines = ["Deterministic analysis of this pull request. Do not repeat these findings."]
    if report.findings:
        lines.append("")
        lines.extend(
            f"- {f.file}:{f.line} [{f.severity}/{f.category}] {f.source}: {f.message}"
            for f in report.findings
        )
    else:
        lines.append("No findings on changed lines.")
    lines.append("")
    lines.append("Tools:")
    for run in report.runs:
        detail = f": {run.detail}" if run.detail else ""
        lines.append(f"- {run.tool}: {run.status}{detail}")
    if report.incomplete:
        lines.append("Some tools did not complete, so the absence of a finding is not proof.")
    return "\n".join(lines)
