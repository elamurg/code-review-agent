"""Scoring a pipeline's findings against the golden labels. Pure functions, no I/O.

    precision          of the findings raised, how many match an issue a human raised
    recall             of the issues humans raised, how many a finding caught
    noise rate         findings per 100 changed lines
    tool overlap       agent findings that repeat a deterministic tool (target: zero)
    location accuracy  of the matched findings, how many cite the exact line

A finding matches an issue in the same file within LINE_TOLERANCE lines: reviewers often
comment on the first line of a statement while a tool reports the line it trips on.
Matching is one-to-one, so two findings cannot both claim the same issue.

Precision here is a lower bound. A finding that matches no human comment may still be
right; humans miss things too. That is why it is measured against human review, and why
the metric is called "matches a human" in the report, not "correct".
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from reviewer.domain.models import Category, Finding

LINE_TOLERANCE = 3
TOOL_SOURCES = frozenset({"ruff", "mypy", "bandit", "semgrep", "pip-audit"})

type IssueLocation = tuple[str, int]


@dataclass(frozen=True, slots=True)
class Match:
    finding: Finding
    issue: IssueLocation

    @property
    def exact(self) -> bool:
        return self.finding.line == self.issue[1]


@dataclass(frozen=True, slots=True, kw_only=True)
class ExampleScore:
    example_id: str
    findings: int
    issues: int
    matches: tuple[Match, ...]
    agent_findings: int
    tool_overlaps: int
    changed_lines: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Metrics:
    examples: int
    findings: int
    issues: int
    matched: int
    exact: int
    agent_findings: int
    tool_overlaps: int
    changed_lines: int
    silent_examples: int  # examples where the correct review says nothing
    silent_examples_with_findings: int

    @property
    def precision(self) -> float | None:
        return _ratio(self.matched, self.findings)

    @property
    def recall(self) -> float | None:
        return _ratio(self.matched, self.issues)

    @property
    def noise_rate(self) -> float | None:
        """Findings per 100 changed lines."""
        rate = _ratio(self.findings, self.changed_lines)
        return None if rate is None else rate * 100

    @property
    def tool_overlap_rate(self) -> float | None:
        """None when no agent findings exist: tools trivially overlap themselves."""
        return _ratio(self.tool_overlaps, self.agent_findings)

    @property
    def location_accuracy(self) -> float | None:
        return _ratio(self.exact, self.matched)


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def match_findings(
    findings: Sequence[Finding], issues: Sequence[IssueLocation], tolerance: int = LINE_TOLERANCE
) -> tuple[Match, ...]:
    """Pair findings with issues one-to-one, closest pairs first.

    Greedy on distance: an exact hit is always paired before a near one, so a nearby
    finding cannot steal an issue from the finding that cites it exactly.
    """
    candidates = sorted(
        (abs(f.line - line), fi, ii)
        for fi, f in enumerate(findings)
        for ii, (path, line) in enumerate(issues)
        if f.file == path and abs(f.line - line) <= tolerance
    )
    used_findings: set[int] = set()
    used_issues: set[int] = set()
    matches = []
    for _, fi, ii in candidates:
        if fi in used_findings or ii in used_issues:
            continue
        used_findings.add(fi)
        used_issues.add(ii)
        matches.append(Match(findings[fi], issues[ii]))
    return tuple(matches)


def count_tool_overlaps(
    findings: Iterable[Finding], tool_findings: Iterable[Finding]
) -> tuple[int, int]:
    """(agent findings, how many of them repeat a tool: same file, line and category)."""
    reported: set[tuple[str, int, Category]] = {(t.file, t.line, t.category) for t in tool_findings}
    agent = [f for f in findings if f.source not in TOOL_SOURCES]
    return len(agent), sum((f.file, f.line, f.category) in reported for f in agent)


def score_example(
    example_id: str,
    findings: Sequence[Finding],
    issues: Sequence[IssueLocation],
    *,
    tool_findings: Sequence[Finding],
    changed_lines: int,
    tolerance: int = LINE_TOLERANCE,
) -> ExampleScore:
    agent, overlaps = count_tool_overlaps(findings, tool_findings)
    return ExampleScore(
        example_id=example_id,
        findings=len(findings),
        issues=len(issues),
        matches=match_findings(findings, issues, tolerance),
        agent_findings=agent,
        tool_overlaps=overlaps,
        changed_lines=changed_lines,
    )


def aggregate(scores: Sequence[ExampleScore]) -> Metrics:
    """Micro-average: sum the counts, then divide. Large PRs weigh more, as they should;
    averaging per-example ratios would let a one-line PR count as much as a big one."""
    return Metrics(
        examples=len(scores),
        findings=sum(s.findings for s in scores),
        issues=sum(s.issues for s in scores),
        matched=sum(len(s.matches) for s in scores),
        exact=sum(m.exact for s in scores for m in s.matches),
        agent_findings=sum(s.agent_findings for s in scores),
        tool_overlaps=sum(s.tool_overlaps for s in scores),
        changed_lines=sum(s.changed_lines for s in scores),
        silent_examples=sum(1 for s in scores if s.issues == 0),
        silent_examples_with_findings=sum(1 for s in scores if s.issues == 0 and s.findings),
    )
