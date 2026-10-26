# Golden dataset

Merged public pull requests that received human review, each stored as the reviewer saw it, with every human comment labelled. Everything the evaluation runner measures is measured against this.

Load it with `reviewer.evaluation.golden.load_golden()`. Add examples with `python -m reviewer.evaluation.collect --repo owner/name`, then label them.

## Layout

```
<owner>__<repo>__<number>/
    example.json   PR metadata and every human review comment, each labelled
    diff.patch     base...reviewed-commit diff: what the reviewer actually saw
    files/         changed .py and requirements files at the reviewed commit
```

**Why the reviewed commit, not the merged result.** By merge time the author has usually fixed what the reviewer raised, so a comment would point at the fix instead of the problem. The collector takes the commit the earliest comment was written on, and keeps only the comments made on that same commit.

**Why only some files.** The analysers only read Python and requirements files, so other changed files are in the diff but not stored. Comments on docs and changelogs are still kept and labelled; leaving them out would make recall look better than it is.

## How examples were chosen

- Merged pull requests from active Python projects, from each project's most recent review comments at collection time (2026-10-21).
- **Reviewed examples:** at least one top-level line comment by a human who is not the author. Replies are discussion, not review. Bots are excluded. Comments on removed lines are excluded, because findings always point at the new side of the diff.
- **Silent examples:** merged with no review comments at all. The correct review for these is silence, so any finding on them is noise. They are what keeps precision honest.
- At most 600 changed lines and 20 files, with at least one Python file changed.

## How comments were labelled

Each comment gets a `label`, a `category` if it is an issue, and a one-line `rationale`. The question asked for every comment:

> Would we want the review bot to have raised this?

The README's principle is precision over recall, so the bar is high.

**`issue`**: a problem in what the code does, or in how reliably it does it:

- a bug, a missed case, or behaviour that contradicts the stated intent;
- an API whose meaning is wrong or inverted (a `text=True` flag that switches to binary);
- an incomplete change (a deprecation warning that one import path bypasses);
- a test that will be flaky (real signals, sleeps, timeouts);
- security or performance problems.

**`not_issue`**: everything else, even when the reviewer was right to say it:

- documentation, changelog, docstring and comment wording or formatting, including docs that misdescribe behaviour (the code itself is fine);
- naming, style and design preferences where the code is correct either way;
- questions that do not assert a problem;
- process remarks (missing PR number, put this test in a different file).

Categories follow the domain model: `security`, `performance`, `correctness`, `style`.

## Known limits

- **One labeller.** The labels were written by Claude (an AI assistant) on 2026-10-21, at the project owner's request, and have not yet been reviewed by a second person. Disagreements are expected at the edges, especially for comments phrased as questions. Review the rationales and change any label you disagree with: the dataset fingerprint changes, so later reports show which version they ran on.
- **Human review is incomplete.** A finding that matches no comment may still be right. Precision measured here is a lower bound.
- **Partial checkouts.** Only changed files are stored, so tools see each file without the rest of its package. mypy runs with imports skipped for this reason.
- **Recency.** Examples come from each project's most recent activity, not a random sample of its history.
