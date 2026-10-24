"""Run every analyser over a pull request's checkout and build one report.

No model is involved anywhere in here. This alone is already a useful product.
"""

import time
from collections.abc import Sequence
from pathlib import Path

from reviewer.analysis.base import DEFAULT_TIMEOUT_SECONDS, Analyser
from reviewer.analysis.execution import AnalysisError, Executor, ToolTimeout
from reviewer.analysis.linters import MypyAnalyser, RuffAnalyser
from reviewer.analysis.report import AnalysisReport, ToolRun, ToolStatus, build_report
from reviewer.analysis.sandbox import SANDBOX_SEMGREP_RULES
from reviewer.analysis.security import BanditAnalyser, PipAuditAnalyser, SemgrepAnalyser
from reviewer.domain.models import Finding, PullRequest


def default_analysers(
    *, sandboxed: bool = False, include_network: bool = True
) -> tuple[Analyser, ...]:
    """Every analyser. pip-audit is the only one that needs network access."""
    analysers: list[Analyser] = [
        RuffAnalyser(),
        MypyAnalyser(),
        BanditAnalyser(),
        SemgrepAnalyser(SANDBOX_SEMGREP_RULES if sandboxed else None),
    ]
    if include_network:
        analysers.append(PipAuditAnalyser())
    return tuple(analysers)


def analyse(
    pull_request: PullRequest,
    checkout: Path,
    executor: Executor,
    analysers: Sequence[Analyser],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> AnalysisReport:
    """One tool failing never stops the others: its failure is recorded in the report,
    so a silent report can be told apart from an unchecked one."""
    results: list[tuple[ToolRun, Sequence[Finding]]] = []
    for analyser in analysers:
        files = analyser.select(pull_request)
        if not files:
            results.append((ToolRun(tool=analyser.name, status=ToolStatus.SKIPPED), ()))
            continue
        started = time.monotonic()
        findings: Sequence[Finding] = ()
        status, detail = ToolStatus.OK, ""
        try:
            findings = analyser.analyse(
                files, checkout=checkout, executor=executor, timeout=timeout
            )
        except ToolTimeout as exc:
            status, detail = ToolStatus.TIMED_OUT, str(exc)
        except AnalysisError as exc:
            status, detail = ToolStatus.FAILED, str(exc)
        run = ToolRun(
            tool=analyser.name,
            status=status,
            files=tuple(files),
            findings=len(findings),
            detail=detail,
            duration_seconds=time.monotonic() - started,
        )
        results.append((run, findings))
    return build_report(pull_request, results)
