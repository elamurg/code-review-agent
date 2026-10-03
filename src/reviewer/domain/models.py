"""Core domain models. Pure: standard library only, no I/O.
Every model validates itself on construction, so an instance that exists is valid.
"""

import re
from dataclasses import dataclass
from enum import StrEnum #restricts values to a fixed set of strings

from reviewer.domain.errors import DomainValidationError

_SHA_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_REPO_PATTERN = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DomainValidationError(message)


def _require_text(name: str, value: object) -> None:
    _require(isinstance(value, str) and value.strip() != "", f"{name} must be a non-empty string")


def _require_relative_path(name: str, value: object) -> None:
    _require_text(name, value)
    _require(not str(value).startswith("/"), f"{name} must be relative to the repo root")


def _is_int(value: object) -> bool:
    # bool is a subclass of int, and True is not a line number.
    return isinstance(value, int) and not isinstance(value, bool)


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    #for capping findings by severity
    @property
    def rank(self) -> int:
        """Higher is more severe. Use this for ordering, never the string value."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

#four agent areas
class Category(StrEnum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    CORRECTNESS = "correctness"
    STYLE = "style"


class LineKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CONTEXT = "context"


class FileStatus(StrEnum):
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"

#solidifying that you cant change the field after creation,
#managing smaller, faster objectt with slots (typos),
#kw_only to force keyword args for clarity
@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """One line of a hunk, numbered on each side of the diff it exists on.
    Added lines have no old number, removed lines have no new number,
    context lines have both.
    """

    kind: LineKind
    content: str
    old_number: int | None
    new_number: int | None

    def __post_init__(self) -> None:
        _require("\n" not in self.content, "line content must not contain a newline")
        self._check_number("old_number", self.old_number, self.kind is not LineKind.ADDED)
        self._check_number("new_number", self.new_number, self.kind is not LineKind.REMOVED)

    def _check_number(self, name: str, value: int | None, expected: bool) -> None:
        if expected:
            _require(
                _is_int(value) and value is not None and value >= 1,
                f"{self.kind} line needs {name} >= 1, got {value!r}",
            )
        else:
            _require(value is None, f"{self.kind} line must not have {name}, got {value!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Hunk:
    """One `@@ -old_start,old_count +new_start,new_count @@ section` block.
    The lines must number consecutively from each start, and the number of lines on
    each side must match its count, exactly as the hunk header claims.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[Line, ...]
    section: str = ""

    def __post_init__(self) -> None:
        self._check_range("old", self.old_start, self.old_count)
        self._check_range("new", self.new_start, self.new_count)
        _require(len(self.lines) > 0, "a hunk must contain at least one line")

        next_old, next_new = self.old_start, self.new_start
        for line in self.lines:
            if line.old_number is not None:
                _require(
                    line.old_number == next_old,
                    f"expected old line {next_old}, got {line.old_number}",
                )
                next_old += 1
            if line.new_number is not None:
                _require(
                    line.new_number == next_new,
                    f"expected new line {next_new}, got {line.new_number}",
                )
                next_new += 1

        _require(
            next_old - self.old_start == self.old_count,
            f"header says {self.old_count} old lines, hunk has {next_old - self.old_start}",
        )
        _require(
            next_new - self.new_start == self.new_count,
            f"header says {self.new_count} new lines, hunk has {next_new - self.new_start}",
        )

    @staticmethod
    def _check_range(side: str, start: int, count: int) -> None:
        _require(_is_int(count) and count >= 0, f"{side}_count must be >= 0, got {count!r}")
        # An empty side points at the line before it, which is 0 at the top of a file.
        minimum = 1 if count > 0 else 0
        _require(
            _is_int(start) and start >= minimum,
            f"{side}_start must be >= {minimum} when {side}_count is {count}, got {start!r}",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffFile:
    """One file in a pull request diff. Binary files carry no hunks."""

    status: FileStatus
    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...] = ()
    is_binary: bool = False

    def __post_init__(self) -> None:
        match self.status:
            case FileStatus.ADDED:
                _require(self.old_path is None, "an added file has no old_path")
                _require_relative_path("new_path", self.new_path)
                _require(
                    all(h.old_count == 0 for h in self.hunks),
                    "an added file cannot have old lines",
                )
            case FileStatus.DELETED:
                _require(self.new_path is None, "a deleted file has no new_path")
                _require_relative_path("old_path", self.old_path)
                _require(
                    all(h.new_count == 0 for h in self.hunks),
                    "a deleted file cannot have new lines",
                )
            case FileStatus.MODIFIED:
                _require_relative_path("new_path", self.new_path)
                _require(
                    self.old_path == self.new_path,
                    "a modified file keeps its path; use RENAMED for a path change",
                )
            case FileStatus.RENAMED:
                _require_relative_path("old_path", self.old_path)
                _require_relative_path("new_path", self.new_path)
                _require(self.old_path != self.new_path, "a renamed file must change its path")

        if self.is_binary:
            _require(not self.hunks, "a binary file has no hunks")

        for previous, current in zip(self.hunks, self.hunks[1:], strict=False):
            _require(
                current.old_start >= previous.old_start + previous.old_count
                and current.new_start >= previous.new_start + previous.new_count,
                "hunks must be in file order and must not overlap",
            )

    @property
    def path(self) -> str:
        """The path a reviewer would refer to: the new path, or the old one if deleted."""
        path = self.new_path if self.new_path is not None else self.old_path
        assert path is not None  # guaranteed by __post_init__
        return path


@dataclass(frozen=True, slots=True, kw_only=True)
class PullRequest:
    repo: str
    number: int
    title: str
    base_sha: str
    head_sha: str
    files: tuple[DiffFile, ...]

    def __post_init__(self) -> None:
        _require(
            isinstance(self.repo, str) and _REPO_PATTERN.fullmatch(self.repo) is not None,
            f"repo must look like 'owner/name', got {self.repo!r}",
        )
        _require(_is_int(self.number) and self.number >= 1, "number must be >= 1")
        _require(isinstance(self.title, str), "title must be a string")
        for name, sha in (("base_sha", self.base_sha), ("head_sha", self.head_sha)):
            _require(
                isinstance(sha, str) and _SHA_PATTERN.fullmatch(sha) is not None,
                f"{name} must be a full lowercase hex commit SHA, got {sha!r}",
            )
        paths = [f.path for f in self.files]
        _require(len(paths) == len(set(paths)), "a file appears more than once in the diff")

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(f.path for f in self.files)


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """One issue, anchored to a file and a line. There is no such thing as a
    finding without a location: that is the rule the whole product rests on.
    """

    file: str
    line: int
    severity: Severity
    category: Category
    message: str
    evidence: str
    source: str
    confidence: float
    prompt_version: str | None = None

    def __post_init__(self) -> None:
        _require_relative_path("file", self.file)
        _require(
            _is_int(self.line) and self.line >= 1,
            f"line must be an int >= 1, got {self.line!r}",
        )
        _require(isinstance(self.severity, Severity), "severity must be a Severity")
        _require(isinstance(self.category, Category), "category must be a Category")
        _require_text("message", self.message)
        _require_text("evidence", self.evidence)
        _require_text("source", self.source)
        _require(
            isinstance(self.confidence, int | float)
            and not isinstance(self.confidence, bool)
            and 0.0 <= self.confidence <= 1.0,
            f"confidence must be between 0 and 1, got {self.confidence!r}",
        )
        if self.prompt_version is not None:
            _require_text("prompt_version", self.prompt_version)


@dataclass(frozen=True, slots=True, kw_only=True)
class Review:
    """The review posted for one pull request at one head commit.

    No findings is a valid review: sometimes the right answer is nothing to say.
    """

    pull_request: PullRequest
    findings: tuple[Finding, ...]
    summary: str

    def __post_init__(self) -> None:
        _require(isinstance(self.summary, str), "summary must be a string")
        paths = self.pull_request.paths
        for finding in self.findings:
            _require(
                finding.file in paths,
                f"finding cites {finding.file!r}, which is not in the pull request",
            )
