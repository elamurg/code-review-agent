"""Deciding what is worth a human's attention. Pure functions, no I/O.

This is where the product's core value lives: staying silent about the wrong things.
Agents propose findings; these rules decide which ones survive.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from reviewer.domain.errors import DomainValidationError
from reviewer.domain.models import Category, Finding


@dataclass(frozen=True, slots=True, kw_only=True)
class SuppressionPolicy:
    min_confidence: float = 0.7
    max_findings: int = 10

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_confidence <= 1.0:
            raise DomainValidationError("min_confidence must be between 0 and 1")
        if self.max_findings < 0:
            raise DomainValidationError("max_findings must be >= 0")


def sort_key(finding: Finding) -> tuple[int, float, str, int, str]:
    """Most severe first, then most confident, then by location for a stable order."""
    return (-finding.severity.rank, -finding.confidence, finding.file, finding.line, finding.source)


def drop_tool_overlap(
    findings: Iterable[Finding], tool_findings: Iterable[Finding]
) -> tuple[Finding, ...]:
    """Drop findings a deterministic tool already reported: same file, line and category.

    Repeating the linter is the fastest way for a review bot to get muted.
    """
    reported: set[tuple[str, int, Category]] = {(t.file, t.line, t.category) for t in tool_findings}
    return tuple(f for f in findings if (f.file, f.line, f.category) not in reported)


def apply_confidence_threshold(
    findings: Iterable[Finding], min_confidence: float
) -> tuple[Finding, ...]:
    return tuple(f for f in findings if f.confidence >= min_confidence)


def deduplicate(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Keep one finding per file, line and category: the most severe, then most confident.

    Two agents flagging the same line for the same reason is one issue, not two.
    Output keeps the order in which each surviving location was first seen.
    """
    best: dict[tuple[str, int, Category], Finding] = {}
    for finding in findings:
        key = (finding.file, finding.line, finding.category)
        current = best.get(key)
        if current is None or sort_key(finding) < sort_key(current):
            best[key] = finding
    return tuple(best.values())


def cap_by_severity(findings: Iterable[Finding], max_findings: int) -> tuple[Finding, ...]:
    """Keep at most max_findings, most severe first. If everything is important, nothing is."""
    return tuple(sorted(findings, key=sort_key)[:max_findings])


def suppress(
    findings: Iterable[Finding],
    tool_findings: Iterable[Finding],
    policy: SuppressionPolicy,
) -> tuple[Finding, ...]:
    """Run every rule in order and return what is worth posting, most severe first.

    The threshold runs before deduplication on purpose: otherwise a severe but
    low-confidence duplicate could win, then be dropped, taking a valid finding with it.
    """
    remaining = drop_tool_overlap(findings, tool_findings)
    remaining = apply_confidence_threshold(remaining, policy.min_confidence)
    remaining = deduplicate(remaining)
    return cap_by_severity(remaining, policy.max_findings)
