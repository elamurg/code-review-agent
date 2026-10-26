import json
from pathlib import Path
from typing import Any

import pytest

from reviewer.domain.models import Category
from reviewer.evaluation.golden import (
    DEFAULT_ROOT,
    GoldenDataError,
    Label,
    fingerprint,
    load_golden,
)

from .conftest import ExampleWriter, comment

REPO_ROOT = Path(__file__).parent.parent.parent


def test_loads_a_labelled_example(write_example: ExampleWriter) -> None:
    (example,) = load_golden(write_example())
    assert example.id == "octo__widgets__1"
    assert example.pull_request.paths == frozenset({"app.py"})
    assert [(c.label, c.category) for c in example.issues] == [(Label.ISSUE, Category.CORRECTNESS)]
    assert example.changed_lines == 3
    assert not example.nothing_to_say
    assert (example.checkout / "app.py").is_file()


def test_example_without_issues_has_nothing_to_say(write_example: ExampleWriter) -> None:
    not_issue = comment(label="not_issue", category=None, rationale="Naming preference.")
    (example,) = load_golden(write_example(comments=[not_issue]))
    assert example.nothing_to_say


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({"label": None}, "unlabelled", id="unlabelled"),
        pytest.param({"label": "maybe"}, "unknown label", id="unknown-label"),
        pytest.param({"category": None}, "needs a category", id="issue-without-category"),
        pytest.param({"rationale": ""}, "rationale", id="no-rationale"),
        pytest.param({"line": 99}, "not a line of the diff", id="comment-off-the-diff"),
        pytest.param({"path": "other.py"}, "not a line of the diff", id="comment-on-other-file"),
    ],
)
def test_invalid_examples_are_rejected(
    write_example: ExampleWriter, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(GoldenDataError, match=message):
        load_golden(write_example(comments=[comment(**overrides)]))


def test_broken_diff_is_rejected(write_example: ExampleWriter) -> None:
    with pytest.raises(GoldenDataError, match="invalid pull request"):
        load_golden(write_example(diff="not a diff\n"))


def test_empty_root_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GoldenDataError, match="no examples"):
        load_golden(tmp_path)


def test_fingerprint_changes_when_a_label_changes(write_example: ExampleWriter) -> None:
    root = write_example()
    before = fingerprint(root)
    path = root / "octo__widgets__1" / "example.json"
    data = json.loads(path.read_text())
    data["comments"][0]["label"] = "not_issue"
    path.write_text(json.dumps(data))
    assert fingerprint(root) != before
    assert fingerprint(root) == fingerprint(root)


# --- the real dataset -----------------------------------------------------------------


def test_the_real_golden_set_is_complete_and_labelled() -> None:
    """CR-11 done-when: 40 to 60 PRs, versioned, loadable, every comment labelled."""
    examples = load_golden(REPO_ROOT / DEFAULT_ROOT)
    assert 40 <= len(examples) <= 60
    assert len({e.pull_request.repo for e in examples}) >= 5  # not one project's habits
    assert sum(e.nothing_to_say for e in examples) >= 5  # "nothing to say" is represented
    # Most human comments are wording and polish, so real issues are sparse (11 of 66
    # comments at collection time). Enough to measure recall, not to over-read it.
    assert sum(len(e.issues) for e in examples) >= 10
    for example in examples:
        for path in example.pull_request.paths:
            if path.endswith(".py") and path in {
                f.path for f in example.pull_request.files if f.new_path and not f.is_binary
            }:
                assert (example.checkout / path).is_file(), f"{example.id}: {path} not stored"
