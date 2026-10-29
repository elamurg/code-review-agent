"""Run a pipeline configuration over the golden set and write a timestamped report.

    python -m reviewer.evaluation.runner --pipeline deterministic
    python -m reviewer.evaluation.runner --pipeline deterministic --sandbox --network

Every later change is judged against a recorded baseline, so a report records exactly
what produced it: the pipeline, its options, the code's git commit, the dataset's
fingerprint, and every prompt version that contributed a finding.
"""

import argparse
import json
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reviewer.analysis.execution import AnalysisError, Executor, LocalExecutor
from reviewer.analysis.pipeline import analyse, default_analysers
from reviewer.analysis.report import AnalysisReport
from reviewer.analysis.sandbox import DockerExecutor
from reviewer.domain.models import Finding
from reviewer.evaluation.golden import DEFAULT_ROOT, GoldenExample, fingerprint, load_golden
from reviewer.evaluation.metrics import (
    LINE_TOLERANCE,
    ExampleScore,
    Metrics,
    aggregate,
    score_example,
)

DEFAULT_OUT = Path("reports/evaluation")


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineOutput:
    findings: tuple[Finding, ...]  # what the pipeline would post
    tool_findings: tuple[Finding, ...]  # what the deterministic tools reported
    analysis: AnalysisReport | None = None


type Pipeline = Callable[[GoldenExample], PipelineOutput]


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineOptions:
    sandbox: bool = False
    network: bool = False
    timeout: float = 120.0


def make_executor(options: PipelineOptions) -> Executor:
    return DockerExecutor() if options.sandbox else LocalExecutor()


ANALYSER_TOOLS = ("ruff", "mypy", "bandit", "semgrep", "pip-audit")


def tool_versions(executor: Executor, tools: Sequence[str] = ANALYSER_TOOLS) -> dict[str, str]:
    """Each tool's version, asked of the tool itself through the same executor.

    Results depend on tool versions as much as on our code: a newer ruff can find more.
    Recording them is what lets a change in the numbers be traced to its cause.
    """
    versions: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as empty:
        for tool in tools:
            try:
                result = executor.run([tool, "--version"], checkout=Path(empty), timeout=60)
            except AnalysisError:
                versions[tool] = "unavailable"
                continue
            lines = result.stdout.strip().splitlines()
            versions[tool] = lines[0] if result.returncode == 0 and lines else "unavailable"
    return versions


def deterministic_pipeline(options: PipelineOptions) -> Pipeline:
    """The analysers alone, no model: the baseline every agent must beat."""
    executor = make_executor(options)
    analysers = default_analysers(sandboxed=options.sandbox, include_network=options.network)

    def run(example: GoldenExample) -> PipelineOutput:
        report = analyse(
            example.pull_request,
            example.checkout,
            executor,
            analysers,
            timeout=options.timeout,
        )
        return PipelineOutput(
            findings=report.findings, tool_findings=report.findings, analysis=report
        )

    return run


PIPELINES: dict[str, Callable[[PipelineOptions], Pipeline]] = {
    "deterministic": deterministic_pipeline,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class ExampleResult:
    example: GoldenExample
    output: PipelineOutput
    score: ExampleScore


@dataclass(frozen=True, slots=True, kw_only=True)
class Evaluation:
    results: tuple[ExampleResult, ...]
    metrics: Metrics


def evaluate(examples: Sequence[GoldenExample], pipeline: Pipeline) -> Evaluation:
    results = []
    for example in examples:
        output = pipeline(example)
        score = score_example(
            example.id,
            output.findings,
            [(issue.path, issue.line) for issue in example.issues],
            tool_findings=output.tool_findings,
            changed_lines=example.changed_lines,
        )
        results.append(ExampleResult(example=example, output=output, score=score))
    return Evaluation(results=tuple(results), metrics=aggregate([r.score for r in results]))


def git_commit(cwd: Path | None = None) -> str:
    """The commit the evaluated code came from, marked dirty if there are local changes."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


def report_data(
    evaluation: Evaluation,
    *,
    pipeline: str,
    options: PipelineOptions,
    dataset: str,
    commit: str,
    created_at: datetime,
    tools: dict[str, str] | None = None,
) -> dict[str, Any]:
    metrics = evaluation.metrics
    prompt_versions = sorted(
        {
            f.prompt_version
            for r in evaluation.results
            for f in r.output.findings
            if f.prompt_version is not None
        }
    )
    return {
        "pipeline": pipeline,
        "created_at": created_at.isoformat(timespec="seconds"),
        "git_commit": commit,
        "dataset": {"fingerprint": dataset, "examples": metrics.examples},
        "options": {
            "sandbox": options.sandbox,
            "network": options.network,
            "timeout": options.timeout,
            "line_tolerance": LINE_TOLERANCE,
        },
        "prompt_versions": prompt_versions,
        "tool_versions": tools or {},
        "metrics": {
            "precision": metrics.precision,
            "recall": metrics.recall,
            "noise_rate_per_100_lines": metrics.noise_rate,
            "tool_overlap_rate": metrics.tool_overlap_rate,
            "location_accuracy": metrics.location_accuracy,
            "counts": {
                "findings": metrics.findings,
                "issues": metrics.issues,
                "matched": metrics.matched,
                "exact": metrics.exact,
                "agent_findings": metrics.agent_findings,
                "tool_overlaps": metrics.tool_overlaps,
                "changed_lines": metrics.changed_lines,
                "silent_examples": metrics.silent_examples,
                "silent_examples_with_findings": metrics.silent_examples_with_findings,
            },
        },
        "examples": [_example_data(r) for r in evaluation.results],
    }


def _example_data(result: ExampleResult) -> dict[str, Any]:
    matched = {id(m.finding) for m in result.score.matches}
    analysis = result.output.analysis
    return {
        "id": result.example.id,
        "url": result.example.url,
        "changed_lines": result.example.changed_lines,
        "issues": [
            {"path": i.path, "line": i.line, "category": i.category, "url": i.url}
            for i in result.example.issues
        ],
        "findings": [
            {
                "file": f.file,
                "line": f.line,
                "severity": f.severity,
                "category": f.category,
                "source": f.source,
                "message": f.message,
                "prompt_version": f.prompt_version,
                "matches_human": id(f) in matched,
            }
            for f in result.output.findings
        ],
        "tools": [
            {"tool": run.tool, "status": run.status, "detail": run.detail}
            for run in (analysis.runs if analysis else ())
        ],
    }


def render_markdown(data: dict[str, Any]) -> str:
    metrics = data["metrics"]
    counts = metrics["counts"]

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.1%}"

    noise = metrics["noise_rate_per_100_lines"]
    lines = [
        f"# Evaluation: {data['pipeline']}",
        "",
        f"- Run at: {data['created_at']}",
        f"- Code: `{data['git_commit']}`",
        f"- Dataset: `{data['dataset']['fingerprint']}` ({data['dataset']['examples']} examples)",
        f"- Options: {data['options']}",
        f"- Prompt versions: {', '.join(data['prompt_versions']) or 'none'}",
        f"- Tool versions: {_tool_versions_text(data['tool_versions'])}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Precision (matches a human) | {pct(metrics['precision'])} "
        f"({counts['matched']}/{counts['findings']}) |",
        f"| Recall against humans | {pct(metrics['recall'])} "
        f"({counts['matched']}/{counts['issues']}) |",
        f"| Noise rate | {'n/a' if noise is None else f'{noise:.2f}'} findings per 100 changed "
        f"lines ({counts['findings']}/{counts['changed_lines']}) |",
        f"| Tool overlap | {pct(metrics['tool_overlap_rate'])} |",
        f"| Location accuracy | {pct(metrics['location_accuracy'])} |",
        f"| Silent PRs with findings | {counts['silent_examples_with_findings']}"
        f"/{counts['silent_examples']} |",
        "",
        "| Example | Changed lines | Issues | Findings | Matched | Tools not OK |",
        "|---|---|---|---|---|---|",
    ]
    for example in data["examples"]:
        failed = [t["tool"] for t in example["tools"] if t["status"] not in ("ok", "skipped")]
        lines.append(
            f"| [{example['id']}]({example['url']}) | {example['changed_lines']} "
            f"| {len(example['issues'])} | {len(example['findings'])} "
            f"| {sum(f['matches_human'] for f in example['findings'])} "
            f"| {', '.join(failed) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def _tool_versions_text(versions: dict[str, str]) -> str:
    # Most tools print their own name ("ruff 0.17.2"); semgrep prints only "1.182.1".
    labelled = [v if v.startswith(tool) else f"{tool} {v}" for tool, v in versions.items()]
    return ", ".join(labelled) or "not recorded"


def write_report(data: dict[str, Any], out: Path) -> tuple[Path, Path]:
    created_at = datetime.fromisoformat(data["created_at"])
    stamp = created_at.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{stamp}-{data['pipeline']}.json"
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(data, indent=2) + "\n")
    markdown_path.write_text(render_markdown(data))
    return json_path, markdown_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a pipeline on the golden set.")
    parser.add_argument("--pipeline", choices=sorted(PIPELINES), default="deterministic")
    parser.add_argument("--golden", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sandbox", action="store_true", help="run tools in Docker")
    parser.add_argument("--network", action="store_true", help="include pip-audit")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    options = PipelineOptions(sandbox=args.sandbox, network=args.network, timeout=args.timeout)
    created_at = datetime.now(UTC).replace(microsecond=0)
    evaluation = evaluate(load_golden(args.golden), PIPELINES[args.pipeline](options))
    data = report_data(
        evaluation,
        pipeline=args.pipeline,
        options=options,
        dataset=fingerprint(args.golden),
        commit=git_commit(),
        created_at=created_at,
        tools=tool_versions(make_executor(options)),
    )
    json_path, markdown_path = write_report(data, args.out)
    print(render_markdown(data))
    print(f"Wrote {json_path} and {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
