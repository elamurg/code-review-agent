# How CR-05 to CR-13 work, in plain words

Claude built tickets CR-05 to CR-13 so you can start at the agent layer (CR-14). This file explains what was built, ticket by ticket and file by file, in plain language. Words in **bold** the first time they appear are explained in the [glossary](#glossary) at the end.

If something here is still unclear, that is a problem with this file, not with you. Ask, and it gets rewritten.

---

## The big picture first

The project is a robot that reviews pull requests. Before any AI is involved (that starts at CR-14), the robot needs four abilities:

1. **Read a pull request.** Turn the raw text of a change into something the program understands. That is CR-05.
2. **Check the code with existing tools.** Run proven checkers (linters, security scanners) on the changed code, safely. That is CR-07 to CR-10.
3. **Decide what is worth saying.** Throw away duplicates, unsure guesses and noise. That is CR-06.
4. **Measure itself.** Compare what the robot says against what real human reviewers said, so you know if a change made it better or worse. That is CR-11 to CR-13.

### One example, followed through the whole system

Imagine someone opens a pull request that adds this line to `app.py`:

```python
value = undefined_name
```

Here is what happens to it:

1. GitHub sends a **diff**: text saying "line 3 of `app.py` was added, and it says `value = undefined_name`".
2. **CR-05** reads that text and produces an object meaning: file `app.py`, added line, new line number 3.
3. **CR-07** runs the tool ruff on `app.py`. Ruff says: "line 3: `undefined_name` is not defined". That becomes a **Finding**.
4. **CR-09** makes sure ruff ran inside a locked box, in case the code was malicious.
5. **CR-10** collects every tool's findings into one report and keeps only the ones on lines this PR added.
6. **CR-06** (later used by the agents) throws away duplicates and anything not worth a human's time.
7. **CR-11 to CR-13** check, across 46 real past PRs, how often the robot's findings match what real reviewers said.

Keep this example in mind as you read each ticket.

### Where the code lives

```
src/reviewer/
├── domain/        the rules of the product. No internet, no files, no tools. (CR-04, CR-06)
├── ingestion/     reading things that arrive from outside, like diffs. (CR-05)
├── analysis/      running the checking tools and collecting what they say. (CR-07 to CR-10)
└── evaluation/    the test set of real PRs, and measuring the robot against it. (CR-11 to CR-13)
```

The one rule about this layout: `domain/` never imports from the other folders. It is the core, and everything else depends on it, never the other way round.

---

## CR-05 · Reading a diff

**The problem.** GitHub describes a change as a block of text called a diff. The program needs to know, for every line, whether it was added, removed or unchanged, and its line number before and after. Getting a line number wrong means the robot comments on the wrong line, which is worse than not commenting at all.

**Everyday comparison.** A diff is like a document with tracked changes. This ticket is the code that reads the tracked changes and writes down exactly which lines moved where.

**What a diff looks like.**

```
diff --git a/app.py b/app.py        ← a new file section starts
--- a/app.py                         ← the old version of the file
+++ b/app.py                         ← the new version of the file
@@ -10,3 +10,4 @@                    ← a "hunk": old lines 10–12 become new lines 10–13
 unchanged line                      ← space = unchanged (exists in both)
-removed line                        ← minus = only in the old version
+added line                          ← plus = only in the new version
+another added line
```

**How the code reads it, step by step.**

1. Cut the text into one piece per file, at every `diff --git` line.
2. For each file, read the header lines to learn: was the file added, deleted, renamed, or changed? Is it binary (an image, for example)?
3. For each `@@` line (a **hunk**), read the four numbers. They say where the hunk starts and how many old and new lines it has.
4. Read exactly that many lines, numbering each one on the old side, the new side, or both.

**The clever part.** Step 4 counts lines instead of looking for the next `@@` or `---`. Why? Deleting a line of SQL that says `-- note` produces the diff line `--- note`, which looks exactly like the start of a new file. Counting can't be fooled by that.

**The safety net.** The parser builds the `Hunk` objects you wrote in CR-04, and those refuse to exist if their line numbers don't add up. So if the parser ever counts wrong, the program stops with an error instead of quietly putting a comment on the wrong line.

**Files**

| File | What it is |
|---|---|
| `src/reviewer/ingestion/diff.py` | The parser. `parse_diff(text)` is the one function other code calls. |
| `src/reviewer/ingestion/__init__.py` | Empty. It just tells Python that `ingestion` is a package. |
| `tests/ingestion/test_diff.py` | Tests: one example diff for every kind of change, broken diffs that must be rejected, and one test that makes a real git repository and parses real `git diff` output. |

---

## CR-06 · Deciding what is worth saying

**The problem.** A review robot that comments too much gets ignored, or switched off. So it needs firm rules for staying quiet.

**Everyday comparison.** An editor going through a pile of notes before passing them to the author. Notes that repeat each other are merged. Notes the editor isn't sure about are dropped. Notes a spell-checker already flagged are dropped. If there are still too many, only the most important ones are kept.

**The four rules, in the order they run.**

1. **Drop what a tool already said.** If ruff already flagged line 3 for a correctness problem, an agent saying the same thing about line 3 adds nothing.
2. **Drop unsure findings.** Each finding carries a **confidence** between 0 and 1. Anything below the threshold (default 0.7) is dropped.
3. **Merge duplicates.** Two findings about the same file, same line and same kind of problem are one issue. The most serious one is kept.
4. **Keep only the top few.** Sort by **severity** and keep at most 10.

**Why the order matters.** Imagine two findings about the same line. One is "critical, but only 30% sure"; the other is "medium, 90% sure". If you merged duplicates first, the critical one would win, then get dropped for being unsure, and you'd lose the good medium one with it. Dropping unsure findings first avoids that. A test checks this order.

**Why it's in `domain/`.** These rules don't touch the internet, files or tools; they only take findings in and give findings out. Rules like that belong in the core, and they are trivial to test.

**Files**

| File | What it is |
|---|---|
| `src/reviewer/domain/suppression.py` | The four rules, plus `suppress()` which runs them in order, and `SuppressionPolicy` which holds the settings (threshold, maximum count). |
| `tests/domain/test_suppression.py` | Tests for each rule on its own, the order test, and a test that runs all four together. |

---

## CR-07 · Running the linters (ruff and mypy)

**The problem.** Good tools for finding bugs already exist. The robot should use them, and turn whatever they say into the project's own `Finding` objects.

**Everyday comparison.** Hiring two specialist inspectors. The robot hands them the changed files, waits for their reports, and copies each report into its own standard form.

**The two tools.**

- **ruff** reads Python and spots mistakes like using a name that was never defined, or importing something and never using it.
- **mypy** checks **types**: for example, a function that promises to return text but actually returns a number.

**The big design idea: the Executor.** The tool code never starts programs itself. It writes down the command (like `ruff check app.py`) and hands it to an **Executor**, which runs it. There are two Executors:

- `LocalExecutor` runs the command as a normal program on your computer. It is fast, and used in tests.
- `DockerExecutor` (CR-09) runs the same command inside a locked box.

Because of this split, the same ruff code works in both places. Switching from "test" to "safe production" means swapping one object.

**Two safety habits.**

- **Time limits that clean up.** If a tool runs too long, the Executor kills it, and also kills any programs the tool itself started. Otherwise those could keep running forever in the background.
- **Our settings, not theirs.** Both tools ignore the settings file inside the pull request. A mypy settings file can name "plugins", which are code that mypy runs, so a stranger's settings file could make mypy run the stranger's code.

**Files**

| File | What it is |
|---|---|
| `src/reviewer/analysis/execution.py` | The Executor idea, `LocalExecutor`, and the time-limit handling. Also the error types `ToolFailed` and `ToolTimeout`. |
| `src/reviewer/analysis/base.py` | Shared helpers every tool uses: picking which files to check, running the command and checking it didn't crash, cleaning up file paths, and reading the code line to quote as **evidence**. |
| `src/reviewer/analysis/linters.py` | `RuffAnalyser` and `MypyAnalyser`. Each one builds its command, and has a function that turns the tool's output into `Finding`s. |
| `src/reviewer/analysis/__init__.py` | Empty package marker. |
| `tests/fixtures/analysis/broken_repo/app.py` | A file full of deliberate mistakes, so every tool has something to find. |
| `tests/analysis/conftest.py` | Gives the tests easy access to that broken file. |
| `tests/analysis/test_execution.py` | Tests that commands run, that input can be passed in, and that a time-out kills the tool and its child programs. |
| `tests/analysis/test_linters.py` | Tests that tool output is read correctly, and that both tools find the expected mistakes in the broken file. |
| `pyproject.toml` | Adds an `analysis` group of tools to install, tells this project's own checks to skip the deliberately broken files, and adds the `docker` and `network` test labels. |

**A lesson learned the hard way (CI failure, 2026-10-22).** The first version listed semgrep (see CR-08) in `pyproject.toml` next to the app's own libraries. Semgrep insists on exact versions of many other libraries. On your Mac, the installer quietly picked a very old semgrep to make everything fit. On GitHub's Linux machines, pip couldn't make it fit at all and failed, which broke every CI job. The fix was to keep semgrep out of the app's dependencies and install it separately (`pipx install semgrep`). The warning sign was there earlier, when your computer got semgrep 1.79 although 1.182 existed. **When the installer picks a surprisingly old version of something, ask why.**

---

## CR-08 · Running the security scanners

**The problem.** Some mistakes are security holes: passwords written into the code, unsafe ways of running commands, libraries with known vulnerabilities. Dedicated tools catch these.

**The three tools.**

- **bandit** looks for risky Python patterns, such as `shell=True` (lets attackers run commands), `pickle.loads` (can run hidden code), or a password written in the code.
- **semgrep** finds code that matches a pattern. The project has its own small list of six patterns, chosen to catch things bandit misses (for example, building SQL by pasting text into it). The list lives in a file in the repo because the safe box (CR-09) has no internet to download one.
- **pip-audit** checks the libraries a project depends on against a public list of known vulnerabilities.

**Why some bandit checks are switched off.** A few of bandit's checks fire on almost everything, for example "you used `assert`" in every test file, or "you imported `subprocess`". They're switched off because the robot's rule is to be quiet unless it matters.

**pip-audit needs care.** It's the only tool that must use the internet, to ask the vulnerability list. So it gets only the list of library versions, never the code, and it's told never to install anything. Lines like `requests>=2` (no exact version) are skipped, because checking them would mean downloading and running library code.

**Files**

| File | What it is |
|---|---|
| `src/reviewer/analysis/security.py` | `BanditAnalyser`, `SemgrepAnalyser`, `PipAuditAnalyser`, and the functions that read their output. Also finds pinned versions in `requirements.txt` and which line they're on. |
| `src/reviewer/analysis/rules/semgrep.yml` | The six semgrep patterns. |
| `tests/analysis/test_security.py` | Tests for reading each tool's output, plus each tool run on the broken file. The pip-audit test is labelled `network` because it uses the internet. |
| `tests/fixtures/analysis/broken_repo/requirements.txt` | Asks for an old library version (`jinja2==2.4.1`) with known vulnerabilities. |

---

## CR-09 · The sandbox (running tools in a locked box)

**The problem.** The robot runs tools on code written by strangers. The tools are only supposed to read that code, but tools have bugs and settings files can be abused. So assume the stranger's code *will* run, and make sure it can't do any harm.

**Everyday comparison.** Examining a suspicious package inside a sealed glove box. Whatever is inside can't get out, can't touch anything else, and the box is thrown away afterwards.

**What the box blocks.** Each tool runs in a fresh **Docker container** that is deleted afterwards. The container:

- has no internet, so nothing can be sent out;
- can read the code but not change it;
- has a memory limit, so it can't eat all of your computer's memory;
- has a limit on how many programs it can start, which stops a "fork bomb" (code that copies itself endlessly);
- doesn't run as the all-powerful root user;
- is killed after a time limit.

**How it was proven.** Four small hostile programs are run inside the box, and the tests check that each one fails to do harm:

| Hostile program | What it tries | What happens |
|---|---|---|
| `fork_bomb.py` | copy itself forever | capped, then killed at the time limit |
| `phone_home.py` | connect to the internet | blocked |
| `write_checkout.py` | change the code | blocked; nothing appears |
| `eat_memory.py` | use huge amounts of memory | killed |

There's also a test that each tool finds exactly the same things inside the box as outside it.

**Why these tests don't run by default.** They need Docker and take about 25 seconds, so they're labelled `docker` and skipped unless you run `pytest -m docker`.

**Files**

| File | What it is |
|---|---|
| `src/reviewer/analysis/sandbox.py` | `DockerExecutor`: builds the `docker run` command with all the locks, runs it, and kills the container on time-out. |
| `Dockerfile.analysis` | The recipe for the box's image: Python plus the five tools and the semgrep patterns. No app code, no secrets. |
| `tests/fixtures/sandbox/*.py` | The four hostile programs. |
| `tests/analysis/test_sandbox.py` | Checks the command has every lock, and the containment tests above. |

---

## CR-10 · One report from all the tools

**The problem.** Five tools each produce a list. The rest of the system wants one clean list, plus a record of which tools actually worked.

**Everyday comparison.** Five inspectors hand in separate reports. A coordinator merges them into one, removes the points two inspectors both made, drops comments about parts of the house that weren't renovated, and notes which inspectors didn't show up.

**What it does, step by step.**

1. Run every tool through the Executor. If one tool crashes, write that down and carry on with the others.
2. Put all findings in one list.
3. **Keep only findings on lines this PR added.** Tools read whole files, so they also complain about old code that this author didn't touch. Commenting on that is noise.
4. Merge duplicates using CR-06's rule (bandit and semgrep often flag the same line).
5. Sort, most serious first.

The result is an `AnalysisReport`. It also says whether it's `incomplete`, meaning a tool failed. That matters because "no findings" from a tool that crashed doesn't mean the code is clean.

**Why this ticket matters for you.** `render_for_agents(report)` turns the report into plain text. In CR-15 that text goes into the AI agent's instructions, with the message "the tools already found these; don't repeat them".

**Files**

| File | What it is |
|---|---|
| `src/reviewer/analysis/report.py` | `AnalysisReport`, the step that keeps only changed lines, the merge step, and `render_for_agents`. |
| `src/reviewer/analysis/pipeline.py` | `analyse(...)` runs every tool and builds the report. `default_analysers()` lists the tools. |
| `tests/analysis/test_report.py` | Tests for each step above. |
| `tests/analysis/test_pipeline.py` | The end-to-end test: a diff goes in, a report comes out, no AI involved. Also tests that one failing tool doesn't stop the rest. |

---

## CR-11 · The golden dataset (real examples to measure against)

**The problem.** To know whether the robot is any good, you need real pull requests where you already know the right answer.

**Everyday comparison.** An answer key for an exam. Each question is a real past PR. The answer is what real human reviewers said, and whether each of their comments pointed at a real problem.

**What's in it.** 46 merged pull requests from 11 well-known Python projects (Flask, pytest, click, httpx and others). Each one has:

- the diff exactly as the reviewer saw it;
- the changed Python files at that moment;
- every human review comment, each **labelled**.

**The labels.** Each comment is either:

- **issue**: a real problem we'd want the robot to catch (a bug, a backwards setting, a flaky test, a security problem); or
- **not_issue**: everything else, even if the reviewer was right to say it (wording, docs, style preferences, questions).

Every label has a one-line reason written next to it. In total: 66 comments, of which 11 are issues. 36 of the 46 PRs have no issues at all; the right review for those is silence.

**Why "as the reviewer saw it" matters.** By the time a PR is merged, the author has usually fixed what the reviewer pointed out. If we stored the final version, the reviewer's comment would point at the fix, not the problem. So the collector saves the version from the moment of the first review comment.

**Important: these labels are Claude's, not yours.** The backlog says labelling is your judgement, because doing it teaches you what good review looks like. Please read them (the reasons are in each `example.json`) and change any you disagree with.

**Files**

| File | What it is |
|---|---|
| `data/golden/<project>__<number>/` | One folder per PR: `example.json` (info and labelled comments), `diff.patch` (the diff), `files/` (the code). |
| `data/golden/README.md` | How PRs were chosen, the exact labelling rules, and the known weaknesses. Read this. |
| `src/reviewer/evaluation/collect.py` | The tool that downloaded the examples from GitHub. `python -m reviewer.evaluation.collect --repo owner/name` collects more. |
| `src/reviewer/evaluation/golden.py` | Loads the dataset, and refuses to load anything broken: an unlabelled comment, an issue with no category, a missing reason, or a comment pointing at a line that isn't in the diff. |
| `tests/evaluation/test_golden.py` | Tests for the loader, plus a test that the real dataset has 40 to 60 PRs and is fully labelled. |
| `tests/evaluation/test_collect.py` | Tests for how the collector picks PRs and comments. |
| `tests/evaluation/conftest.py` | A helper that writes small fake examples for the tests. |
| `.gitattributes` | Tells git never to change the dataset's bytes (for example, line endings). |
| `.pre-commit-config.yaml` | Skips the dataset in pre-commit, because the whitespace fixers would corrupt the stored diffs. |

---

## CR-12 · The scores (metrics)

**The problem.** "Better" needs to be a number.

**How a finding gets matched to a human comment.** A finding counts as matching if it's in the same file and within 3 lines of a human's issue. Each human issue can only be matched once.

**The scores, in plain words.**

| Score | Question it answers |
|---|---|
| **Precision** | When the robot says something, how often did a human also flag it? |
| **Recall** | Of the real problems humans flagged, how many did the robot catch? |
| **Noise rate** | How many comments does the robot make per 100 changed lines? |
| **Tool overlap** | How often does an AI agent repeat what a tool already said? Should be zero. |
| **Location accuracy** | When the robot matches a human, is it on the exact same line? |
| **Silent PRs with findings** | On PRs where the right answer is silence, how often did the robot speak anyway? |

The project's rule is that **precision matters more than recall.** A robot that's usually right but misses things is useful. A robot that catches everything but is mostly noise gets switched off.

**One honest caveat.** Precision here can only be too low, never too high. Humans miss things too, so a robot finding that matches no human comment might still be correct.

**Files**

| File | What it is |
|---|---|
| `src/reviewer/evaluation/metrics.py` | The matching, and the calculation of every score. |
| `tests/evaluation/test_metrics.py` | Tests, including one example worked out by hand in its comments. Try checking the arithmetic yourself. |

---

## CR-13 · Running the exam (the evaluation runner)

**The problem.** You need one command that runs the robot over all 46 PRs, scores it, and saves the result, so every future change can be compared against it.

**The command.**

```bash
python -m reviewer.evaluation.runner
```

**What it does.** For every PR in the dataset, it runs the robot, scores the result, and saves two files in `reports/evaluation/`:

- a `.md` file to read;
- a `.json` file for programs.

The report also records exactly what produced it: which version of the code, which version of the dataset, which version of each tool, and which AI prompt versions (none yet). Without that, "it got better" can't be checked. Tool versions matter because a newer ruff can find more, which would change the numbers without any change to your code.

**The baseline: the tools on their own, no AI.**

| Score | Result |
|---|---|
| Precision | 5.3% (1 of 19 findings matched a human) |
| Recall | 9.1% (1 of 11 issues caught) |
| Noise rate | 0.44 comments per 100 changed lines |
| Silent PRs with findings | 6 of 36 |

**What those numbers mean.** The tools are rarely *wrong*, but they mostly say things human reviewers don't care about. For example, they flag undefined names in black's test files, which are deliberately broken on purpose. The one match is a nice story: in a Flask PR, mypy found a type error on line 271, and a human had commented on line 270 about the exact same code. The tool saw the symptom; the human saw the cause.

**Why this ticket matters for you.** Your AI pipeline plugs in here. A pipeline is a function that takes one example and returns findings. Add yours to the `PIPELINES` list in `runner.py`, run `--pipeline your-name`, and compare against the table above.

**Files**

| File | What it is |
|---|---|
| `src/reviewer/evaluation/runner.py` | The runner: the list of pipelines, the scoring loop, and the report writer. |
| `tests/evaluation/test_runner.py` | Tests with a fake pipeline, so the scoring and the report can be checked exactly. |
| `reports/evaluation/2026-10-26T18-32-45Z-deterministic.*` | The baseline report. |
| `README.md` | Its Evaluation section now has the baseline table. |

---

## Commands you'll use

```bash
source .venv/bin/activate
pip install -e ".[dev]"       # the project and its tools
pipx install semgrep          # semgrep separately (see the CR-07 lesson)

pytest                        # normal tests
pytest -m docker              # sandbox tests (needs Docker running)
pytest -m network             # the test that uses the internet

python -m reviewer.evaluation.runner     # score the robot on all 46 PRs
```

---

## Glossary

- **Diff**: text describing what changed between two versions of some files.
- **Hunk**: one block of changes inside a diff, starting with a `@@` line.
- **Finding**: one thing the robot wants to say, always tied to a file and a line.
- **Severity**: how serious a finding is: low, medium, high or critical.
- **Confidence**: how sure the finder is, from 0 (guess) to 1 (certain). Tools are usually 1.
- **Evidence**: the line of code a finding is about, quoted so a human can check it.
- **Linter**: a tool that reads code looking for mistakes, without running it.
- **Types**: what kind of value something is (text, number, list). mypy checks they're used consistently.
- **Executor**: the part that actually runs a tool, either normally or inside the sandbox.
- **Docker container**: a sealed, temporary mini-computer running inside your computer.
- **Fork bomb**: a program that keeps copying itself until the machine runs out of resources.
- **Golden dataset**: real examples with known right answers, used to measure the robot.
- **Label**: our judgement on one human comment: `issue` or `not_issue`.
- **Baseline**: the score to beat. Every future change is compared against it.
- **CI**: the GitHub checks that run automatically on every pull request.
- **Dependency conflict**: two libraries demanding versions of a third library that can't both be satisfied.

---

## Things to try before CR-14

1. Read ten labels in `data/golden/*/example.json`. Do you agree? Change one, rerun the runner, and see the numbers move.
2. In `suppression.py`, swap the two lines in `suppress()` that drop unsure findings and merge duplicates. Run `pytest tests/domain`, and read why the test fails.
3. Run `pytest -m docker`, then read `sandbox.py` and match each line of the `docker run` command to the list in CR-09.
4. Pick one example from the baseline report and explain to yourself why the tool's finding did or didn't match the human.
