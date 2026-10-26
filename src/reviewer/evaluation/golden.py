"""The golden dataset: real pull requests, real human review comments, and our labels.

Layout, one directory per example under data/golden/:

    <owner>__<repo>__<number>/
        example.json   PR metadata and every human comment, each labelled
        diff.patch     the diff the reviewer saw (base...reviewed commit)
        files/         changed Python and requirements files at the reviewed commit

A comment labelled "issue" is something a good review bot should also have raised.
Everything else is "not_issue": questions, style preferences, docs wording, praise.
The rules for deciding are in data/golden/README.md.
"""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from reviewer.domain.errors import DomainValidationError
from reviewer.domain.models import Category, LineKind, PullRequest
from reviewer.ingestion.diff import DiffParseError, parse_diff

DEFAULT_ROOT = Path("data/golden")


class GoldenDataError(ValueError):
    """An example is missing, unlabelled, or does not match its own diff."""


class Label(StrEnum):
    ISSUE = "issue"
    NOT_ISSUE = "not_issue"


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenComment:
    id: int
    author: str
    path: str
    line: int
    url: str
    body: str
    label: Label
    category: Category | None
    rationale: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GoldenExample:
    id: str
    url: str
    pull_request: PullRequest
    comments: tuple[GoldenComment, ...]
    checkout: Path  # the files/ directory: the changed files at the reviewed commit

    @property
    def issues(self) -> tuple[GoldenComment, ...]:
        return tuple(c for c in self.comments if c.label is Label.ISSUE)

    @property
    def nothing_to_say(self) -> bool:
        """The correct review is silence. A bot that comments here is pure noise."""
        return not self.issues

    @property
    def changed_lines(self) -> int:
        return sum(
            1
            for f in self.pull_request.files
            for hunk in f.hunks
            for line in hunk.lines
            if line.kind is not LineKind.CONTEXT
        )


def load_golden(root: Path = DEFAULT_ROOT) -> tuple[GoldenExample, ...]:
    directories = sorted(p for p in root.iterdir() if (p / "example.json").is_file())
    if not directories:
        raise GoldenDataError(f"no examples found under {root}")
    return tuple(load_example(d) for d in directories)


def load_example(directory: Path) -> GoldenExample:
    try:
        raw = json.loads((directory / "example.json").read_text())
        diff = (directory / "diff.patch").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenDataError(f"{directory.name}: cannot read example: {exc}") from exc
    try:
        pull_request = PullRequest(
            repo=raw["repo"],
            number=raw["number"],
            title=raw["title"],
            base_sha=raw["base_sha"],
            head_sha=raw["head_sha"],
            files=parse_diff(diff),
        )
    except (DiffParseError, DomainValidationError, KeyError) as exc:
        raise GoldenDataError(f"{directory.name}: invalid pull request: {exc}") from exc

    comments = tuple(_comment(directory.name, c) for c in raw["comments"])
    new_side = {
        f.path: {line.new_number for h in f.hunks for line in h.lines if line.new_number}
        for f in pull_request.files
    }
    for comment in comments:
        if comment.line not in new_side.get(comment.path, set()):
            raise GoldenDataError(
                f"{directory.name}: comment {comment.id} points at {comment.path}:"
                f"{comment.line}, which is not a line of the diff"
            )
    return GoldenExample(
        id=directory.name,
        url=raw["url"],
        pull_request=pull_request,
        comments=comments,
        checkout=directory / "files",
    )


def _comment(example: str, raw: dict[str, Any]) -> GoldenComment:
    where = f"{example}: comment {raw.get('id')}"
    try:
        label = Label(raw["label"])
    except ValueError:
        raise GoldenDataError(f"{where}: unlabelled or unknown label {raw['label']!r}") from None
    category = Category(raw["category"]) if raw.get("category") else None
    if label is Label.ISSUE and category is None:
        raise GoldenDataError(f"{where}: an issue needs a category")
    if not str(raw.get("rationale") or "").strip():
        raise GoldenDataError(f"{where}: every label needs a written rationale")
    return GoldenComment(
        id=raw["id"],
        author=raw["author"],
        path=raw["path"],
        line=raw["line"],
        url=raw["url"],
        body=raw["body"],
        label=label,
        category=category,
        rationale=raw["rationale"],
    )


def fingerprint(root: Path = DEFAULT_ROOT) -> str:
    """A short hash of every example's labels and diff. Changes whenever the data does,
    so an evaluation report records exactly which version of the set it was run on."""
    digest = hashlib.sha256()
    for directory in sorted(p for p in root.iterdir() if (p / "example.json").is_file()):
        digest.update(directory.name.encode())
        digest.update((directory / "example.json").read_bytes())
        digest.update((directory / "diff.patch").read_bytes())
    return digest.hexdigest()[:12]
