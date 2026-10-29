from typing import Any

import pytest

from reviewer.domain.models import Category, Finding, Severity
from reviewer.evaluation.metrics import (
    aggregate,
    count_tool_overlaps,
    match_findings,
    score_example,
)


def finding(file: str = "app.py", line: int = 10, **overrides: Any) -> Finding:
    fields: dict[str, Any] = {
        "file": file,
        "line": line,
        "severity": Severity.MEDIUM,
        "category": Category.CORRECTNESS,
        "message": "m",
        "evidence": "e",
        "source": "correctness-agent",
        "confidence": 0.9,
    }
    fields.update(overrides)
    return Finding(**fields)


# --- matching -------------------------------------------------------------------------


def test_exact_and_near_matches_within_tolerance() -> None:
    exact, near, far = finding(line=10), finding(line=23), finding(line=40)
    matches = match_findings([exact, near, far], [("app.py", 10), ("app.py", 20)])
    assert [(m.finding, m.issue, m.exact) for m in matches] == [
        (exact, ("app.py", 10), True),
        (near, ("app.py", 20), False),
    ]


def test_different_file_never_matches() -> None:
    assert match_findings([finding(file="a.py")], [("b.py", 10)]) == ()


def test_matching_is_one_to_one_and_closest_first() -> None:
    # Both findings are near the one issue; only the exact one may claim it.
    near, exact = finding(line=12), finding(line=10)
    matches = match_findings([near, exact], [("app.py", 10)])
    assert [m.finding for m in matches] == [exact]


def test_one_finding_cannot_claim_two_issues() -> None:
    matches = match_findings([finding(line=10)], [("app.py", 9), ("app.py", 11)])
    assert len(matches) == 1


# --- tool overlap ---------------------------------------------------------------------


def test_tool_overlap_counts_only_agent_findings() -> None:
    tool = finding(source="ruff")
    repeat = finding(source="security-agent")
    new = finding(line=50, source="security-agent")
    assert count_tool_overlaps([tool, repeat, new], [tool]) == (2, 1)


# --- a hand-computed example ----------------------------------------------------------


def test_aggregate_against_a_hand_computed_example() -> None:
    """Three examples, computed by hand:

    A: 200 changed lines, issues at x.py:10 and x.py:50.
       Findings: x.py:10 (exact), x.py:52 (near), x.py:90 (miss). 3 findings, 2 matched.
       The finding at x.py:90 repeats what bandit already reported there.
    B: 100 changed lines, nothing to say. Findings: y.py:5. 1 finding, 0 matched.
    C: 100 changed lines, nothing to say, no findings.

    Totals: 4 findings, 2 issues, 2 matched (1 exact), 400 changed lines.
      precision          2 / 4             = 0.5
      recall             2 / 2             = 1.0
      noise rate         4 / 400 * 100     = 1.0 per 100 lines
      location accuracy  1 / 2             = 0.5
      tool overlap       1 / 4 agent findings = 0.25 (only x.py:90 repeats a tool)
      silent examples    2, of which 1 got findings
    """
    tool_at_90 = finding("x.py", 90, source="bandit")
    a = score_example(
        "A",
        [finding("x.py", 10), finding("x.py", 52), finding("x.py", 90)],
        [("x.py", 10), ("x.py", 50)],
        tool_findings=[tool_at_90],
        changed_lines=200,
    )
    b = score_example("B", [finding("y.py", 5)], [], tool_findings=[], changed_lines=100)
    c = score_example("C", [], [], tool_findings=[], changed_lines=100)

    metrics = aggregate([a, b, c])

    assert metrics.precision == 0.5
    assert metrics.recall == 1.0
    assert metrics.noise_rate == 1.0
    assert metrics.location_accuracy == 0.5
    assert (metrics.agent_findings, metrics.tool_overlaps) == (4, 1)
    assert metrics.tool_overlap_rate == pytest.approx(0.25)
    assert (metrics.silent_examples, metrics.silent_examples_with_findings) == (2, 1)


def test_rates_are_none_when_undefined() -> None:
    metrics = aggregate([score_example("E", [], [], tool_findings=[], changed_lines=0)])
    assert metrics.precision is None  # no findings: precision is undefined, not zero
    assert metrics.recall is None
    assert metrics.noise_rate is None
    assert metrics.tool_overlap_rate is None
    assert metrics.location_accuracy is None


def test_deterministic_findings_have_no_tool_overlap_rate() -> None:
    tool = finding(source="ruff")
    score = score_example("T", [tool], [], tool_findings=[tool], changed_lines=10)
    assert aggregate([score]).tool_overlap_rate is None
