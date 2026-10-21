import shutil
import subprocess
from pathlib import Path

import pytest

from reviewer.domain.models import DiffFile, FileStatus, Hunk, Line, LineKind
from reviewer.ingestion.diff import DiffParseError, parse_diff


def added(new: int, content: str) -> Line:
    return Line(kind=LineKind.ADDED, content=content, old_number=None, new_number=new)


def removed(old: int, content: str) -> Line:
    return Line(kind=LineKind.REMOVED, content=content, old_number=old, new_number=None)


def context(old: int, new: int, content: str) -> Line:
    return Line(kind=LineKind.CONTEXT, content=content, old_number=old, new_number=new)


def diff(*lines: str) -> str:
    # Built line by line so editors and pre-commit can't strip meaningful whitespace.
    return "\n".join(lines) + "\n"


ADDED_FILE = diff(
    "diff --git a/new.py b/new.py",
    "new file mode 100644",
    "index 0000000..3b18e51",
    "--- /dev/null",
    "+++ b/new.py",
    "@@ -0,0 +1,2 @@",
    "+import os",
    "+print(os.getcwd())",
)

DELETED_FILE = diff(
    "diff --git a/old.py b/old.py",
    "deleted file mode 100644",
    "index 3b18e51..0000000",
    "--- a/old.py",
    "+++ /dev/null",
    "@@ -1,2 +0,0 @@",
    "-import os",
    "-print(os.getcwd())",
)

PURE_RENAME = diff(
    "diff --git a/src/a.py b/src/b.py",
    "similarity index 100%",
    "rename from src/a.py",
    "rename to src/b.py",
)

RENAME_WITH_EDIT = diff(
    "diff --git a/a.py b/b.py",
    "similarity index 80%",
    "rename from a.py",
    "rename to b.py",
    "index 1111111..2222222 100644",
    "--- a/a.py",
    "+++ b/b.py",
    "@@ -1,2 +1,2 @@",
    " x = 1",
    "-y = 2",
    "+y = 3",
)

BINARY_FILE = diff(
    "diff --git a/logo.png b/logo.png",
    "index 1111111..2222222 100644",
    "Binary files a/logo.png and b/logo.png differ",
)

MULTIPLE_HUNKS = diff(
    "diff --git a/app.py b/app.py",
    "index 1111111..2222222 100644",
    "--- a/app.py",
    "+++ b/app.py",
    "@@ -1,3 +1,4 @@",
    "+import sys",
    " import os",
    " ",
    " def main():",
    "@@ -20,3 +21,2 @@ def main():",
    "     a = 1",
    "-    b = 2",
    "     return a",
)

NO_TRAILING_NEWLINE = diff(
    "diff --git a/end.py b/end.py",
    "index 1111111..2222222 100644",
    "--- a/end.py",
    "+++ b/end.py",
    "@@ -1,2 +1,2 @@",
    " first",
    "-last",
    "\\ No newline at end of file",
    "+last",
    "\\ No newline at end of file",
)

HUNK_AT_LINE_1 = diff(
    "diff --git a/top.py b/top.py",
    "index 1111111..2222222 100644",
    "--- a/top.py",
    "+++ b/top.py",
    "@@ -1,2 +1,3 @@",
    "+# header",
    " a",
    " b",
)

COUNTS_LEFT_OUT = diff(
    "diff --git a/one.py b/one.py",
    "index 1111111..2222222 100644",
    "--- a/one.py",
    "+++ b/one.py",
    "@@ -5 +5 @@",
    "-old",
    "+new",
)

# Removing a SQL comment "-- note" produces a diff line "--- note", which looks
# exactly like a file header. Counting lines from the hunk header avoids the trap.
DASH_DASH_CONTENT = diff(
    "diff --git a/q.sql b/q.sql",
    "index 1111111..2222222 100644",
    "--- a/q.sql",
    "+++ b/q.sql",
    "@@ -1,2 +1,1 @@",
    "-- note",
    " select 1;",
)

EMPTY_CONTEXT_STRIPPED = diff(
    "diff --git a/gap.py b/gap.py",
    "index 1111111..2222222 100644",
    "--- a/gap.py",
    "+++ b/gap.py",
    "@@ -1,3 +1,3 @@",
    " a",
    "",
    "-b",
    "+c",
)

QUOTED_PATH = diff(
    'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"',
    "index 1111111..2222222 100644",
    '--- "a/caf\\303\\251.py"',
    '+++ "b/caf\\303\\251.py"',
    "@@ -1 +1 @@",
    "-x",
    "+y",
)

PATH_WITH_SPACE = diff(
    "diff --git a/my file.py b/my file.py",
    "index 1111111..2222222 100644",
    "--- a/my file.py\t",
    "+++ b/my file.py\t",
    "@@ -1 +1 @@",
    "-x",
    "+y",
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            ADDED_FILE,
            DiffFile(
                status=FileStatus.ADDED,
                old_path=None,
                new_path="new.py",
                hunks=(
                    Hunk(
                        old_start=0,
                        old_count=0,
                        new_start=1,
                        new_count=2,
                        lines=(added(1, "import os"), added(2, "print(os.getcwd())")),
                    ),
                ),
            ),
            id="added-file",
        ),
        pytest.param(
            DELETED_FILE,
            DiffFile(
                status=FileStatus.DELETED,
                old_path="old.py",
                new_path=None,
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=2,
                        new_start=0,
                        new_count=0,
                        lines=(removed(1, "import os"), removed(2, "print(os.getcwd())")),
                    ),
                ),
            ),
            id="deleted-file",
        ),
        pytest.param(
            PURE_RENAME,
            DiffFile(status=FileStatus.RENAMED, old_path="src/a.py", new_path="src/b.py"),
            id="renamed-file",
        ),
        pytest.param(
            RENAME_WITH_EDIT,
            DiffFile(
                status=FileStatus.RENAMED,
                old_path="a.py",
                new_path="b.py",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=2,
                        new_start=1,
                        new_count=2,
                        lines=(context(1, 1, "x = 1"), removed(2, "y = 2"), added(2, "y = 3")),
                    ),
                ),
            ),
            id="renamed-file-with-edit",
        ),
        pytest.param(
            BINARY_FILE,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="logo.png",
                new_path="logo.png",
                is_binary=True,
            ),
            id="binary-file",
        ),
        pytest.param(
            MULTIPLE_HUNKS,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="app.py",
                new_path="app.py",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=3,
                        new_start=1,
                        new_count=4,
                        lines=(
                            added(1, "import sys"),
                            context(1, 2, "import os"),
                            context(2, 3, ""),
                            context(3, 4, "def main():"),
                        ),
                    ),
                    Hunk(
                        old_start=20,
                        old_count=3,
                        new_start=21,
                        new_count=2,
                        lines=(
                            context(20, 21, "    a = 1"),
                            removed(21, "    b = 2"),
                            context(22, 22, "    return a"),
                        ),
                        section="def main():",
                    ),
                ),
            ),
            id="multiple-hunks-with-section",
        ),
        pytest.param(
            NO_TRAILING_NEWLINE,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="end.py",
                new_path="end.py",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=2,
                        new_start=1,
                        new_count=2,
                        lines=(context(1, 1, "first"), removed(2, "last"), added(2, "last")),
                    ),
                ),
            ),
            id="no-trailing-newline",
        ),
        pytest.param(
            HUNK_AT_LINE_1,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="top.py",
                new_path="top.py",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=2,
                        new_start=1,
                        new_count=3,
                        lines=(added(1, "# header"), context(1, 2, "a"), context(2, 3, "b")),
                    ),
                ),
            ),
            id="hunk-at-line-1",
        ),
        pytest.param(
            COUNTS_LEFT_OUT,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="one.py",
                new_path="one.py",
                hunks=(
                    Hunk(
                        old_start=5,
                        old_count=1,
                        new_start=5,
                        new_count=1,
                        lines=(removed(5, "old"), added(5, "new")),
                    ),
                ),
            ),
            id="header-without-counts",
        ),
        pytest.param(
            DASH_DASH_CONTENT,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="q.sql",
                new_path="q.sql",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=2,
                        new_start=1,
                        new_count=1,
                        lines=(removed(1, "- note"), context(2, 1, "select 1;")),
                    ),
                ),
            ),
            id="removed-line-looks-like-file-header",
        ),
        pytest.param(
            EMPTY_CONTEXT_STRIPPED,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="gap.py",
                new_path="gap.py",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=3,
                        new_start=1,
                        new_count=3,
                        lines=(
                            context(1, 1, "a"),
                            context(2, 2, ""),
                            removed(3, "b"),
                            added(3, "c"),
                        ),
                    ),
                ),
            ),
            id="empty-context-line-without-space",
        ),
        pytest.param(
            QUOTED_PATH,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="café.py",
                new_path="café.py",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=1,
                        new_start=1,
                        new_count=1,
                        lines=(removed(1, "x"), added(1, "y")),
                    ),
                ),
            ),
            id="quoted-utf8-path",
        ),
        pytest.param(
            PATH_WITH_SPACE,
            DiffFile(
                status=FileStatus.MODIFIED,
                old_path="my file.py",
                new_path="my file.py",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=1,
                        new_start=1,
                        new_count=1,
                        lines=(removed(1, "x"), added(1, "y")),
                    ),
                ),
            ),
            id="path-with-space",
        ),
    ],
)
def test_parses_single_file(text: str, expected: DiffFile) -> None:
    assert parse_diff(text) == (expected,)


def test_parses_several_files_in_order() -> None:
    files = parse_diff(ADDED_FILE + BINARY_FILE + DELETED_FILE)
    assert [f.path for f in files] == ["new.py", "logo.png", "old.py"]
    assert [f.status for f in files] == [FileStatus.ADDED, FileStatus.MODIFIED, FileStatus.DELETED]


@pytest.mark.parametrize("text", ["", "\n", "   \n\n"])
def test_empty_input_is_an_empty_diff(text: str) -> None:
    assert parse_diff(text) == ()


def test_mode_change_only_is_a_modified_file_without_hunks() -> None:
    text = diff("diff --git a/run.sh b/run.sh", "old mode 100644", "new mode 100755")
    assert parse_diff(text) == (
        DiffFile(status=FileStatus.MODIFIED, old_path="run.sh", new_path="run.sh"),
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param(
            diff("@@ -1 +1 @@", "-a", "+b"),
            "expected 'diff --git'",
            id="hunk-before-any-file",
        ),
        pytest.param(
            diff(
                "diff --git a/x.py b/x.py",
                "--- a/x.py",
                "+++ b/x.py",
                "@@ -1,3 +1,3 @@",
                " a",
                "-b",
                "+c",
            ),
            "hunk ended early",
            id="hunk-shorter-than-header",
        ),
        pytest.param(
            diff(
                "diff --git a/x.py b/x.py",
                "--- a/x.py",
                "+++ b/x.py",
                "@@ -1,2 +1,2 @@",
                " a",
                "-b",
                "+c",
                "+d",
            ),
            "unexpected line",
            id="hunk-longer-than-header",
        ),
        pytest.param(
            diff("diff --git a/x.py b/x.py", "--- a/x.py", "+++ b/x.py", "@@ -1 +1 @@", "?a"),
            "unexpected line",
            id="unknown-line-prefix",
        ),
        pytest.param(
            diff("diff --git a/x.py b/x.py", "--- a/x.py", "+++ b/x.py", "@@ -a +1 @@", "+a"),
            "malformed hunk header",
            id="malformed-hunk-header",
        ),
        pytest.param(
            diff(
                "diff --git a/x.py b/y.py",
                "copy from x.py",
                "copy to y.py",
            ),
            "copied files are not supported",
            id="copy",
        ),
    ],
)
def test_malformed_diff_raises(text: str, message: str) -> None:
    with pytest.raises(DiffParseError, match=message):
        parse_diff(text)


def test_error_names_the_file() -> None:
    text = diff("diff --git a/x.py b/x.py", "--- a/x.py", "+++ b/x.py", "@@ -1,2 +1,2 @@", " a")
    with pytest.raises(DiffParseError, match=r"^x\.py: @@ -1,2 \+1,2 @@"):
        parse_diff(text)


# --- real git output ------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=true", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_parses_real_git_diff(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "keep.py").write_text("".join(f"line {n}\n" for n in range(1, 41)))
    (tmp_path / "gone.py").write_text("bye\n")
    (tmp_path / "move_me.py").write_text("".join(f"stay {n}\n" for n in range(1, 21)))
    (tmp_path / "image.bin").write_bytes(bytes(range(256)))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")

    keep = [f"line {n}" for n in range(1, 41)]
    keep[0] = "line 1 changed"
    keep[29] = "line 30 changed"
    (tmp_path / "keep.py").write_text("\n".join(keep))  # also drops the final newline
    (tmp_path / "gone.py").unlink()
    _git(tmp_path, "mv", "move_me.py", "moved.py")
    (tmp_path / "new.py").write_text("print('hi')\n")
    (tmp_path / "image.bin").write_bytes(bytes(reversed(range(256))))
    _git(tmp_path, "add", "-A")

    files = {f.path: f for f in parse_diff(_git(tmp_path, "diff", "--cached", "-M"))}

    assert files["gone.py"].status is FileStatus.DELETED
    assert files["new.py"].status is FileStatus.ADDED
    assert files["moved.py"].status is FileStatus.RENAMED
    assert files["moved.py"].old_path == "move_me.py"
    assert files["image.bin"].is_binary
    keep_hunks = files["keep.py"].hunks
    assert len(keep_hunks) == 3
    changed = [
        (line.old_number, line.new_number, line.content)
        for hunk in keep_hunks
        for line in hunk.lines
        if line.kind is not LineKind.CONTEXT
    ]
    assert changed == [
        (1, None, "line 1"),
        (None, 1, "line 1 changed"),
        (30, None, "line 30"),
        (None, 30, "line 30 changed"),
        (40, None, "line 40"),
        (None, 40, "line 40"),
    ]
