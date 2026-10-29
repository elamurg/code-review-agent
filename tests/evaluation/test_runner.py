import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from reviewer.analysis.execution import LocalExecutor
from reviewer.domain.models import Category, Finding, Severity
from reviewer.evaluation.golden import GoldenExample, load_golden
from reviewer.evaluation.runner import (
    PipelineOptions,
    PipelineOutput,
    deterministic_pipeline,
    evaluate,
    render_markdown,
    report_data,
    tool_versions,
    write_report,
)

from .conftest import ExampleWriter

CREATED_AT = datetime(2026, 10, 21, 12, 30, 5, tzinfo=UTC)


def fake_pipeline(example: GoldenExample) -> PipelineOutput:
    """Finds the issue on line 3, plus one false alarm on line 1."""
    findings = tuple(
        Finding(
            file="app.py",
            line=line,
            severity=Severity.HIGH,
            category=Category.CORRECTNESS,
            message="m",
            evidence="e",
            source="correctness-agent",
            confidence=0.9,
            prompt_version="correctness-v1",
        )
        for line in (3, 1)
    )
    return PipelineOutput(findings=findings, tool_findings=())


def test_evaluate_scores_every_example(write_example: ExampleWriter) -> None:
    evaluation = evaluate(load_golden(write_example()), fake_pipeline)
    assert evaluation.metrics.precision == 0.5
    assert evaluation.metrics.recall == 1.0
    assert evaluation.metrics.location_accuracy == 1.0


def test_report_records_what_produced_it(write_example: ExampleWriter) -> None:
    evaluation = evaluate(load_golden(write_example()), fake_pipeline)
    data = report_data(
        evaluation,
        pipeline="fake",
        options=PipelineOptions(),
        dataset="abc123",
        commit="deadbeef",
        created_at=CREATED_AT,
    )
    assert data["git_commit"] == "deadbeef"
    assert data["dataset"] == {"fingerprint": "abc123", "examples": 1}
    assert data["prompt_versions"] == ["correctness-v1"]
    assert data["metrics"]["precision"] == 0.5
    (example,) = data["examples"]
    assert [f["matches_human"] for f in example["findings"]] == [True, False]


def test_write_report_uses_a_timestamped_name(write_example: ExampleWriter, tmp_path: Path) -> None:
    evaluation = evaluate(load_golden(write_example()), fake_pipeline)
    data = report_data(
        evaluation,
        pipeline="fake",
        options=PipelineOptions(),
        dataset="abc123",
        commit="deadbeef",
        created_at=CREATED_AT,
    )
    json_path, markdown_path = write_report(data, tmp_path / "out")
    assert json_path.name == "2026-10-21T12-30-05Z-fake.json"
    assert json.loads(json_path.read_text())["pipeline"] == "fake"
    assert "| Precision (matches a human) | 50.0% (1/2) |" in markdown_path.read_text()


def test_markdown_shows_undefined_rates_as_na(write_example: ExampleWriter) -> None:
    empty = evaluate(
        load_golden(write_example()), lambda e: PipelineOutput(findings=(), tool_findings=())
    )
    data = report_data(
        empty,
        pipeline="silent",
        options=PipelineOptions(),
        dataset="x",
        commit="y",
        created_at=CREATED_AT,
    )
    assert "| Precision (matches a human) | n/a (0/0) |" in render_markdown(data)


def test_deterministic_pipeline_runs_the_analysers(write_example: ExampleWriter) -> None:
    evaluation = evaluate(load_golden(write_example()), deterministic_pipeline(PipelineOptions()))
    (result,) = evaluation.results
    found = {(f.line, f.source) for f in result.output.findings}
    assert (3, "ruff") in found  # F821 undefined name, on the commented line
    assert evaluation.metrics.recall == 1.0


def test_tool_versions_asks_each_tool_and_marks_missing_ones() -> None:
    versions = tool_versions(LocalExecutor(), [sys.executable, "no-such-analyser-xyz"])
    assert versions[sys.executable].startswith("Python 3.")
    assert versions["no-such-analyser-xyz"] == "unavailable"


def test_report_records_tool_versions(write_example: ExampleWriter) -> None:
    evaluation = evaluate(load_golden(write_example()), fake_pipeline)
    data = report_data(
        evaluation,
        pipeline="fake",
        options=PipelineOptions(),
        dataset="x",
        commit="y",
        created_at=CREATED_AT,
        tools={"ruff": "ruff 0.17.1"},
    )
    assert data["tool_versions"] == {"ruff": "ruff 0.17.1"}
    assert "- Tool versions: ruff 0.17.1" in render_markdown(data)
