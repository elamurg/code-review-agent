from typing import Any

from reviewer.evaluation.collect import (
    review_comments_by_pr,
    reviewed_candidate,
    silent_candidates,
)


def user(login: str, kind: str = "User") -> dict[str, str]:
    return {"login": login, "type": kind}


def api_comment(number: int, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": 1,
        "pull_request_url": f"https://api.github.com/repos/o/r/pulls/{number}",
        "in_reply_to_id": None,
        "subject_type": "line",
        "side": "RIGHT",
        "original_line": 10,
        "original_commit_id": "c1",
        "created_at": "2026-01-02T00:00:00Z",
        "user": user("reviewer"),
    }
    fields.update(overrides)
    return fields


def api_pull(number: int, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "number": number,
        "title": "t",
        "html_url": f"https://github.com/o/r/pull/{number}",
        "merged_at": "2026-01-05T00:00:00Z",
        "created_at": "2026-01-03T00:00:00Z",
        "user": user("author"),
        "base": {"sha": "base"},
        "head": {"sha": "head"},
    }
    fields.update(overrides)
    return fields


def test_keeps_only_top_level_human_comments_on_new_lines() -> None:
    keep = api_comment(1, id=1)
    dropped = [
        api_comment(1, id=2, in_reply_to_id=1),  # a reply
        api_comment(1, id=3, side="LEFT"),  # on a removed line
        api_comment(1, id=4, subject_type="file"),  # about the whole file
        api_comment(1, id=5, original_line=None),
        api_comment(1, id=6, user=user("ci[bot]", "Bot")),
    ]
    grouped = review_comments_by_pr([keep, *dropped])
    assert {n: [c["id"] for c in cs] for n, cs in grouped.items()} == {1: [1]}


def test_snapshot_is_the_commit_of_the_earliest_comment() -> None:
    later = api_comment(1, id=2, original_commit_id="c2", created_at="2026-01-04T00:00:00Z")
    earliest = api_comment(1, id=1, original_commit_id="c1", created_at="2026-01-02T00:00:00Z")
    same_commit = api_comment(1, id=3, original_commit_id="c1", created_at="2026-01-03T00:00:00Z")
    candidate = reviewed_candidate("o/r", api_pull(1), [later, earliest, same_commit])
    assert candidate is not None
    assert candidate.snapshot_sha == "c1"
    assert [c["id"] for c in candidate.comments] == [1, 3]


def test_authors_own_comments_and_unmerged_prs_are_not_reviews() -> None:
    by_author = api_comment(1, user=user("author"))
    assert reviewed_candidate("o/r", api_pull(1), [by_author]) is None
    assert reviewed_candidate("o/r", api_pull(1, merged_at=None), [api_comment(1)]) is None


def test_silent_prs_are_merged_uncommented_and_inside_the_window() -> None:
    comments = [api_comment(5, created_at="2026-01-02T00:00:00Z")]
    pulls = [
        api_pull(4, created_at="2026-01-03T00:00:00Z"),  # silent
        api_pull(5, created_at="2026-01-03T00:00:00Z"),  # has a comment
        api_pull(3, created_at="2025-12-01T00:00:00Z"),  # older than the comment window
        api_pull(6, created_at="2026-01-03T00:00:00Z", merged_at=None),  # closed unmerged
    ]
    assert [c.number for c in silent_candidates("o/r", pulls, comments)] == [4]
