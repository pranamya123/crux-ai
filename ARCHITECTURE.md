# AI Weekly — System Architecture

**Document type:** Technical architecture
**Audience:** Engineers, technical reviewers, architecture interviewers
**Status:** Production
**Last updated:** May 2026 (post-Verifier agent integration)

---

## 0. Executive Summary

AI Weekly is an autonomous newsletter system that ships one issue every Thursday, written end-to-end by **eight specialized Anthropic Managed Agents** coordinating through a shared event log. The system aligns with the patterns described in Anthropic's *"Scaling Managed Agents: Decoupling the brain from the hands"* (April 2026) — agents (brains), tools (hands), and session state are independent abstractions that can fail, retry, or be replaced without disturbing each other.

**Key design choices:**

| Decision | Rationale |
|----------|-----------|
| **Multi-agent specialization** over single big-prompt | Each agent has one job, one quality bar, one criticality class. Easier to debug, swap, and reason about. |
| **Event-sourced session log** (Memory Stores JSONL) | Durable shared state. Resume-from-crash for free. Natural audit trail. |
| **Per-tool modules** (`tools/email.py`, etc.) | Independent failure boundaries. Each tool has its own retry/backoff. "Many hands" pattern. |
| **Hallucination grounding via Verifier agent** | Every URL and arXiv citation in the approved draft is checked against reality before delivery. Catches fabricated links before they reach subscribers. |
| **Citation enforcement in Writer + Critic** | Every factual claim must include a Markdown link. Critic rejects drafts that lack citations. Combined with the Verifier, this hardens output trust. |
| **GitHub Actions** as the orchestration runtime | Vercel Hobby has a 60s function cap; pipelines run 5–15 min. GitHub Actions gives 6 hours, free, no cold starts. |
| **Vercel** as the web layer only | Subscribe form, `/latest`, `/admin`, `/unsubscribe`. Auto-deploys on every push. |
| **Supabase** as the subscriber registry | Live source of truth. Orchestrator queries it on every run, so subscribe/unsubscribe takes effect immediately. |
| **Structured JSON logs + RunTracker** | Per-agent timing, token usage, retry counts. Logs queryable post-hoc. |

**Operational profile:**
- ~$2 per run (occasional spikes to $4 on Critic retries)
- ~10 minutes wall-clock per run
- Zero touch ops: subscribe → live; orchestrator → autonomous
- 8 agents × 1 shared session × 6 logical steps × 4 custom tools

---

## 1. Problem Statement

### What we wanted to learn

The project's primary goal is **education through production**: build a real, end-to-end system on Anthropic Managed Agents to internalize the patterns that matter (and form opinions on which patterns don't).

A weekly AI newsletter was chosen because it exercises every interesting capability:
- **Multi-stage pipeline** (research → evaluate → write → critique → verify → deliver) — true multi-agent
- **Long-horizon execution** (10+ minutes, multi-step, asynchronous)
- **Quality gates** (a Critic agent that can reject the Writer's draft)
- **Hallucination grounding** (a Verifier agent that confirms every URL and arXiv citation actually exists before delivery)
- **Cross-session memory** (Memory Agent reads what was covered last week)
- **External integrations** (SMTP, web research, subscriber DB)
- **Cost sensitivity** (every run costs real money — forces good engineering)

If we'd picked a single-agent task (e.g., "summarize this article"), there would be nothing interesting to design.

### What "good" looks like

A successful design should demonstrate:

1. **Clean failure modes** — if any one agent silently exits, the pipeline still ships an issue (with the failure visibly logged).
2. **Resume-from-crash** — if the orchestrator dies mid-run, restarting from the same `session_id` picks up at the last completed step.
3. **No external state coupling** — the session log is a single durable structure. No "did this side-effect happen?" guessing.
4. **Observability without tooling investment** — structured logs to stdout work without Datadog or any vendor.
5. **Cost transparency** — token usage per agent visible in run summaries, so optimization is data-driven, not vibes.
6. **Operational quietness** — once deployed, no human touches it weekly.

---

## 2. Design Principles

These guided every architectural choice. They are not invented; they are lifted directly from Anthropic's Managed Agents writings and standard distributed systems practice.

### 2.1 Decouple brain from hands from session

This is the central insight from Anthropic's April 2026 article:

> Managed Agents follow [the OS abstraction pattern]. We virtualized the components of an agent: a session (the append-only log of everything that happened), a harness (the loop that calls Claude and routes Claude's tool calls to the relevant infrastructure), and a sandbox (an execution environment where Claude can run code and edit files). This allows the implementation of each to be swapped without disturbing the others.

In our system:

| Component | What it is | Where it lives |
|-----------|------------|----------------|
| **Brain** | A Managed Agent (system prompt + model + tools) | Anthropic platform |
| **Hand** | A custom tool (`emit_event`, `get_events`, `send_email_smtp`) | `tools/*.py`, executed by the orchestrator |
| **Session** | The shared event log | Memory Stores JSONL files (`/mnt/memory/session_{id}.jsonl`) |
| **Harness** | The orchestrator loop | `orchestrator_v2.py` running on GitHub Actions |

Each of these can fail, be retried, or be swapped without the others noticing. If the orchestrator dies, a new one wakes up, reads the session, resumes. If a tool times out, the agent gets a tool-error result and the harness can retry. If a brain misbehaves, swap its system prompt in the Console — no code change.

### 2.2 Cattle, not pets

Original sin in agent systems: treating the runtime as a long-lived stateful process. In a "pet" architecture, when the container dies, the conversation is lost; when an agent fails, you have to nurse it back.

We aggressively prevent this:

- **Orchestrator process is stateless.** All state lives in the session log. A fresh process can resume any session.
- **Each agent run creates a fresh Managed Agents session.** No reuse, no implicit state.
- **Each tool call is idempotent at the boundary.** `emit_event` appends; repeating it inserts a duplicate (acceptable: the orchestrator filters by event type, not count).
- **GitHub Actions runner is ephemeral.** Each weekly run starts on a clean Ubuntu VM.

The tradeoff: we pay setup cost on every run. We accept this because the system runs once a week — the setup cost is invisible, the reliability gain is enormous.

### 2.3 The session log is the source of truth, not the agent's context

When designing multi-agent systems, the temptation is to pass state through Claude's context window — let the agent "remember" what previous agents did. This breaks under any failure: rate limits, retries, re-runs, prompt engineering changes.

Our orchestrator never relies on context. Every agent's output is **emitted as an event**. Downstream agents **read events**, not prior conversations. This means:

- An agent can run alone, in any order, for testing — as long as its prerequisite events exist.
- Mid-pipeline crashes don't lose work; the events are already durable.
- We can replay the full pipeline from logs by inspecting the event stream.

This is the **event sourcing pattern**, applied to multi-agent coordination.

### 2.4 Quality gates over post-hoc fixes

The Writer/Critic loop (max 3 attempts) is the only mechanism preventing low-quality issues from being sent. We chose a **synchronous critic in the loop** rather than:

- ❌ Post-publication editorial review (humans don't scale to weekly)
- ❌ A single super-prompt that "writes well the first time" (model output quality is variable)
- ❌ Multiple draft generation with a final picker (3× cost; hard to define "best")

The Critic adds ~30% to the run cost but catches roughly half the bad drafts. This is a deliberate cost/quality trade.

### 2.5 Fail loud, log structured, recover automatic

Three concrete habits:

1. **Auto-insert placeholder events** when an agent silently exits. The downstream pipeline keeps moving; the failure is visible in logs and run summaries.
2. **Per-agent criticality** — `delivery` is critical (must succeed); `papers_researched` is optional (zero papers is acceptable).
3. **Retries are bounded and observable** — three attempts max, exponential backoff, every retry logged with the prior error.

We do not silently retry forever. We do not silently swallow errors. We do not require humans to discover failures.

---

## 3. System Overview

```
                  ┌──────────────────────────────────────┐
                  │  GitHub Actions Cron                 │
                  │  Schedule: 0 9 * * 4 (Thu 9am UTC)   │
                  │  Runtime: ubuntu-latest, 30 min cap  │
                  └──────────────┬───────────────────────┘
                                 │ runs python3 orchestrator_v2.py
                                 ▼
   ┌───────────────────────────────────────────────────────────────┐
   │  OrchestratorV2 (host process; stateless)                     │
   │  ──────────────────────────────────────────────────────────   │
   │  • Generates / resumes shared_session_id                      │
   │  • Drives 6 logical steps in sequence (parallel where useful) │
   │  • Routes 4 custom tool types to handler modules              │
   │  • Maintains StructuredLogger + RunTracker (observability)    │
   │  • Persists run summary to runs/{session_id}.json             │
   └─────────────────────────────────────────────┬─────────────────┘
                                                 │
   ┌──────────────────────────────────┐          │ creates fresh session
   │  Anthropic Managed Agents        │ ◄────────┘ per agent (cattle)
   │  ─────────────────────────────   │
   │  8 specialized agents            │
   │  Each: model + system prompt +   │
   │  tool config + criticality       │
   └────────────┬─────────────────────┘
                │ stream events back; emit custom_tool_use
                ▼
   ┌──────────────────────────────────┐    ┌──────────────────────┐
   │  Tools (hands; per-tool module)  │    │  Memory Stores       │
   │  ─────────────────────────────   │    │  ──────────────────  │
   │  tools/memory_store.py    ──────►│ ──►│  /mnt/memory/        │
   │    emit_event, get_events        │    │    session_{id}      │
   │  tools/email.py           ──────►│    │      .jsonl          │
   │    send_email_smtp               │    │  (append-only event  │
   │  tools/subscribers.py     ──────►│    │   sourcing log)      │
   │    get_subscribers (Supabase)    │    └──────────────────────┘
   │  tools/verifier.py        ──────►│                            
   │    verify_links (HEAD + arXiv)   │                            
   └────────────┬─────────────────────┘
                │
                ├─► SMTP (Gmail) ──► subscriber inboxes
                │
                └─► commits latest_issue.html back to repo
                                 │
                                 ▼
   ┌──────────────────────────────────────┐
   │  Vercel (web layer; auto-deploy)     │
   │  ──────────────────────────────────  │
   │  Flask app (app.py via index.py)     │
   │  • GET  /                subscribe   │
   │  • POST /api/subscribe   add (50-cap)│
   │  • GET  /unsubscribe     remove      │
   │  • GET  /latest          serve issue │
   │  • GET  /admin           list emails │
   │                                      │
   │  Reads/writes Supabase 'subscribers' │
   │  Serves latest_issue.html from repo  │
   └──────────────────────────────────────┘
```

---

## 4. Component Deep Dive

### 4.1 The eight agents (brains)

Each agent is configured in the Claude Console with a model assignment, system prompt, and tool list. The Console is the source of truth for prompts; this repo references them by `agent_id`.

| # | Agent | Model | Role | Emits | Reads | Criticality |
|---|-------|-------|------|-------|-------|-------------|
| 1 | **Memory** | Haiku 4.5 | Read prior runs' coverage so we don't repeat | `covered_topics` | (none — reads prior runs' events) | Optional |
| 2 | **Research Launches** | Opus 4.7 | Find AI ecosystem developments past 7 days | `launches_researched` | `covered_topics` | Critical |
| 3 | **Research Papers** | Opus 4.7 | Find actionable AI research past 7 days | `papers_researched` | `covered_topics` | Optional |
| 4 | **Evaluator** | Opus 4.7 | Score & rank with transparent rubric (relevance/depth/novelty 1–10 each) | `items_evaluated` | research events | Critical |
| 5 | **Writer** | Opus 4.7 | Draft the brief in markdown with mandatory citations on every claim | `draft_written` | `items_evaluated` + (on retry) `critic_rejection` or `verification_failed` | Critical |
| 6 | **Critic** | Opus 4.7 | Review for quality, banned-words, style, and citation presence | `draft_approved` or `critic_rejection` | latest `draft_written` | Critical |
| 7 | **Verifier** | Haiku 4.5 | Verify every URL and arXiv citation in the approved draft actually exists | `verification_passed` or `verification_failed` | latest `draft_approved` | Critical |
| 8 | **Delivery** | Haiku 4.5 | Render & send via SMTP | `email_sent` | `draft_approved` + `verification_passed` | Critical |

**Model tiering rationale:**
- **Opus 4.7** for cognitive work that materially affects output quality (research judgment, evaluation, writing, critique).
- **Haiku 4.5** for mechanical work where quality is about correctness, not insight (memory pull, SMTP send).
- Tiering cuts cost ~40% vs. all-Opus, with no observable quality drop on Haiku-assigned tasks.

**Parallelism:**
Agents 2 and 3 (Research Launches + Research Papers) run **concurrently** via a `ThreadPoolExecutor`. They share no state during research; they only converge at the Evaluator. This cuts the longest single step's wall-clock by ~half.

**Retry semantics in the Writer/Critic loop:**

The Writer/Critic loop is **state-driven**, not turn-driven. It counts events:

```
drafts       = count_events("draft_written")
rejections   = count_events("critic_rejection")

if drafts == 0 or rejections >= drafts:
    → Writer needs to write (initial pass or addressing rejection)
elif drafts > rejections:
    → Critic needs to review the latest draft
```

Max 2 rejections (3 total Writer attempts). If the Critic rejects the third draft, we abort before delivery — better to skip a week than send a bad issue. (This has happened zero times in production but is the right default.)

### 4.2 The orchestrator (harness)

`orchestrator_v2.py` is a stateless Python process that:

1. Generates or resumes a `shared_session_id`.
2. Initializes a `StructuredLogger` and `RunTracker` for observability.
3. Drives the six logical steps (`memory`, `research`, `evaluate`, `write_critique`, `verify`, `deliver`) sequentially.
4. For each step, checks the session log for terminal events; **skips already-completed steps** (resume support).
5. Spawns a fresh Managed Agents session per agent run.
6. Streams events from each session, routing `custom_tool_use` events to the appropriate `tools/` module.
7. After each agent completes, records timing and token usage in the `RunTracker`.
8. Persists the run summary to `runs/{session_id}.json` on completion.

The orchestrator is **single-process but multi-step replayable**. If the GitHub Actions runner is killed (e.g., 30-min timeout exceeded), a new run with `--session-id <id>` resumes from the last completed step. In practice this never triggers — runs complete in 10–15 min, well under the 30-min cap — but the capability exists.

**Why one process, not five:**
The original design had each step as a separate Vercel function chained via async HTTP. We abandoned that because:
1. **Hobby plan limit:** Vercel Hobby caps functions at 60s, but most agents take 60–180s.
2. **Async chains are fragile:** if the next-step trigger fails, the pipeline stalls invisibly.
3. **Chaining adds no value here:** GitHub Actions has 6 hours of headroom. We're not gaining horizontal scale by splitting.

The capability still exists in code (`orchestrator_v2.py --step <name>`) for environments with hard timeouts, but the production path is the single-process loop.

### 4.3 The session log (durable shared state)

The session log is a per-run JSONL file:

```
/mnt/memory/session_newsletter_20260507_090000_a1b2c3d4.jsonl
```

Each line is one event:

```json
{
  "id": "uuid",
  "session_id": "newsletter_20260507_090000_a1b2c3d4",
  "agent_name": "research_launches",
  "event_type": "launches_researched",
  "data": { "launches": [...] },
  "created_at": "2026-05-07T09:04:12.123456Z"
}
```

**Location resolution:**
1. **Production (Managed Agents environment):** `/mnt/memory/` — Anthropic's workspace-scoped persistent storage.
2. **Local fallback:** `./memory_local/` — for `python3 orchestrator_v2.py` runs from a developer machine.

Both paths use identical code. The orchestrator picks whichever is writable.

**Why JSONL, not SQLite or a database:**
- **Append-only is the right primitive.** No updates, no deletes — every event is immutable.
- **Streamable.** Tools can read line-by-line without loading the whole file.
- **Trivial to inspect.** `cat session_*.jsonl | jq '.event_type'` shows the full run.
- **Zero schema migrations.** New event types just appear; old code ignores them.
- **Portable.** Same files work in Memory Stores, S3, local disk, anywhere.

The trade-off: no native indexes. With ~30 events per run, this is not a problem. If event count grew 100×, we'd add an index file or migrate to SQLite.

**Event-type catalog:**

| Event type | Emitted by | Means |
|------------|------------|-------|
| `covered_topics` | Memory | Topics covered in recent prior issues |
| `launches_researched` | Research Launches (or orchestrator fallback) | Candidate ecosystem items found |
| `papers_researched` | Research Papers (or orchestrator fallback) | Candidate research papers found |
| `items_evaluated` | Evaluator | Ranked/filtered set with scoring breakdown |
| `draft_written` | Writer | A complete brief in markdown |
| `critic_rejection` | Critic | Specific issues that must be addressed |
| `draft_approved` | Critic | Brief passed quality + citation checks |
| `verification_passed` | Verifier | All URLs and arXiv citations confirmed to exist |
| `verification_failed` | Verifier | One or more URLs were unreachable or fabricated |
| `email_sent` | Delivery | Issue went out; pipeline complete |

The orchestrator's resume logic depends only on the **terminal event** of each step (e.g., `draft_approved`, `email_sent`). It does not care how many drafts or rejections preceded approval — those exist in the log for debugging.

### 4.4 Tools (hands)

Each tool is its own module under `tools/`. This is the "many hands" pattern — independent failure boundaries, per-tool retry, swappable.

```
tools/
├── __init__.py          # exports
├── memory_store.py      # emit_event, get_events    — session log access
├── email.py             # send_email_smtp           — SMTP delivery
├── subscribers.py       # get_subscribers           — Supabase fetch
└── verifier.py          # verify_links              — URL + arXiv existence check
```

**`tools/memory_store.py`:**
- `emit_event(session_id, agent_name, event_type, data)` — appends one JSONL line
- `get_events(session_id, agent_name?, event_type?, limit?)` — reads, filters, sorts
- Two helpers used by orchestrator: `has_event()`, `count_events()`
- Wrapped in `@retry_with_backoff(max_attempts=3, initial_delay=0.5)` — retries on transient I/O errors

**`tools/email.py`:**
- `send_email_smtp(args)` — sends per-recipient HTML emails via SMTP
- Per-recipient rendering: each email gets its own personalized unsubscribe link
- Side effects:
  1. Saves a snapshot to `latest_issue.html`/`.md`/`_meta.json` (served by `/latest`)
  2. Rewrites in-doc anchors to absolute URLs (Gmail strips `id` attributes)
- Wrapped in `@retry_with_backoff(max_attempts=2, initial_delay=2.0)` — SMTP retries are slower

**`tools/subscribers.py`:**
- `get_subscribers()` — returns the live recipient list with this priority:
  1. Supabase `subscribers` table (live source of truth)
  2. `RECIPIENT_EMAILS` env var (fallback if Supabase unreachable)
- This means subscribe/unsubscribe via the website takes effect on the **next run**, with no manual env var update.

**`tools/verifier.py`:**
- `verify_links(args)` — extracts every Markdown link from a document and checks each URL exists
- Two verification paths:
  1. Standard URLs: HTTP `HEAD` request with `GET` fallback for servers that reject HEAD (5xx → fail; 405 → fall through to GET)
  2. arXiv URLs: queries the arXiv API directly to confirm the paper ID actually exists rather than just that the URL responds
- Returns structured result: `{all_valid, checked, valid: [...], invalid: [{url, reason}]}`
- Wrapped in `@retry_with_backoff(max_attempts=2, initial_delay=1.0)` — slow networks need a second pass
- Cost: roughly two cents per run; mostly HTTP, no LLM tokens

**Why one module per tool:**

The Anthropic article frames each "hand" as `execute(name, input) → string` — a uniform interface but a separate execution boundary. Putting each tool in its own file:

- Lets us version and replace each independently.
- Gives each its own retry policy (Memory Stores vs. SMTP have very different error profiles).
- Makes testing trivial — import the module, call the function, no orchestrator needed.
- Mirrors the "tools as cattle" philosophy: a tool failure is a per-tool concern, not a system concern.

### 4.5 Subscriber registry (Supabase)

The Flask app and the orchestrator both read/write to one Supabase table:

```sql
CREATE TABLE subscribers (
  id     BIGSERIAL PRIMARY KEY,
  email  TEXT      NOT NULL UNIQUE
);
```

**Write path** (Flask `/api/subscribe`):
- Validate email format
- Check for duplicate
- Enforce 50-subscriber cap (Hobby tier sanity)
- Insert

**Read paths:**
- Flask `/admin` — display the current list (read-only HTML page)
- Orchestrator `tools/subscribers.py` — fetch the list at delivery time

**Why Supabase, not a flat file or env var:**
- Concurrent writes (multiple subscribers signing up at once) need transaction safety.
- The Flask app is multi-process on Vercel; coordination through the database is mandatory.
- The 50-cap query is cheap with `count='exact'`.
- Free tier is plenty for this workload (max ~50 rows).

This is the **only piece of external infrastructure** in the system besides Anthropic and SMTP. We deliberately did not adopt Supabase for the session log (Memory Stores serves that), so the dependency is contained.

### 4.6 Web layer (Vercel + Flask)

Standard Flask app, deployed via Vercel's Python runtime (`index.py` → `from app import app`). Routes:

| Method | Route                       | Purpose |
|--------|-----------------------------|---------|
| GET    | `/`                         | Subscribe form (single-page) |
| POST   | `/api/subscribe`            | Add to Supabase, with cap enforcement |
| GET    | `/unsubscribe?email=X`      | Delete from Supabase, styled confirmation |
| GET    | `/latest`                   | Serve `latest_issue.html` (or 404 page if not yet generated) |
| GET    | `/admin`                    | Plain page listing current subscribers (no auth — relies on URL obscurity for now) |

**`/latest` content lifecycle:**
1. Orchestrator runs (Thursday, GitHub Actions).
2. Delivery agent calls `send_email_smtp`, which writes `latest_issue.html` to disk.
3. The GitHub Actions workflow commits `latest_issue.html` back to the repo.
4. Vercel auto-deploys on push.
5. New `/latest` is live within ~60 seconds of email send.

This is a slightly unusual pattern (using git as the deployment channel for runtime-generated content), but for a weekly cadence it's perfect: free, audited, version-controlled, and rolls back easily.

### 4.7 Observability (`observability.py`)

Two primitives, both deliberately minimal:

**`StructuredLogger`** — emits one JSON object per log line:
```json
{
  "ts": "2026-05-07T09:04:12.123456Z",
  "level": "INFO",
  "session_id": "newsletter_...",
  "agent": "research_launches",
  "message": "agent_end: research_launches",
  "elapsed_sec": 142.7,
  "tool_calls": 4,
  "input_tokens": 45000,
  "output_tokens": 8200,
  "cache_read_tokens": 320000
}
```

Logs go to **stdout** (where GitHub Actions captures them) and to **`logs/session_{id}.log`** (uploaded as workflow artifacts, 30-day retention).

**`RunTracker`** — in-memory accumulator that produces a single run summary:
```json
{
  "session_id": "...",
  "total_elapsed_sec": 487.3,
  "agent_timings": { "memory": 12.1, "research_launches": 145.2, ... },
  "agent_status": { "memory": "success", "research_launches": "success", ... },
  "agent_tokens": { "research_launches": { "input": 45000, "output": 8200, ... } },
  "totals": { "input_tokens": 380000, "output_tokens": 95000, "cache_read_tokens": 1200000 },
  "errors": [],
  "success": true
}
```

Persisted to `runs/{session_id}.json` after every run. Recent N can be listed via `get_recent_runs()` for a future runs-history endpoint.

**Why we did not adopt Datadog / Sentry / OpenTelemetry:**
- Cost: zero subscribers' worth of free tier doesn't justify any vendor.
- Cardinality: ~4 runs/month, ~30 events/run. We don't have a metrics scale problem.
- Lock-in: structured JSON to stdout is portable to any tool we'd adopt later.

We will revisit if the system grows to multiple newsletters or sub-daily runs.

### 4.8 Error handling & retry (`retry.py`)

Three mechanisms, each operating at a different scope:

**Tool-level retry** (`@retry_with_backoff`):
- Wraps each `tools/*` function
- 2–3 attempts depending on the tool (SMTP gets 2; Memory Stores get 3)
- Exponential backoff (1s → 2s → 4s, capped at 30s)
- Recognizes Anthropic's transient errors (rate limits, 5xx, "overloaded") via `is_retryable_anthropic_error()`
- Non-retryable errors (auth failures, 400s) fail immediately

**Agent-level timeout & criticality** (in `orchestrator_v2.py`):

```python
AGENT_CRITICALITY = {
  "memory":            "optional",
  "research_launches": "critical",
  "research_papers":   "optional",
  "evaluator":         "critical",
  "writer":            "critical",
  "critic":            "critical",
  "delivery":          "critical",
}

AGENT_TIMEOUTS = {
  "memory": 120, "research_launches": 300, "research_papers": 300,
  "evaluator": 180, "writer": 300, "critic": 180, "delivery": 120,
}
```

If an agent exceeds its timeout, the AgentRunner breaks the event stream and records a failure. The orchestrator decides what to do based on criticality:
- **Optional** failures → log and continue (e.g., zero papers is acceptable)
- **Critical** failures → log and abort before delivery

**Pipeline-level fallbacks**:
The most subtle defense is the **post-condition placeholder**. If the Research Launches or Research Papers agent silently exits without emitting its terminal event (a real bug observed in 2 of 3 early runs), the orchestrator inserts an empty placeholder event:

```python
{
  "event_type": "papers_researched",
  "data": { "papers": [], "auto_inserted": True, "note": "..." }
}
```

This unblocks the Evaluator (which depends on the terminal event existing), preserves the failure in the log, and lets the pipeline ship a (lighter) issue rather than dropping the week.

**Why we don't have a global "alert on failure" yet:**
GitHub Actions emails the workflow owner on job failure. That's the alert. We have not added Slack/PagerDuty because the system runs once a week and the GitHub email is sufficient.

### 4.9 Implicit contracts between systems

The Writer's markdown output is consumed by the email renderer (`email_renderer.py`), which parses it with regex to identify section headings and per-item entries. This forms an **implicit structural contract** between two systems:

- **Producer:** Writer agent system prompt, which dictates the markdown shape.
- **Consumer:** `email_renderer.py`, whose regex assumes that shape.

**The contract specifically requires:**
- H1 title: `# AI Weekly — <theme>`
- Section heading containing "what shipped" → routed to `company_items`
- Section heading containing "research" or "worth knowing" → routed to `research_items`
- Each item under a section: `### N. Title` (numbered H3, period after the number)
- Item body paragraph(s) directly below the H3

**Why it matters:** When the Writer's prompt was updated late in the project to use bold-link headings instead of numbered H3s, the pipeline kept passing every internal check (Writer produced output, Critic approved, Verifier confirmed every link resolved, Delivery sent) but the email arrived empty under the section headers because the renderer's regex no longer matched.

**The defense:** A smoke test at `scripts/test_render.py` (or equivalent) feeds a sample brief through the renderer and asserts the rendered HTML contains the expected items before any prompt change ships. The Critic's `STRUCTURE CHECKS` section also enforces the format from the producer side, so a structurally invalid draft is rejected before it reaches Verify or Delivery.

**The general principle:** Anywhere a downstream system parses an LLM's output, the prompt and the parser form a contract that must be tested explicitly. Prompt iteration breaks consumers silently otherwise.

### 4.10 Hallucination grounding (Verifier loop)

The risk that a Critic-approved draft could still contain a fabricated URL or a non-existent arXiv paper is real and high-impact: a single hallucinated citation in a research newsletter destroys reader trust permanently. The Critic catches a lot of bad writing but cannot verify factual claims against external reality. The Verifier closes that gap.

The flow looks like this:

1. The Critic approves a draft (`draft_approved` event emitted).
2. The orchestrator runs the **Verifier** agent.
3. The Verifier reads the latest `draft_approved` event and calls `verify_links` on the brief content.
4. `verify_links` extracts every Markdown link, runs HEAD requests on each URL, and queries the arXiv API for any arXiv IDs it finds.
5. The Verifier emits one of two terminal events:
   - `verification_passed` — every URL and citation resolved successfully; the Delivery agent runs.
   - `verification_failed` — one or more URLs are unreachable; the orchestrator loops back to the Writer with the list of bad URLs.
6. On a `verification_failed` event, the Writer reads the `invalid_urls` array and either replaces each bad URL with a valid one from the `items_evaluated` event or rewrites the surrounding sentence to remove the citation entirely. The Critic then re-approves; the Verifier re-checks.

**Bounded retry:** Up to two verification failures are tolerated (`MAX_VERIFICATION_RETRIES = 2`). Beyond that, the orchestrator aborts the run before delivery — better to skip a week than ship a verified-fabricated issue.

**Citation enforcement complements this defense.** The Writer's system prompt requires every factual claim to include a Markdown link, and the Critic explicitly rejects drafts that lack citations. Together, the prompt-level requirement and the runtime check form a two-layer defense against hallucinated facts: the Critic ensures citations exist, the Verifier ensures the citations resolve.

**Why a separate agent rather than a tool the Critic calls:**
- The Verifier has a different criticality and timeout profile than the Critic (mechanical work, faster timeout).
- It runs on Haiku rather than Opus since the work is deterministic.
- Keeping it a separate agent means the Critic's prompt doesn't need to know about HTTP semantics or arXiv API behavior.
- The verification step is now a first-class part of the pipeline state machine, with its own terminal events and resume support.

---

## 5. End-to-End Data Flow

A single Thursday's run, in time order:

```
T+0:00    GitHub Actions cron fires (0 9 * * 4)
T+0:01    Ubuntu runner provisioned, repo cloned, deps installed
T+0:30    `python3 orchestrator_v2.py` starts
          ↓ generates session_id = "newsletter_20260507_090030_a1b2c3d4"
          ↓ logger + tracker initialized

T+0:31    Step 1: MEMORY AGENT (Haiku, ~30s)
          • Reads /mnt/memory/ for events from prior session_ids
          • Extracts topics covered in last 12 weeks
          • emit_event("covered_topics", {...})

T+1:00    Step 2: RESEARCH (parallel, Opus × 2, ~3 min)
          ┌─ Research Launches: web search, filter, evaluate
          │  emit_event("launches_researched", { launches: [5–7 items] })
          └─ Research Papers: web search, filter, evaluate
             emit_event("papers_researched", { papers: [2–3 items] })
          (post-condition: orchestrator inserts placeholder if either is missing)

T+4:30    Step 3: EVALUATOR (Opus, ~2 min)
          • get_events() → reads launches, papers, covered_topics
          • Scores each item on relevance/depth/novelty (1–10 each)
          • Drops items with total < 18, drops covered duplicates
          • emit_event("items_evaluated", { selected_launches, selected_papers, rejected_items, summary })

T+6:30    Step 4: WRITER ↔ CRITIC LOOP (Opus × N, ~3–6 min)
          • Writer: get_events("items_evaluated") → draft with mandatory citations →
                    emit_event("draft_written")
          • Critic: get_events("draft_written") → review (quality + citations present) →
              ◦ If approved: emit_event("draft_approved")
              ◦ If rejected: emit_event("critic_rejection") → loop back to Writer
          (max 2 rejections; 3 total Writer attempts)

T+10:30   Step 5: VERIFY (Haiku, ~30s)
          • Verifier: get_events("draft_approved") → call verify_links on brief →
              ◦ All URLs resolve + arXiv IDs valid: emit_event("verification_passed")
              ◦ One or more invalid: emit_event("verification_failed") with bad URLs →
                loop back to Writer (Writer fixes URLs, Critic re-approves, Verifier re-checks)
          (max 2 verification failures before abort)

T+11:00   Step 6: DELIVERY (Haiku, ~1 min)
          • get_events("draft_approved") + verification_passed exists → final markdown
          • Calls send_email_smtp:
              ◦ tools/subscribers.get_subscribers() → live list from Supabase
              ◦ tools/email.handle_send_email_smtp(subject, markdown):
                  - Per-recipient HTML render (personalized unsubscribe)
                  - Save snapshot: latest_issue.{html,md,json}
                  - SMTP send to all subscribers
          • emit_event("email_sent", { recipients, subject })

T+12:00   Orchestrator finalizes:
          • RunTracker.persist() → writes runs/{session_id}.json
          • briefs/{session_id}_log.json written (compact view of run)

T+12:05   GitHub Actions workflow:
          • Commits latest_issue.{html,md,json} to repo
          • Uploads logs/, runs/, briefs/ as workflow artifacts (30-day retention)
          • Pushes to main

T+12:30   Vercel auto-deploys the new commit
          • /latest now serves the new issue
          • Subscribers see new edition in their inbox
```

---

## 6. Failure Modes & Recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Anthropic API rate-limited | Retry decorator catches 429 | Exponential backoff, 3 attempts |
| Agent silently exits without emitting | Post-condition check after step | Auto-insert placeholder event; pipeline continues |
| Agent exceeds per-agent timeout | AgentRunner timer breaks stream | Marked failed; criticality decides if pipeline aborts |
| Critic rejects 3 times | Loop counter hits max_retries | Pipeline aborts before delivery; week is skipped |
| Verifier reports invalid URLs | `verification_failed` event | Loop back to Writer with bad URLs; Critic re-approves; Verifier re-checks (max 2 retries) |
| Verifier exceeds 2 retries (URLs still bad) | `MAX_VERIFICATION_RETRIES` exhausted | Pipeline aborts before delivery; week is skipped |
| GitHub Actions runner killed mid-run | Workflow shows failure | Re-trigger with `--session-id <id>` resumes from last terminal event |
| SMTP server transient failure | `tools/email.py` retry decorator | 2 attempts with 2s/4s backoff |
| Supabase unreachable for subscriber fetch | `tools/subscribers.py` returns None | Falls back to `RECIPIENT_EMAILS` env var |
| Memory Stores write failure | `emit_event` returns `{ok: false}` | Tool-level retry; if persistent, agent receives error and decides |
| Slow target server during link verification | `verify_links` HEAD timeout | Falls back to GET; if both fail, URL marked invalid |
| Vercel deploy fails after commit | Vercel dashboard shows red | Manual rollback; orchestrator already sent emails — only `/latest` page is stale |
| `latest_issue.html` git push conflict | Workflow exits nonzero | Re-run workflow; idempotent commit |

The system is designed to **degrade gracefully, fail loudly, and require zero manual intervention for transient failures**.

---

## 7. Security & Privacy Posture

This is a small, public-facing system. We did not over-engineer security, but we did reason about it.

**Threat model (what we worry about):**
- **Subscriber email leak.** All emails are stored in Supabase (free tier, RLS not configured). The `/admin` page exposes them without auth. This is acceptable for the current population (friends/family + first ~50 subscribers); it's not acceptable at scale.
- **SMTP credential exposure.** `SMTP_PASSWORD` is a Gmail App Password — limited blast radius (only sends mail from that account; cannot read inbox). Stored in GitHub Secrets and Vercel Env Vars.
- **Anthropic key exposure.** Same: GitHub Secrets + Vercel Env Vars. Rotation is manual.
- **Prompt injection from research content.** The Research agents fetch web content; this content is fed into downstream agent context. We do not currently strip injection patterns. The Evaluator and Critic provide some defense-in-depth (an injected prompt asking us to email private data would have to survive 4+ agent stages).

**What we do not protect against:**
- A malicious GitHub collaborator with write access (could change the workflow file).
- A compromised Anthropic API key (would allow arbitrary token spend).
- A compromised Vercel deployment (could exfiltrate Supabase data).

**Improvements we'd make at scale:**
- Move `/admin` behind auth (Vercel password protection or Supabase Auth).
- Add Supabase RLS policies so the anon key can only insert/delete on `subscribers`, not select bulk.
- Rotate SMTP and Anthropic keys quarterly.
- Add prompt-injection sanitization on research content.

---

## 8. Performance & Cost

**Per-run timing (observed, p50):**
- Memory: 12s
- Research (parallel): 145s (worst of the two)
- Evaluator: 95s
- Writer (single attempt): 110s
- Critic (single review): 75s
- Verifier: 30s (HTTP HEAD requests + arXiv API)
- Delivery: 35s
- Orchestrator overhead: ~10s
- **Total: ~8.5 minutes** (no Critic or Verifier retries)
- **With one Critic retry: ~12 minutes**
- **With one Verifier retry (Writer fixes URLs, Critic re-approves, Verifier re-checks): ~14 minutes**

**Per-run cost (observed range, USD):**
- Lowest observed: $0.77 (heavy cache hits, single Critic pass)
- Average: $2.08
- Highest observed: $3.91 (two Critic retries → 3 Writer + 3 Critic passes)
- Cache hit rate: ~83% on Opus inputs (Anthropic's prompt caching does real work here)

**Cost dominators:**
- Writer Opus calls (~30%)
- Research Opus calls (~25% combined)
- Evaluator Opus (~20%)
- Critic Opus (~15%)
- Haiku agents combined: Memory + Verifier + Delivery (~10%)
- Verifier specifically: ~$0.02/run — almost entirely HTTP, no LLM tokens beyond reading the draft

**Operational cost (monthly, 4 runs):**
- Anthropic API: $8–$32
- GitHub Actions: $0 (Hobby tier, well under free quota)
- Vercel: $0 (Hobby tier)
- Supabase: $0 (free tier)
- Gmail SMTP: $0
- **Total: $8–$32/month**

**Total project cost (all-in, including prototyping and development):**
- Across the entire project lifecycle (initial prototyping, prompt iterations, debugging, multiple production runs): **$49.04** in Anthropic API credits.
- This figure includes all the runs that exposed the silent-failure bug, the renderer-contract bug, and the prompt iterations needed to stabilize all eight agents.

---

## 9. Decisions Log

Brief notes on choices that warrant justification.

| Decision | Alternatives considered | Why |
|----------|------------------------|-----|
| Memory Stores JSONL for session log | Supabase table; SQLite | JSONL is the native primitive of the platform; no schema; trivially inspectable |
| GitHub Actions for orchestration | Vercel Cron + Functions; AWS Lambda; Modal | Vercel Hobby's 60s cap is fatal; GH Actions is free with 6h headroom |
| Vercel for web only | Render; Fly.io | Already using for Flask; auto-deploy on git push is the perfect channel for `latest_issue.html` |
| Per-tool modules under `tools/` | One `tool_handlers.py` file | Independent failure boundaries; per-tool retry policies |
| State-driven Writer/Critic loop | Counter-driven | Counts of `draft_written` vs `critic_rejection` give correct resume behavior automatically |
| Auto-insert placeholder on silent agent failure | Hard-fail; Manual restart | Optional agents shouldn't kill the run; failure stays visible in the log |
| Verifier as a separate agent (not a Critic tool) | Add `verify_links` to Critic's toolset | Different criticality, timeout, and model tier; keeps Critic's prompt focused on quality, not HTTP semantics |
| Citation enforcement at prompt + Critic check | Just hope the Writer cites things | Two-layer defense: Writer must include links by prompt; Critic rejects drafts that don't. Pairs with Verifier for full trust hardening. |
| Verifier on Haiku, not Opus | All-Opus | Verification is mechanical (read draft, call HTTP); no judgment needed. Cuts cost to ~$0.02/run |
| Bounded verification retries (`MAX_VERIFICATION_RETRIES = 2`) | Unlimited retries | If URLs keep failing, the source data is broken; better to skip a week than ship a verified-fabricated issue |
| Writer output format constrained to `### N. Title` H3s | Free-form markdown | Email renderer parses with regex; structural contract is enforced at the prompt and again by the Critic to prevent silent rendering failures |
| Smoke test the renderer on prompt changes | Manually run the pipeline after each prompt edit | Costs ~$2 per pipeline run; smoke test costs zero and catches the same class of bug |
| Opus for cognitive agents, Haiku for mechanical | All-Opus; All-Haiku | Tiering cuts ~40% of cost with no observable quality loss on Haiku-assigned tasks |
| Supabase as live subscriber source | Static `RECIPIENT_EMAILS` env | Avoids weekly manual env-var updates after subscribe/unsubscribe |
| Commit `latest_issue.html` to repo | Object storage (S3); database blob | Free; auditable; rolls back via git revert |
| Single shared `session_events` style log across all runs | Per-run isolated logs | Memory Agent needs cross-run history (covered topics); easier than a separate cross-run store |
| 50-subscriber cap | No cap | Sanity for a Hobby-tier system; trivially raised |
| No structured alerting (Slack/PagerDuty) | Slack webhook on failure | GitHub email-on-failure is sufficient for weekly cadence |

---

## 10. When This Architecture Is Right (and When It's Not)

This system is intentionally over-engineered for one subscriber. The point is to exercise the patterns, not to optimize the immediate workload.

### Use this pattern when you have:

1. **Multiple specialized agents.** Not "one big prompt" — actual cognitive specialization (research vs. writing vs. critique).
2. **Long-horizon execution.** Multi-minute pipelines where partial failures are common.
3. **Quality gates between agents.** A Critic that can reject the Writer's output is the canonical example.
4. **Cross-run memory.** The Memory Agent reading prior coverage is what differentiates "newsletter" from "summary."
5. **Operational quietness as a goal.** Subscribe → live; orchestrator → autonomous.

### Do not adopt this pattern for:

1. **Single-shot tasks.** A summarizer needs one agent, not a hosted runtime.
2. **Workflows you fully control.** Deterministic logic (data pipelines, ETL) doesn't need an agent runtime.
3. **Low-stakes outputs.** No Critic needed if a misfire costs nothing.
4. **High-frequency tasks.** Sub-minute pipelines don't benefit from session/event-sourcing overhead — just call the API directly.

### What would change at 1000 subscribers / multiple newsletters:

- Move `/admin` behind auth.
- Add Supabase RLS.
- Add per-newsletter sharding of the session log.
- Replace the single GH Actions runner with a queue (QStash, SQS) so multiple newsletters can run concurrently.
- Add Datadog or similar for cross-run metric trending.
- Move SMTP from Gmail to a transactional provider (Resend, Postmark).
- Add A/B testing of Writer prompts.

None of these are needed today. They are obvious at the scale that triggers them.

---

## 11. Open Questions / Future Work

Concrete next moves we have considered but not yet built:

1. **Human-in-the-loop approval gate** before Delivery. A Slack message with the rendered draft + "Ship it" button. Adds latency but eliminates Critic-misjudged sends.
2. **Per-recipient personalization in the Writer.** The Writer currently produces one brief; we could pass each subscriber's preferences in via the session and customize.
3. **Long-term memory beyond covered topics.** A RAG layer over the past 12 issues' content for the Memory Agent to reason over richly, not just topic strings.
4. **A/B testing Writer prompts.** Two Writer agents in parallel; second Critic ranks them; ship the winner. ~2× cost; would tell us empirically whether prompt iterations help.
5. **Failure alerting beyond GitHub email.** Slack webhook for structured failure summaries.
6. **Public run history.** A `/runs` endpoint reading from `runs/*.json`, showing token cost and timing trends.

---

## 12. Appendices

### 12.1 File reference

| Path | Purpose |
|------|---------|
| `orchestrator_v2.py` | Step-based orchestrator + AgentRunner with retry/timeout/observability + Verifier loop |
| `observability.py` | StructuredLogger, RunTracker, run-history readers |
| `retry.py` | Exponential backoff decorator + Anthropic transient-error classifier |
| `credentials.py` | Credential resolution chain (env → vault placeholder) |
| `tools/__init__.py` | Tool exports (many-hands pattern) |
| `tools/memory_store.py` | `emit_event`, `get_events` (session log) |
| `tools/email.py` | `send_email_smtp` (SMTP + per-recipient render + snapshot) |
| `tools/subscribers.py` | `get_subscribers` (Supabase live fetch + env fallback) |
| `tools/verifier.py` | `verify_links` (HTTP HEAD + arXiv API for hallucination grounding) |
| `email_renderer.py` | Editorial HTML rendering (per-recipient unsubscribe + Gmail-safe TOC) |
| `app.py` | Flask web app: subscribe / unsubscribe / latest / admin |
| `index.py` | Vercel Python runtime entrypoint (`from app import app`) |
| `vercel.json` | Vercel config (web layer only) |
| `.github/workflows/newsletter.yml` | GitHub Actions cron + manual trigger |
| `templates/index.html` | Subscribe page template |
| `requirements.txt` | Python dependencies |
| `briefs/` | Per-run JSON logs (gitignored) |
| `logs/` | Per-session structured JSON log files (gitignored) |
| `runs/` | Per-run summary metrics (gitignored) |
| `memory_local/` | Local Memory Stores fallback for testing (gitignored) |
| `latest_issue.{html,md,json}` | Latest newsletter (committed by GH Actions; served by `/latest`) |
| `architecture_diagram.svg` | One-page system diagram |

### 12.2 Required environment variables

GitHub Secrets (orchestrator):
```
ANTHROPIC_API_KEY      # Anthropic platform key
SMTP_USER              # Gmail address
SMTP_PASSWORD          # Gmail App Password (not regular password)
APP_BASE_URL           # https://your-domain.vercel.app
SUPABASE_URL           # Supabase project URL
SUPABASE_ANON_KEY      # Supabase anon key
RECIPIENT_EMAILS       # Fallback list (used only if Supabase unavailable)
SMTP_HOST              # Defaults to smtp.gmail.com
SMTP_PORT              # Defaults to 587
SMTP_FROM              # Defaults to SMTP_USER
```

Vercel Environment Variables (web layer):
```
SUPABASE_URL, SUPABASE_ANON_KEY     # for subscribe/unsubscribe/admin
APP_BASE_URL                          # for absolute link generation
```

### 12.3 Inspecting a run

```bash
# Recent runs (downloaded from GH Actions artifacts):
ls runs/ | sort | tail -5

# Full event stream of a session:
cat memory_local/session_<id>.jsonl | jq '.event_type'

# Run summary:
jq '.' runs/<session_id>.json

# Log lines for a specific agent:
grep '"agent":"writer"' logs/session_<id>.log | jq '.'
```

### 12.4 Operational rituals

| Ritual | Frequency | What |
|--------|-----------|------|
| Inbox check | Weekly (Thursday morning) | Confirm email arrived |
| `/latest` check | Weekly | Confirm new issue served |
| GH Actions run review | Weekly | Skim logs for warnings, auto-inserted placeholders, and `verification_failed` events |
| Verifier review | Weekly | Check session log for any `verification_failed` events; spot-check the URLs that triggered them |
| Renderer smoke test | Before any Writer prompt change | Push a sample brief through `email_renderer.py`; assert the output HTML contains the expected number of items. Costs zero, catches the contract-drift class of bugs |
| Cost review | Monthly | Anthropic dashboard; investigate if > $10/run |
| Subscriber audit | Monthly | `/admin` page; remove obvious dupes/typos |
| Anthropic key rotation | Quarterly | Generate new key, update Vercel + GH secrets |
| SMTP password rotation | Quarterly | Generate new App Password, update secrets |

---

*End of architecture document.*
