"""What every analyser has in common: choosing files, running, and normalising output."""

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from reviewer.analysis.execution import Executor, ToolFailed, ToolResult
from reviewer.domain.models import FileStatus, Finding, PullRequest

DEFAULT_TIMEOUT_SECONDS = 120.0


class Analyser(Protocol):
    name: str

    def select(self, pull_request: PullRequest) -> tuple[str, ...]:
        """The changed files this analyser should look at. Empty means skip it."""
        ...

    def analyse(
        self,
        files: Sequence[str],
        *,
        checkout: Path,
        executor: Executor,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[Finding, ...]: ...


def changed_files(pull_request: PullRequest, suffix: str) -> tuple[str, ...]:
    """Paths of files that still exist after the PR and end with suffix."""
    return tuple(
        f.path
        for f in pull_request.files
        if f.status is not FileStatus.DELETED and not f.is_binary and f.path.endswith(suffix)
    )


def run_checked(
    executor: Executor,
    argv: Sequence[str],
    *,
    checkout: Path,
    timeout: float,
    ok_codes: Sequence[int],
    network: bool = False,
    stdin: str | None = None,
) -> ToolResult:
    """Run a tool and fail loudly on any exit code that means it crashed.

    Linters exit non-zero when they find something, so "success" is a set of codes.
    """
    result = executor.run(argv, checkout=checkout, timeout=timeout, network=network, stdin=stdin)
    if result.returncode not in ok_codes:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise ToolFailed(f"{argv[0]} exited with {result.returncode}: {detail}")
    return result


def load_json(result: ToolResult) -> object:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ToolFailed(f"{result.argv[0]} did not print valid JSON: {exc}") from exc


def relative_path(raw: str, workdir: str) -> str:
    """Turn a path a tool printed into a repo-relative path."""
    if os.path.isabs(raw):
        try:
            return str(PurePosixPath(raw).relative_to(workdir))
        except ValueError:
            # macOS reports /private/var/... for /var/...; compare resolved paths.
            return str(Path(raw).resolve().relative_to(Path(workdir).resolve()))
    return raw.removeprefix("./")


@dataclass
class SourceReader:
    """Reads source lines from the checkout, for a finding's evidence."""

    checkout: Path

    def __post_init__(self) -> None:
        self._cache: dict[str, list[str]] = {}

    def line(self, path: str, number: int) -> str:
        if path not in self._cache:
            try:
                text = (self.checkout / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            self._cache[path] = text.split("\n")
        lines = self._cache[path]
        return lines[number - 1].strip() if 1 <= number <= len(lines) else ""


def evidence(tool_message: str, source_line: str) -> str:
    """The code that triggered a finding, falling back to the tool's own words."""
    return source_line or tool_message
