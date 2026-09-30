# CodeReview Agent

An automated pull request reviewer that posts structured, grounded review comments on GitHub. Built as a LangGraph multi-agent pipeline with a deterministic analysis layer underneath and an evaluation harness measured against real human reviews.

> **Status:** in development. This README describes the target system and is written before the code, deliberately — if the description isn't compelling in one page, the design isn't finished. Sections marked _(planned)_ aren't built yet.

---

## What it does

When a pull request is opened or updated:

1. The diff is fetched and parsed into structured hunks.
2. Deterministic analysers run first (linting, type checking, security scanning, dependency audit).
3. A LangGraph pipeline routes the diff to specialised agents — security, performance, correctness, style — with the analyser output supplied as context.
4. A synthesiser merges findings, removes duplicates and anything a tool already reported, and ranks by severity.
5. A single structured review comment is posted back to the PR, with every finding tied to a file and line.

It never blocks a merge and never pushes code. It comments.

## What it is not

- Not a linter. If `ruff` or `mypy` catches it, the agent stays silent about it.
- Not a merge gate. Advisory only.
- Not a replacement for human review. It is a first pass that catches the things reviewers are tired of catching.

---

## The design principle: precision over recall

A review bot that produces noise gets muted within a week, and a muted bot has zero value regardless of how much it found. So the system is tuned to say less and be right, not to say more.

Consequences that run through the whole codebase:

- **Every finding must cite a file and a line.** A finding without a location is dropped before it reaches the synthesiser.
- **Every finding must cite evidence** — the specific code, or the analyser output that triggered it.
- **Anything a deterministic tool already reported is suppressed.** Duplicating the linter is the fastest way to be ignored.
- **Confidence thresholds are enforced at the synthesiser**, not the agent. Agents propose; the synthesiser decides what is worth a human's attention.
- **A hard cap on findings per review.** If everything is important, nothing is.

## What is deterministic and what is a model

The same rule as any well-built agent system: models read, summarise and explain; they never decide anything that can be computed.

| Concern | How |
|---|---|
| Parsing the diff into hunks | Deterministic |
| Linting, typing, security scanning, dependency audit | Real tools (`ruff`, `mypy`, `bandit`, `semgrep`, `pip-audit`) |
| Finding where a changed symbol is used elsewhere | AST parsing into a code graph |
| Judging whether a change is risky, and explaining why | Agent |
| Deduplication, ranking, suppression, final selection | Deterministic |
| Drafting the review prose | Agent |

Roughly a third of the pipeline calls a model. That ratio is the point.

---

## Architecture

```
src/reviewer/
├── domain/              CORE — pure, no I/O
│   ├── models.py          PullRequest, Diff, Hunk, Finding, Severity, Review
│   ├── suppression.py     dedup and tool-overlap rules (pure functions)
│   └── errors.py
│
├── ingestion/           EDGE
│   ├── webhook.py         GitHub event verification and parsing
│   ├── diff.py            unified diff -> structured hunks
│   └── files.py           fetch file contents at the PR head
│
├── analysis/            EDGE — deterministic analysers
│   ├── linters.py         ruff, mypy
│   ├── security.py        bandit, semgrep, pip-audit
│   └── report.py          normalise tool output into Finding objects
│
├── context/             EDGE — what else does this change touch
│   ├── graph.py           Neo4j: imports and call relationships
│   └── retrieval.py       related code for a changed symbol
│
├── agents/              EDGE — each returns validated findings
│   ├── base.py            structured output, retry, prompt versioning, cost
│   ├── security.py
│   ├── performance.py
│   ├── correctness.py
│   ├── style.py
│   └── synthesizer.py
│
├── graph/               ORCHESTRATION
│   ├── state.py           the typed state threaded through the pipeline
│   ├── nodes.py
│   ├── supervisor.py      routing — deterministic
│   └── build.py
│
├── publishing/          EDGE
│   └── github.py          post the review, handle re-delivery
│
├── storage/             EDGE — Postgres, Redis
├── evaluation/          golden dataset, metrics, regression runner
└── api/                 FastAPI: webhook endpoint, health, admin
```

Dependencies point inward. `domain/` imports nothing from the other packages.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph | Explicit state, deterministic routing, checkpointing, auditable |
| API | FastAPI | Async webhook handling, typed contracts |
| Models | Anthropic API | Structured output, tool use |
| Relational | PostgreSQL | Reviews, findings, cost records |
| Cache / queue | Redis | Idempotency keys, token budgets, rate limits, job queue |
| Code graph | Neo4j | Import and call relationships — a genuine graph query |
| Analysers | ruff, mypy, bandit, semgrep, pip-audit | The deterministic layer |
| Container | Docker | |
| Infrastructure | Terraform on AWS | Everything in code, no console clicking |
| Tracing | OpenTelemetry | One trace per review, spans per agent |

---

## Quickstart _(planned)_

```bash
git clone <repo> && cd codereview-agent
cp .env.example .env          # add ANTHROPIC_API_KEY and GitHub App credentials
docker compose up -d          # app, Postgres, Redis, Neo4j
alembic upgrade head
pytest
```

Point a GitHub App webhook at `/webhooks/github`, or replay a saved event:

```bash
python -m reviewer.cli replay fixtures/events/pr_opened.json
```

## Configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Model access |
| `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY` | App authentication |
| `GITHUB_WEBHOOK_SECRET` | Signature verification |
| `DATABASE_URL`, `REDIS_URL`, `NEO4J_URI` | Storage |
| `NEO4J_USER`, `NEO4J_PASSWORD` | Neo4j credentials, shared by the app and the container |
| `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD` | Postgres container setup; must match `DATABASE_URL` |
| `MAX_TOKENS_PER_REVIEW` | Hard cost ceiling per PR |
| `MIN_CONFIDENCE` | Suppression threshold |
| `MAX_FINDINGS` | Cap per review |

---

## Evaluation _(planned)_

The thing that makes this a system rather than a demo.

**Dataset.** Merged public PRs that received substantive human review. The human comments are the labels. Held in `data/golden/`, versioned, with the labelling rationale documented.

**Metrics.**

| Metric | What it measures |
|---|---|
| Precision | Of the findings raised, how many a reviewer would call valid |
| Recall against humans | Of the issues humans raised, how many were caught |
| Noise rate | Findings per 100 changed lines |
| Tool overlap | Findings a deterministic tool already reported (target: zero) |
| Location accuracy | Findings citing the correct file and line |
| Cost | Tokens and money per review |

Precision and noise rate are the ones that matter. High recall with high noise is a worse product than the reverse.

**Regression gate.** CI runs the evaluation on every change to a prompt file or agent, and fails the build if precision drops or noise rises beyond a threshold. Prompts are versioned files; every finding records the prompt version that produced it.

---

## Design decisions

Recorded in `docs/adr/`. The significant ones:

1. **LangGraph over a plain loop** — checkpointing and explicit state make the review reconstructable after the fact.
2. **Deterministic analysers run first** — they are faster, free and correct. Agents get their output as context and are instructed not to repeat it.
3. **The supervisor is pure Python** — routing depends on file types, diff size and analyser output, all of which are knowable without a model.
4. **Neo4j for the code graph** — "what else calls this function" is a traversal, and traversals are what graph databases are for.
5. **Never push code, never block merges** — trust is the constraint on adoption, not capability.
6. **One review comment, not many inline ones** — a bot that posts fifteen inline comments is experienced as spam.

---

## Roadmap

- [x] GitHub webhook ingestion
- [ ] Deterministic analysis layer
- [ ] Evaluation harness and golden dataset
- [ ] Agent pipeline
- [ ] Code graph context
- [ ] AWS deployment via Terraform
- [ ] Tracing and cost dashboards

## Licence

MIT.
