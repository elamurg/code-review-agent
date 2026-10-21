"""ruff and mypy, normalised into Finding objects.

Both run with settings we control rather than the repository's own config. That keeps
results comparable across repositories, and stops untrusted config from loading code:
a mypy config can name plugins, and plugins are arbitrary Python.
"""

import json
from collections.abc import Sequence
from pathlib import Path

from reviewer.analysis.base import (
    DEFAULT_TIMEOUT_SECONDS,
    SourceReader,
    changed_files,
    evidence,
    load_json,
    relative_path,
    run_checked,
)
from reviewer.analysis.execution import Executor, ToolFailed
from reviewer.domain.models import Category, Finding, PullRequest, Severity

# Rules that point at bugs, not taste: syntax errors, pyflakes, bugbear.
RUFF_RULES = "E9,F,B"
RUFF_TARGET_VERSION = "py313"
# Unused names are real but minor; everything else in the selection can break at runtime.
_RUFF_LOW = ("F401", "F841", "F811")
_RUFF_HIGH = ("E9", "F821", "F822", "F823", "F63", "F7")


class RuffAnalyser:
    name = "ruff"

    def select(self, pull_request: PullRequest) -> tuple[str, ...]:
        return changed_files(pull_request, ".py")

    def command(self, files: Sequence[str]) -> list[str]:
        return [
            "ruff",
            "check",
            "--isolated",  # ignore the repository's config
            "--select",
            RUFF_RULES,
            # Without a config, ruff assumes an old Python and flags newer builtins such as
            # BaseExceptionGroup as undefined. Assume the newest instead.
            "--target-version",
            RUFF_TARGET_VERSION,
            "--output-format",
            "json",
            "--no-cache",
            "--exit-zero",
            "--",
            *files,
        ]

    def analyse(
        self,
        files: Sequence[str],
        *,
        checkout: Path,
        executor: Executor,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[Finding, ...]:
        result = run_checked(
            executor, self.command(files), checkout=checkout, timeout=timeout, ok_codes=(0,)
        )
        return parse_ruff(load_json(result), result.workdir, SourceReader(checkout))


def parse_ruff(output: object, workdir: str, source: SourceReader) -> tuple[Finding, ...]:
    if not isinstance(output, list):
        raise ToolFailed("ruff output is not a list")
    findings = []
    for item in output:
        code = item.get("code") or "E999"  # ruff reports syntax errors without a code
        line = item["location"]["row"]
        if line < 1:
            continue  # file-level problems have no line to anchor to
        path = relative_path(item["filename"], workdir)
        findings.append(
            Finding(
                file=path,
                line=line,
                severity=_ruff_severity(code),
                category=Category.STYLE if code.startswith(_RUFF_LOW) else Category.CORRECTNESS,
                message=f"{code}: {item['message']}",
                evidence=evidence(item["message"], source.line(path, line)),
                source="ruff",
                confidence=1.0,
            )
        )
    return tuple(findings)


def _ruff_severity(code: str) -> Severity:
    if code.startswith(_RUFF_HIGH):
        return Severity.HIGH
    if code.startswith(_RUFF_LOW):
        return Severity.LOW
    return Severity.MEDIUM


class MypyAnalyser:
    name = "mypy"

    def select(self, pull_request: PullRequest) -> tuple[str, ...]:
        return changed_files(pull_request, ".py")

    def command(self, files: Sequence[str]) -> list[str]:
        return [
            "mypy",
            "--config-file=",  # empty: never read the repository's config or plugins
            "--output",
            "json",
            "--no-error-summary",
            "--ignore-missing-imports",
            # Check each changed file on its own; imported modules become Any. Following
            # imports into a partial or unbuildable checkout reports errors about modules
            # mypy only half-sees, which is noise about the setup, not the PR.
            "--follow-imports=skip",
            "--explicit-package-bases",
            "--no-incremental",
            "--cache-dir=/dev/null",
            "--",
            *files,
        ]

    def analyse(
        self,
        files: Sequence[str],
        *,
        checkout: Path,
        executor: Executor,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[Finding, ...]:
        # 0: clean, 1: type errors found, 2: mypy itself failed.
        result = run_checked(
            executor, self.command(files), checkout=checkout, timeout=timeout, ok_codes=(0, 1)
        )
        return parse_mypy(result.stdout, result.workdir, SourceReader(checkout))


def parse_mypy(stdout: str, workdir: str, source: SourceReader) -> tuple[Finding, ...]:
    """mypy's JSON output is one object per line, not one document."""
    findings = []
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolFailed(f"mypy printed a line that is not JSON: {raw!r}") from exc
        if item.get("severity") != "error" or item.get("line", 0) < 1:
            continue  # notes explain an error; they are not findings of their own
        path = relative_path(item["file"], workdir)
        code = item.get("code") or "misc"
        findings.append(
            Finding(
                file=path,
                line=item["line"],
                severity=Severity.MEDIUM,
                category=Category.CORRECTNESS,
                message=f"{code}: {item['message']}",
                evidence=evidence(item["message"], source.line(path, item["line"])),
                source="mypy",
                confidence=1.0,
            )
        )
    return tuple(findings)
