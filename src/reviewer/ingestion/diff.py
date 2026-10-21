"""Parse a unified diff, as produced by `git diff` or GitHub, into domain models.

Line numbers are the hard part, so the parser leans on the domain models: every Hunk
checks its own numbering against its header, and any mismatch surfaces here as a
DiffParseError naming the file and the hunk.
"""

import re
from dataclasses import dataclass

from reviewer.domain.errors import DomainValidationError
from reviewer.domain.models import DiffFile, FileStatus, Hunk, Line, LineKind

_GIT_HEADER = "diff --git "
_DEV_NULL = "/dev/null"
_HUNK_HEADER = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))?"
    r" \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@ ?(?P<section>.*)"
)
# Escapes git uses inside quoted paths, e.g. "a/caf\303\251.py".
_SIMPLE_ESCAPES = {
    "a": 7,
    "b": 8,
    "t": 9,
    "n": 10,
    "v": 11,
    "f": 12,
    "r": 13,
    '"': 34,
    "\\": 92,
}


class DiffParseError(ValueError):
    """The text is not a unified diff this parser understands."""


@dataclass(frozen=True, slots=True)
class _Header:
    status: FileStatus
    old_path: str | None
    new_path: str | None
    is_binary: bool


def parse_diff(text: str) -> tuple[DiffFile, ...]:
    """Parse a whole multi-file diff. Empty input is an empty diff, not an error."""
    # Not splitlines(): it also splits on form feeds and other characters that can
    # legitimately appear inside a line, which would shift every line number after them.
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return tuple(_parse_file(section) for section in _split_files(lines))


def _split_files(lines: list[str]) -> list[list[str]]:
    sections: list[list[str]] = []
    for number, line in enumerate(lines, start=1):
        if line.startswith(_GIT_HEADER):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        elif line.strip():
            raise DiffParseError(f"line {number}: expected 'diff --git', got {line!r}")
    return sections


def _parse_file(lines: list[str]) -> DiffFile:
    hunk_start = next((i for i, line in enumerate(lines) if line.startswith("@@")), len(lines))
    header = _parse_header(lines[:hunk_start])
    path = header.new_path or header.old_path

    hunks: list[Hunk] = []
    position = hunk_start
    while position < len(lines):
        if header.is_binary:
            raise DiffParseError(f"{path}: binary file has hunk lines")
        hunk_header = lines[position]
        try:
            hunk, position = _parse_hunk(lines, position)
        except DomainValidationError as exc:
            raise DiffParseError(f"{path}: {hunk_header}: {exc}") from exc
        except DiffParseError as exc:
            raise DiffParseError(f"{path}: {hunk_header}: {exc}") from exc
        hunks.append(hunk)

    try:
        return DiffFile(
            status=header.status,
            old_path=header.old_path,
            new_path=header.new_path,
            hunks=tuple(hunks),
            is_binary=header.is_binary,
        )
    except DomainValidationError as exc:
        raise DiffParseError(f"{path}: {exc}") from exc


def _parse_header(lines: list[str]) -> _Header:
    git_old, git_new = _paths_from_git_line(lines[0])
    minus: str | None = None
    plus: str | None = None
    rename_from: str | None = None
    rename_to: str | None = None
    is_new = is_deleted = is_binary = False

    for line in lines[1:]:
        if is_binary:
            break  # whatever follows is binary patch data
        if line.startswith("new file mode "):
            is_new = True
        elif line.startswith("deleted file mode "):
            is_deleted = True
        elif line.startswith("rename from "):
            rename_from = _unquote(line.removeprefix("rename from "))
        elif line.startswith("rename to "):
            rename_to = _unquote(line.removeprefix("rename to "))
        elif line.startswith(("copy from ", "copy to ")):
            raise DiffParseError(f"{git_new}: copied files are not supported")
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            is_binary = True
        elif line.startswith("--- "):
            minus = _side_path(line.removeprefix("--- "), "a/")
        elif line.startswith("+++ "):
            plus = _side_path(line.removeprefix("+++ "), "b/")
        # index, mode and similarity lines carry nothing the models need.

    if is_new:
        return _Header(FileStatus.ADDED, None, plus or rename_to or git_new, is_binary)
    if is_deleted:
        return _Header(FileStatus.DELETED, minus or rename_from or git_old, None, is_binary)
    old_path = rename_from or minus or git_old
    new_path = rename_to or plus or git_new
    if old_path != new_path:
        return _Header(FileStatus.RENAMED, old_path, new_path, is_binary)
    return _Header(FileStatus.MODIFIED, old_path, new_path, is_binary)


def _parse_hunk(lines: list[str], position: int) -> tuple[Hunk, int]:
    match = _HUNK_HEADER.fullmatch(lines[position])
    if match is None:
        raise DiffParseError("malformed hunk header")
    old_start = int(match["old_start"])
    new_start = int(match["new_start"])
    # A count left out of the header means one line.
    old_count = int(match["old_count"] or 1)
    new_count = int(match["new_count"] or 1)

    # Consume lines by counting down the header, never by guessing from prefixes:
    # removing a line that starts with "-- " produces "--- ", which looks like a file header.
    old_left, new_left = old_count, new_count
    next_old, next_new = old_start, new_start
    body: list[Line] = []
    position += 1
    while old_left or new_left:
        if position >= len(lines):
            raise DiffParseError(
                f"hunk ended early: {old_left} old and {new_left} new lines missing"
            )
        raw = lines[position]
        position += 1
        if raw.startswith("\\"):
            continue  # "\ No newline at end of file" describes the line before it
        prefix, content = raw[:1], raw[1:]
        # Some tools strip the single space from an empty context line.
        if prefix in (" ", "") and old_left and new_left:
            body.append(
                Line(
                    kind=LineKind.CONTEXT,
                    content=content,
                    old_number=next_old,
                    new_number=next_new,
                )
            )
            next_old, next_new = next_old + 1, next_new + 1
            old_left, new_left = old_left - 1, new_left - 1
        elif prefix == "-" and old_left:
            body.append(
                Line(kind=LineKind.REMOVED, content=content, old_number=next_old, new_number=None)
            )
            next_old, old_left = next_old + 1, old_left - 1
        elif prefix == "+" and new_left:
            body.append(
                Line(kind=LineKind.ADDED, content=content, old_number=None, new_number=next_new)
            )
            next_new, new_left = next_new + 1, new_left - 1
        else:
            raise DiffParseError(
                f"unexpected line {raw!r} with {old_left} old and {new_left} new lines left"
            )

    while position < len(lines) and lines[position].startswith("\\"):
        position += 1
    if position < len(lines) and not lines[position].startswith("@@"):
        raise DiffParseError(f"unexpected line after hunk: {lines[position]!r}")

    hunk = Hunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        lines=tuple(body),
        section=match["section"],
    )
    return hunk, position


def _side_path(raw: str, prefix: str) -> str | None:
    # Git ends the path with a tab when it contains spaces; other tools add a timestamp.
    path = _unquote(raw.split("\t", 1)[0])
    if path == _DEV_NULL:
        return None
    return path.removeprefix(prefix)


def _paths_from_git_line(line: str) -> tuple[str, str]:
    rest = line.removeprefix(_GIT_HEADER)
    if rest.startswith('"') or rest.endswith('"'):
        old, new = _split_quoted_pair(rest)
        return old.removeprefix("a/"), new.removeprefix("b/")
    # "a/P b/P": when both sides are the same path, splitting in the middle is exact,
    # even if P contains " b/". Renames are resolved from "rename from/to" instead.
    half = (len(rest) - 5) // 2
    if len(rest) % 2 == 1 and rest[2 : 2 + half] == rest[5 + half :]:
        path = rest[2 : 2 + half]
        return path, path
    old, _, new = rest.partition(" b/")
    return old.removeprefix("a/"), new


def _split_quoted_pair(rest: str) -> tuple[str, str]:
    if rest.startswith('"'):
        end = _closing_quote(rest)
        old, new = rest[: end + 1], rest[end + 2 :]
    else:
        old, new = rest.split(" ", 1)
    return _unquote(old), _unquote(new)


def _closing_quote(text: str) -> int:
    position = 1
    while position < len(text):
        if text[position] == "\\":
            position += 2
            continue
        if text[position] == '"':
            return position
        position += 1
    raise DiffParseError(f"unterminated quoted path: {text!r}")


def _unquote(path: str) -> str:
    """Undo git's C-style quoting. Unquoted paths are returned unchanged."""
    if not (len(path) >= 2 and path.startswith('"') and path.endswith('"')):
        return path
    body = path[1:-1]
    data = bytearray()
    position = 0
    while position < len(body):
        char = body[position]
        if char != "\\":
            data.extend(char.encode())
            position += 1
            continue
        escaped = body[position + 1 : position + 2]
        if escaped in _SIMPLE_ESCAPES:
            data.append(_SIMPLE_ESCAPES[escaped])
            position += 2
        elif re.fullmatch(r"[0-7]{3}", body[position + 1 : position + 4]):
            data.append(int(body[position + 1 : position + 4], 8))
            position += 4
        else:
            raise DiffParseError(f"bad escape in quoted path: {path!r}")
    return data.decode("utf-8", errors="replace")
