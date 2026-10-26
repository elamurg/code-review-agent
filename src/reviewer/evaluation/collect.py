"""Collect golden-set candidates from merged public pull requests.

    python -m reviewer.evaluation.collect --repo pallets/click --out data/golden

For each repository this makes two GitHub API calls (recent review comments, recent
closed PRs), which fits the 60 requests/hour allowed without a token. Set GITHUB_TOKEN
to raise the limit. Diffs and file contents come from github.com and
raw.githubusercontent.com, which do not count against the API limit.

Collected comments are unlabelled. Labelling is a human judgement; see data/golden/README.md.

The diff is taken at the commit the reviewer actually saw, not the final merged state:
by the time a PR merges, the author has usually fixed what the reviewer pointed out, and
the comment would then point at the fix instead of the problem.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reviewer.analysis.security import is_requirements_file
from reviewer.domain.models import FileStatus, LineKind
from reviewer.ingestion.diff import DiffParseError, parse_diff

API = "https://api.github.com"
MAX_CHANGED_LINES = 600
MAX_FILES = 20
MAX_FILE_BYTES = 300_000


@dataclass(frozen=True, slots=True)
class Candidate:
    repo: str
    number: int
    title: str
    url: str
    base_sha: str
    snapshot_sha: str
    comments: tuple[dict[str, Any], ...]  # empty: a PR merged with nothing to say


def _is_bot(user: dict[str, Any] | None) -> bool:
    if not user:
        return True
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def review_comments_by_pr(comments: Sequence[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Top-level line comments by humans on the new side of the diff, grouped by PR.

    Replies are discussion, not review; comments on removed lines (side LEFT) cannot be
    matched against findings, which always point at the new side.
    """
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for comment in comments:
        if (
            comment.get("in_reply_to_id") is None
            and comment.get("subject_type", "line") == "line"
            and comment.get("side") == "RIGHT"
            and comment.get("original_line") is not None
            and not _is_bot(comment.get("user"))
        ):
            grouped[_pr_number(comment)].append(comment)
    return grouped


def reviewed_candidate(
    repo: str, pull: dict[str, Any], comments: Sequence[dict[str, Any]]
) -> Candidate | None:
    """The PR as the reviewer saw it, or None if it is not a usable reviewed example.

    The snapshot is the commit the earliest comment was written on, and only comments on
    that same commit are kept, so they all match one diff. The author's own comments
    are notes, not review.
    """
    if not pull.get("merged_at") or _is_bot(pull.get("user")):
        return None
    author = pull["user"]["login"]
    by_others = sorted(
        (c for c in comments if c["user"]["login"] != author), key=lambda c: c["created_at"]
    )
    if not by_others:
        return None
    snapshot = by_others[0]["original_commit_id"]
    kept = tuple(c for c in by_others if c["original_commit_id"] == snapshot)
    return _candidate(repo, pull, snapshot, kept)


def silent_candidates(
    repo: str, pulls: Sequence[dict[str, Any]], comments: Sequence[dict[str, Any]]
) -> list[Candidate]:
    """Merged PRs with no review comments at all, newest first.

    Only PRs opened after the oldest comment on the page count: for older ones the page
    does not cover their whole life, so "no comments seen" would not mean "none made".
    """
    commented = {_pr_number(c) for c in comments}
    window_start = min((c["created_at"] for c in comments), default="9999")
    return [
        _candidate(repo, pull, pull["head"]["sha"], ())
        for pull in sorted(pulls, key=lambda p: p["number"], reverse=True)
        if pull.get("merged_at")
        and not _is_bot(pull.get("user"))
        and pull["number"] not in commented
        and pull["created_at"] >= window_start
    ]


def _pr_number(comment: dict[str, Any]) -> int:
    return int(comment["pull_request_url"].rsplit("/", 1)[-1])


def _candidate(
    repo: str, pull: dict[str, Any], snapshot: str, comments: tuple[dict[str, Any], ...]
) -> Candidate:
    return Candidate(
        repo=repo,
        number=pull["number"],
        title=pull["title"],
        url=pull["html_url"],
        base_sha=pull["base"]["sha"],
        snapshot_sha=snapshot,
        comments=comments,
    )


class RateLimited(Exception):
    """The GitHub API limit is used up. Cached responses mean a rerun resumes cheaply."""


def _get(url: str) -> bytes:
    headers = {"User-Agent": "code-review-agent-golden-collector"}
    if url.startswith(API):
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body: bytes = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429) and exc.headers.get("X-RateLimit-Remaining") == "0":
            reset = time.localtime(int(exc.headers.get("X-RateLimit-Reset", "0")))
            raise RateLimited(
                f"GitHub API limit reached; it resets at {time.strftime('%H:%M', reset)}"
            ) from exc
        raise
    return body


def _get_json(url: str, cache: Path) -> Any:
    """API responses are cached on disk, so rerunning after a rate limit costs nothing."""
    cached = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if cached.exists():
        return json.loads(cached.read_text())
    data = _get(url)
    cache.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(data)
    return json.loads(data)


def collect_repo(
    repo: str,
    out: Path,
    *,
    max_reviewed: int,
    max_silent: int,
    cache: Path,
    verbose: bool = True,
) -> list[str]:
    """Write usable examples from one repository's recent history. Returns their ids."""
    comments = _get_json(
        f"{API}/repos/{repo}/pulls/comments?sort=created&direction=desc&per_page=100", cache
    )
    pulls = _get_json(
        f"{API}/repos/{repo}/pulls?state=closed&sort=created&direction=desc&per_page=100", cache
    )
    known = {p["number"]: p for p in pulls}
    written: list[str] = []

    def attempt(candidate: Candidate) -> bool:
        reason = _write_example(candidate, out)
        if verbose:
            print(f"{repo}#{candidate.number}: {reason or 'written'}", file=sys.stderr)
        if reason is None:
            written.append(example_id(repo, candidate.number))
        return reason is None

    reviewed = 0
    for number, pr_comments in sorted(review_comments_by_pr(comments).items(), reverse=True):
        if reviewed >= max_reviewed:
            break
        pull = known.get(number) or _get_json(f"{API}/repos/{repo}/pulls/{number}", cache)
        candidate = reviewed_candidate(repo, pull, pr_comments)
        if candidate is not None and attempt(candidate):
            reviewed += 1

    silent = 0
    for candidate in silent_candidates(repo, pulls, comments):
        if silent >= max_silent:
            break
        if attempt(candidate):
            silent += 1
    return written


def example_id(repo: str, number: int) -> str:
    return f"{repo.replace('/', '__')}__{number}"


def _write_example(candidate: Candidate, out: Path) -> str | None:
    """Fetch, check and write one example. Returns why it was skipped, or None."""
    repo = candidate.repo
    try:
        diff = _get(
            f"https://github.com/{repo}/compare/{candidate.base_sha}...{candidate.snapshot_sha}.diff"
        ).decode("utf-8")
        files = parse_diff(diff)
    except (urllib.error.URLError, UnicodeDecodeError, DiffParseError) as exc:
        return f"skipped: diff unusable ({exc})"

    changed = sum(
        1 for f in files for h in f.hunks for line in h.lines if line.kind is not LineKind.CONTEXT
    )
    analysable = [
        f
        for f in files
        if f.status is not FileStatus.DELETED
        and not f.is_binary
        and (f.path.endswith(".py") or is_requirements_file(f.path))
    ]
    if not any(f.path.endswith(".py") for f in analysable):
        return "skipped: no Python files changed"
    if changed > MAX_CHANGED_LINES or len(files) > MAX_FILES:
        return f"skipped: too large ({changed} changed lines, {len(files)} files)"

    new_side = {
        f.path: {
            line.new_number for h in f.hunks for line in h.lines if line.new_number is not None
        }
        for f in files
    }
    comments = [
        c for c in candidate.comments if c["original_line"] in new_side.get(c["path"], set())
    ]
    if candidate.comments and not comments:
        return "skipped: no comment lands on a line of the diff"

    contents: dict[str, bytes] = {}
    for f in analysable:
        data = _get(f"https://raw.githubusercontent.com/{repo}/{candidate.snapshot_sha}/{f.path}")
        if len(data) > MAX_FILE_BYTES:
            return f"skipped: {f.path} is {len(data)} bytes"
        contents[f.path] = data

    directory = out / example_id(repo, candidate.number)
    (directory / "files").mkdir(parents=True, exist_ok=True)
    (directory / "diff.patch").write_text(diff, encoding="utf-8")
    for path, data in contents.items():
        target = directory / "files" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    example = {
        "repo": repo,
        "number": candidate.number,
        "title": candidate.title,
        "url": candidate.url,
        "base_sha": candidate.base_sha,
        "head_sha": candidate.snapshot_sha,
        "comments": [
            {
                "id": c["id"],
                "author": c["user"]["login"],
                "path": c["path"],
                "line": c["original_line"],
                "url": c["html_url"],
                "body": c["body"],
                "label": None,
                "category": None,
                "rationale": None,
            }
            for c in comments
        ],
    }
    (directory / "example.json").write_text(json.dumps(example, indent=2) + "\n")
    time.sleep(0.3)  # be polite to github.com
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", action="append", required=True, help="owner/name")
    parser.add_argument("--out", type=Path, default=Path("data/golden"))
    parser.add_argument("--max-reviewed", type=int, default=4)
    parser.add_argument("--max-silent", type=int, default=1)
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache" / "reviewer-golden-api"
    )
    args = parser.parse_args(argv)
    try:
        for repo in args.repo:
            collect_repo(
                repo,
                args.out,
                max_reviewed=args.max_reviewed,
                max_silent=args.max_silent,
                cache=args.cache,
            )
    except RateLimited as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
