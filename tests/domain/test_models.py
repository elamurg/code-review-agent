import dataclasses
import subprocess
import sys
from typing import Any

import pytest

from reviewer.domain.errors import DomainValidationError
from reviewer.domain.models import (
    Category,
    DiffFile,
    FileStatus,
    Finding,
    Hunk,
    Line,
    LineKind,
    PullRequest,
    Review,
    Severity,
)

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40


def added(new: int, content: str = "x") -> Line:
    return Line(kind=LineKind.ADDED, content=content, old_number=None, new_number=new)


def removed(old: int, content: str = "x") -> Line:
    return Line(kind=LineKind.REMOVED, content=content, old_number=old, new_number=None)


def context(old: int, new: int, content: str = "x") -> Line:
    return Line(kind=LineKind.CONTEXT, content=content, old_number=old, new_number=new)


def modified_hunk() -> Hunk:
    # @@ -10,3 +10,3 @@
    return Hunk(
        old_start=10,
        old_count=3,
        new_start=10,
        new_count=3,
        lines=(context(10, 10), removed(11), added(11), context(12, 12)),
    )


def modified_file(path: str = "src/app.py") -> DiffFile:
    return DiffFile(
        status=FileStatus.MODIFIED, old_path=path, new_path=path, hunks=(modified_hunk(),)
    )


def make_finding(**overrides: Any) -> Finding:
    fields: dict[str, Any] = {
        "file": "src/app.py",
        "line": 11,
        "severity": Severity.HIGH,
        "category": Category.CORRECTNESS,
        "message": "Off-by-one in loop bound.",
        "evidence": "for i in range(len(items) + 1):",
        "source": "correctness-agent",
        "confidence": 0.8,
        "prompt_version": "correctness-v1",
    }
    fields.update(overrides)
    return Finding(**fields)


def make_pull_request(**overrides: Any) -> PullRequest:
    fields: dict[str, Any] = {
        "repo": "octo/widgets",
        "number": 42,
        "title": "Fix loop",
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "files": (modified_file(),),
    }
    fields.update(overrides)
    return PullRequest(**fields)


def test_review_builds_from_valid_parts() -> None:
    review = Review(
        pull_request=make_pull_request(), findings=(make_finding(),), summary="One issue."
    )
    assert review.findings[0].line == 11


def test_review_with_no_findings_is_valid() -> None:
    assert Review(pull_request=make_pull_request(), findings=(), summary="").findings == ()


def test_tool_finding_needs_no_prompt_version() -> None:
    assert make_finding(source="ruff", prompt_version=None, confidence=1.0).prompt_version is None


def test_models_are_immutable() -> None:
    finding = make_finding()
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.line = 12  # type: ignore[misc]


def test_severity_ranks_in_order() -> None:
    ranked = sorted(Severity, key=lambda s: s.rank)
    assert ranked == [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]


@pytest.mark.parametrize(
    "diff_file",
    [
        pytest.param(
            DiffFile(
                status=FileStatus.ADDED,
                old_path=None,
                new_path="new.py",
                # @@ -0,0 +1,2 @@
                hunks=(
                    Hunk(
                        old_start=0,
                        old_count=0,
                        new_start=1,
                        new_count=2,
                        lines=(added(1), added(2)),
                    ),
                ),
            ),
            id="added",
        ),
        pytest.param(
            DiffFile(
                status=FileStatus.DELETED,
                old_path="old.py",
                new_path=None,
                # @@ -1,2 +0,0 @@
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=2,
                        new_start=0,
                        new_count=0,
                        lines=(removed(1), removed(2)),
                    ),
                ),
            ),
            id="deleted",
        ),
        pytest.param(
            DiffFile(status=FileStatus.RENAMED, old_path="a.py", new_path="b.py"),
            id="pure-rename",
        ),
        pytest.param(
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="logo.png",
                new_path="logo.png",
                is_binary=True,
            ),
            id="binary",
        ),
        pytest.param(
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="a.py",
                new_path="a.py",
                # @@ -1,1 +1,2 @@ then @@ -20,1 +21,1 @@
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=1,
                        new_start=1,
                        new_count=2,
                        lines=(added(1), context(1, 2)),
                    ),
                    Hunk(
                        old_start=20,
                        old_count=1,
                        new_start=21,
                        new_count=1,
                        lines=(context(20, 21),),
                    ),
                ),
            ),
            id="multiple-hunks-from-line-1",
        ),
    ],
)
def test_valid_diff_files(diff_file: DiffFile) -> None:
    assert diff_file.path


def test_deleted_file_path_falls_back_to_old_path() -> None:
    deleted = DiffFile(status=FileStatus.DELETED, old_path="gone.py", new_path=None)
    assert deleted.path == "gone.py"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"file": None}, id="no-file"),
        pytest.param({"file": ""}, id="empty-file"),
        pytest.param({"file": "   "}, id="blank-file"),
        pytest.param({"file": "/abs/app.py"}, id="absolute-file"),
        pytest.param({"line": None}, id="no-line"),
        pytest.param({"line": 0}, id="line-zero"),
        pytest.param({"line": -3}, id="negative-line"),
        pytest.param({"line": True}, id="bool-line"),
        pytest.param({"line": 11.0}, id="float-line"),
        pytest.param({"severity": "high"}, id="raw-string-severity"),
        pytest.param({"category": "bug"}, id="unknown-category"),
        pytest.param({"message": ""}, id="empty-message"),
        pytest.param({"evidence": ""}, id="no-evidence"),
        pytest.param({"source": ""}, id="no-source"),
        pytest.param({"confidence": 1.5}, id="confidence-above-1"),
        pytest.param({"confidence": -0.1}, id="confidence-below-0"),
        pytest.param({"prompt_version": ""}, id="empty-prompt-version"),
    ],
)
def test_invalid_finding_raises(overrides: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        make_finding(**overrides)


@pytest.mark.parametrize("missing", ["file", "line"])
def test_finding_without_location_cannot_be_constructed(missing: str) -> None:
    fields = dataclasses.asdict(make_finding())
    del fields[missing]
    with pytest.raises(TypeError):
        Finding(**fields)


@pytest.mark.parametrize(
    ("kind", "old", "new"),
    [
        pytest.param(LineKind.ADDED, 3, 3, id="added-with-old-number"),
        pytest.param(LineKind.ADDED, None, None, id="added-without-new-number"),
        pytest.param(LineKind.REMOVED, 3, 3, id="removed-with-new-number"),
        pytest.param(LineKind.REMOVED, None, None, id="removed-without-old-number"),
        pytest.param(LineKind.CONTEXT, 3, None, id="context-missing-new"),
        pytest.param(LineKind.CONTEXT, None, 3, id="context-missing-old"),
        pytest.param(LineKind.CONTEXT, 0, 1, id="line-number-zero"),
    ],
)
def test_invalid_line_numbers_raise(kind: LineKind, old: int | None, new: int | None) -> None:
    with pytest.raises(DomainValidationError):
        Line(kind=kind, content="x", old_number=old, new_number=new)


def test_line_content_cannot_span_lines() -> None:
    with pytest.raises(DomainValidationError):
        added(1, content="a\nb")


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {
                "old_start": 10,
                "old_count": 2,
                "new_start": 10,
                "new_count": 2,
                "lines": (context(10, 10), context(11, 11), context(12, 12)),
            },
            id="more-lines-than-header",
        ),
        pytest.param(
            {
                "old_start": 10,
                "old_count": 1,
                "new_start": 10,
                "new_count": 1,
                "lines": (context(10, 10), added(12)),
            },
            id="gap-in-new-numbers",
        ),
        pytest.param(
            {
                "old_start": 10,
                "old_count": 1,
                "new_start": 10,
                "new_count": 1,
                "lines": (context(11, 11),),
            },
            id="numbers-do-not-start-at-header",
        ),
        pytest.param(
            {
                "old_start": 0,
                "old_count": 1,
                "new_start": 0,
                "new_count": 1,
                "lines": (context(1, 1),),
            },
            id="start-zero-with-lines",
        ),
        pytest.param(
            {"old_start": 1, "old_count": -1, "new_start": 1, "new_count": 1, "lines": (added(1),)},
            id="negative-count",
        ),
        pytest.param(
            {"old_start": 1, "old_count": 0, "new_start": 1, "new_count": 0, "lines": ()},
            id="empty-hunk",
        ),
    ],
)
def test_invalid_hunk_raises(fields: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        Hunk(**fields)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {"status": FileStatus.ADDED, "old_path": "a.py", "new_path": "a.py"},
            id="added-with-old-path",
        ),
        pytest.param(
            {"status": FileStatus.DELETED, "old_path": "a.py", "new_path": "a.py"},
            id="deleted-with-new-path",
        ),
        pytest.param(
            {"status": FileStatus.MODIFIED, "old_path": "a.py", "new_path": "b.py"},
            id="modified-changes-path",
        ),
        pytest.param(
            {"status": FileStatus.RENAMED, "old_path": "a.py", "new_path": "a.py"},
            id="rename-to-same-path",
        ),
        pytest.param(
            {"status": FileStatus.MODIFIED, "old_path": None, "new_path": None},
            id="no-paths",
        ),
        pytest.param(
            {
                "status": FileStatus.MODIFIED,
                "old_path": "a.png",
                "new_path": "a.png",
                "is_binary": True,
                "hunks": (modified_hunk(),),
            },
            id="binary-with-hunks",
        ),
        pytest.param(
            {
                "status": FileStatus.ADDED,
                "old_path": None,
                "new_path": "a.py",
                "hunks": (modified_hunk(),),
            },
            id="added-with-old-lines",
        ),
        pytest.param(
            {
                "status": FileStatus.MODIFIED,
                "old_path": "a.py",
                "new_path": "a.py",
                "hunks": (modified_hunk(), modified_hunk()),
            },
            id="overlapping-hunks",
        ),
    ],
)
def test_invalid_diff_file_raises(fields: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        DiffFile(**fields)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"repo": "widgets"}, id="repo-without-owner"),
        pytest.param({"number": 0}, id="number-zero"),
        pytest.param({"head_sha": "abc123"}, id="short-sha"),
        pytest.param({"base_sha": "A" * 40}, id="uppercase-sha"),
        pytest.param({"files": (modified_file(), modified_file())}, id="duplicate-file"),
    ],
)
def test_invalid_pull_request_raises(overrides: dict[str, Any]) -> None:
    with pytest.raises(DomainValidationError):
        make_pull_request(**overrides)


def test_review_rejects_finding_outside_the_pull_request() -> None:
    with pytest.raises(DomainValidationError):
        Review(
            pull_request=make_pull_request(),
            findings=(make_finding(file="src/elsewhere.py"),),
            summary="",
        )


# --- the domain stays pure ------------------------------------------------------------


def test_domain_imports_nothing_outside_the_standard_library() -> None:
    # Run in a fresh interpreter so modules other tests imported don't count.
    script = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import reviewer.domain.models\n"
        "new = set(sys.modules) - before\n"
        "tops = {n.split('.')[0] for n in new}\n"
        "print(sorted(tops - set(sys.stdlib_module_names) - {'reviewer'}))\n"
        "print(sorted(n for n in new if n.startswith('reviewer.')"
        " and not n.startswith('reviewer.domain')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    third_party, other_layers = result.stdout.splitlines()
    assert third_party == "[]"
    assert other_layers == "[]"
