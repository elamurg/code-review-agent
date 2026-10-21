from typing import Any

import pytest

from reviewer.domain.errors import DomainValidationError
from reviewer.domain.models import Category, Finding, Severity
from reviewer.domain.suppression import (
    SuppressionPolicy,
    apply_confidence_threshold,
    cap_by_severity,
    deduplicate,
    drop_tool_overlap,
    suppress,
)


def finding(**overrides: Any) -> Finding:
    fields: dict[str, Any] = {
        "file": "app.py",
        "line": 10,
        "severity": Severity.MEDIUM,
        "category": Category.CORRECTNESS,
        "message": "Possible bug.",
        "evidence": "x = y",
        "source": "correctness-agent",
        "confidence": 0.9,
        "prompt_version": "correctness-v1",
    }
    fields.update(overrides)
    return Finding(**fields)


def tool(**overrides: Any) -> Finding:
    return finding(source="ruff", confidence=1.0, prompt_version=None, **overrides)


# --- drop_tool_overlap ----------------------------------------------------------------


def test_drops_finding_at_same_location_and_category_as_tool() -> None:
    agent = finding()
    assert drop_tool_overlap([agent], [tool()]) == ()


@pytest.mark.parametrize(
    "tool_finding",
    [
        pytest.param(tool(line=11), id="different-line"),
        pytest.param(tool(file="other.py"), id="different-file"),
        pytest.param(tool(category=Category.STYLE), id="different-category"),
    ],
)
def test_keeps_finding_the_tool_did_not_report(tool_finding: Finding) -> None:
    agent = finding()
    assert drop_tool_overlap([agent], [tool_finding]) == (agent,)


def test_no_tool_findings_drops_nothing() -> None:
    agents = (finding(line=1), finding(line=2))
    assert drop_tool_overlap(agents, []) == agents


# --- apply_confidence_threshold -------------------------------------------------------


def test_threshold_is_inclusive() -> None:
    at, above, below = finding(confidence=0.7), finding(confidence=0.71), finding(confidence=0.69)
    assert apply_confidence_threshold([at, above, below], 0.7) == (at, above)


# --- deduplicate ----------------------------------------------------------------------


def test_duplicates_keep_the_most_severe() -> None:
    low = finding(severity=Severity.LOW, source="a")
    high = finding(severity=Severity.HIGH, source="b")
    assert deduplicate([low, high]) == (high,)


def test_equal_severity_keeps_the_most_confident() -> None:
    unsure = finding(confidence=0.75, source="a")
    sure = finding(confidence=0.95, source="b")
    assert deduplicate([unsure, sure]) == (sure,)


def test_same_line_different_category_is_not_a_duplicate() -> None:
    bug = finding(category=Category.CORRECTNESS)
    hole = finding(category=Category.SECURITY)
    assert deduplicate([bug, hole]) == (bug, hole)


def test_full_tie_is_broken_deterministically() -> None:
    a, b = finding(source="a-agent"), finding(source="b-agent")
    assert deduplicate([b, a]) == deduplicate([a, b]) == (a,)


# --- cap_by_severity ------------------------------------------------------------------


def test_cap_keeps_most_severe_first() -> None:
    low = finding(line=1, severity=Severity.LOW)
    critical = finding(line=2, severity=Severity.CRITICAL)
    medium = finding(line=3, severity=Severity.MEDIUM)
    high = finding(line=4, severity=Severity.HIGH)
    assert cap_by_severity([low, critical, medium, high], 2) == (critical, high)


def test_cap_of_zero_returns_nothing() -> None:
    assert cap_by_severity([finding()], 0) == ()


def test_cap_larger_than_input_returns_everything_sorted() -> None:
    low, high = finding(line=1, severity=Severity.LOW), finding(line=2, severity=Severity.HIGH)
    assert cap_by_severity([low, high], 10) == (high, low)


# --- suppress: the rules together -----------------------------------------------------


def test_threshold_runs_before_deduplication() -> None:
    # The severe duplicate is below the threshold. If deduplication ran first it would
    # win, then be dropped, and the valid medium finding would be lost with it.
    severe_but_unsure = finding(severity=Severity.CRITICAL, confidence=0.3, source="a")
    valid = finding(severity=Severity.MEDIUM, confidence=0.9, source="b")
    result = suppress([severe_but_unsure, valid], [], SuppressionPolicy(min_confidence=0.7))
    assert result == (valid,)


def test_suppress_applies_every_rule() -> None:
    overlapping = finding(line=1)
    unsure = finding(line=2, confidence=0.2)
    duplicate_low = finding(line=3, severity=Severity.LOW, source="a")
    duplicate_high = finding(line=3, severity=Severity.HIGH, source="b")
    extra = [finding(line=10 + n, severity=Severity.LOW) for n in range(5)]

    result = suppress(
        [overlapping, unsure, duplicate_low, duplicate_high, *extra],
        [tool(line=1)],
        SuppressionPolicy(min_confidence=0.7, max_findings=3),
    )

    assert result == (duplicate_high, extra[0], extra[1])


def test_suppressing_nothing_is_valid() -> None:
    assert suppress([], [], SuppressionPolicy()) == ()


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"min_confidence": 1.5}, id="confidence-above-1"),
        pytest.param({"min_confidence": -0.1}, id="confidence-below-0"),
        pytest.param({"max_findings": -1}, id="negative-cap"),
    ],
)
def test_invalid_policy_raises(fields: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        SuppressionPolicy(**fields)
