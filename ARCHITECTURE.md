# AI Weekly: Multi-Agent Newsletter on Managed Agents

## Project Goal

Apply Anthropic's Managed Agents to build a production-grade multi-agent newsletter system, learning agentic engineering patterns and forming opinions on when Managed Agents matter.

Aligns with Anthropic's "Scaling Managed Agents: Decoupling the brain from the hands" (Apr 2026):
- Brain (agents) decoupled from Hands (tools) decoupled from Session (event log)
- Each can fail/be replaced independently
- Step-based execution (each step in its own Vercel function — fixes timeout)
- Many hands pattern: each tool is its own module
- Full observability: structured JSON logs + per-agent metrics + run history endpoint
- Retry with exponential backoff on transient API errors

## TL;DR

- **Backend pipeline:** 7 Managed Agents (brains) share one event log via Anthropic Memory Stores (`/mnt/memory/session_{id}.jsonl`). Each agent emits its work as an event; downstream agents read prior events via custom tools (`emit_event`, `get_events`). Resume-from-failure via `--session-id`.
- **Decoupled tools (hands):** Each tool lives in its own module (`tools/memory_store.py`, `tools/email.py`). Independent failure boundaries. Per-tool retry with exponential backoff.
- **Step-based execution:** Pipeline split into 5 steps (memory, research, evaluate, write_critique, deliver). Each runs in its own Vercel function call to avoid timeouts. Steps chain via async HTTP triggers.
- **Observability:** Structured JSON logs (`observability.py`), per-agent timing & token metrics (`RunTracker`), `/api/status` endpoint for run inspection.
- **Public web layer:** Flask app (`app.py`, on Vercel) handles subscribe / unsubscribe / latest-issue / admin against a separate Supabase `subscribers` table.

---

## System at a glance

```
                  Trigger: GitHub Actions Cron (Thursday 9am UTC)
                                │
                                v
                  ┌────────────────────────────────────────┐
                  │  .github/workflows/newsletter.yml      │
                  │  - Checks out repo                     │
                  │  - Runs: python3 orchestrator_v2.py    │
                  │  - 30 min timeout, 6h max              │
                  │  - Uploads logs as artifacts           │
                  │  - Commits latest_issue.* back to repo │
                  └──────────┬─────────────────────────────┘
                             │ runs locally on Ubuntu runner
                             v
        ┌────────────────────────────────────────────────┐
        │  OrchestratorV2.orchestrate()                  │
        │  ──────────────────────────────────────────    │
        │  Steps run sequentially:                       │
        │  1. Memory      → covered_topics               │
        │  2. Research    → launches+papers (parallel)   │
        │  3. Evaluate    → items_evaluated              │
        │  4. Write+Critic → draft_approved (max 2 retry)│
        │  5. Deliver     → email_sent                   │
        │                                                 │
        │  Each step has retry + timeout + observability │
        │  (See sections 5-7 below for details)          │
        └──────────┬─────────────────────────────────────┘
                   │ all read/write
                   v
       ┌─────────────────────────────────────────┐
       │  Memory Stores (/mnt/memory/)           │  ← durable session state
       │  session_{id}.jsonl  (append-only log)  │     (event sourcing)
       │  covered_topics.md   (cross-session)    │
       └────────────────┬────────────────────────┘
                        ▲
                        │ via tools (independent hands)
                        │
        ┌──────────────┴──────────────┐
        │                             │
   ┌────┴────┐                  ┌─────┴─────┐
   │ tools/  │                  │  tools/   │
   │ memory_ │                  │  email.py │
   │ store.py│                  │           │
   └─────────┘                  └───────────┘
   (emit_event,                 (send_email_smtp)
    get_events)

   Each tool is decoupled, independently retryable, can be replaced.

  ┌──────┬──────┬──────┬────────┬────────┬────────┬────────┐
  │ 1    │ 2    │ 3    │ 4      │ 5      │ 6      │ 7
Memory  Launches  Papers  Evaluator  Writer  Critic  Delivery
           (parallel)                       ↑     │
                                            │     │ rejected
                                            └─────┘ (max 2 retries)

           ───────────  Observability  ───────────

   StructuredLogger → JSON log lines (Vercel captures)
   RunTracker       → per-agent timing + token metrics
   /api/status      → live state inspection
   runs/*.json      → completed run summaries

           ───────────  Public web layer  ───────────

   Subscribe form  ──> POST /api/subscribe  ──> Supabase · subscribers (50 cap)
   Email footer    ──> GET  /unsubscribe?email=…
                       GET  /latest                 ──> serves latest_issue.html
                       GET  /admin                  ──> subscriber list
```

---

## Components

### 1 · Backend pipeline (7 Managed Agents)

| # | Agent | Model* | Role | Emits |
|---|-------|--------|------|-------|
| 1 | Memory            | Haiku 4.5 | Read past briefs from `session_events` (across runs) so we don't repeat coverage | `covered_topics` |
| 2 | Research Launches | Opus 4.7  | Find AI launches in the past 7 days, filtered against `covered_topics` | `launches_researched` |
| 3 | Research Papers   | Opus 4.7  | Find AI research findings in the past 7 days, filtered against `covered_topics` | `papers_researched` |
| 4 | Evaluator         | Haiku 4.5 | Score and rank merged candidate set | `items_evaluated` |
| 5 | Writer            | Opus 4.7  | Draft the brief in markdown | `draft_written` |
| 6 | Critic            | Opus 4.7  | Quality + style + banned-word check via Python tool | `draft_approved` or `critic_rejection` |
| 7 | Delivery          | Haiku 4.5 | Call `send_email_smtp` with subject + markdown | `email_sent` |

\* Model assignments live in each agent's Claude Console config, not in the local code. Verify in **Console → Managed Agents → Agents** if you need to confirm.

Agents 2 and 3 run **concurrently** via a `ThreadPoolExecutor(max_workers=2)`. Agents 5 and 6 are in a **feedback loop**: rejection triggers a fresh write, up to 2 retries (3 total writer attempts).

### 2 · Custom tools (Many Hands Pattern)

Each tool lives in its own module — independent failure boundary, per-tool retry, can be replaced without touching others.

```
tools/
├── __init__.py          (exports all hands)
├── memory_store.py      ← Hand 1: emit_event, get_events
└── email.py             ← Hand 2: send_email_smtp
```

```python
# tools/memory_store.py
emit_event(session_id, event_type, data)            # JSONL append, returns event_id
get_events(session_id, agent_name?, event_type?, limit?)  # JSONL read+filter

# tools/email.py
send_email_smtp(subject, body_markdown, recipients?) # SMTP + per-recipient render + snapshot
```

**Every tool has retry+backoff (`retry.py`):**
- 3 attempts on transient errors (rate limits, 5xx, network)
- Exponential backoff (1s → 2s → 4s, capped at 30s)
- Decorated via `@retry_with_backoff(...)`

**How agents use these tools:**
- **emit_event**: Append event to `/mnt/memory/session_{session_id}.jsonl` (via `tools/memory_store.py`)
- **get_events**: Read & filter events from JSONL (via `tools/memory_store.py`)
- **send_email_smtp**: Send via SMTP, save snapshot, render per-recipient (via `tools/email.py`)

**send_email_smtp side effects:**
1. **Per-recipient rendering.** Each email is rendered separately so the footer can include `Unsubscribe`, with a personalised link `https://<APP_BASE_URL>/unsubscribe?email=<that recipient>`. TOC and back-to-top anchors are also rewritten to absolute `…/latest#item-N` URLs (Gmail strips in-document `id`s, breaking plain `#anchor` links).
2. **Snapshot save.** Before sending, a non-personalised copy is rendered and written to `latest_issue.html` / `latest_issue.md` / `latest_issue_meta.json` so the public site's `/latest` endpoint can serve the most recent issue.
3. **Credentials isolation.** SMTP credentials (username, password) are retrieved from Anthropic's credential vault, never exposed to the agent.

### 3 · Shared session log (Anthropic Memory Stores · `/mnt/memory/`)

Events are stored as append-only JSONL files in Anthropic's workspace-scoped Memory Stores:

```
/mnt/memory/
├── session_{session_id}.jsonl        (per-run event log: agents append events)
├── covered_topics.md                 (cross-session: topics covered in last 12 runs)
└── latest_issue_meta.json            (snapshot: last newsletter subject, sent_at, recipients)
```

**Event schema** (each line is a JSON object):
```json
{
  "id": "uuid",
  "session_id": "newsletter_YYYYMMDD_HHMMSS_8charhex",
  "agent_name": "memory|research_launches|research_papers|evaluator|writer|critic|delivery",
  "event_type": "covered_topics|launches_researched|papers_researched|items_evaluated|draft_written|critic_rejection|draft_approved|email_sent",
  "data": { "...agent-specific payload..." },
  "created_at": "2026-05-04T16:06:49.123456Z"
}
```

**Read the full run:**
```bash
cat /mnt/memory/session_newsletter_20260504_160649_80879d6e.jsonl | jq '.event_type'
```

**Cross-session memory:** The Memory Agent reads `/mnt/memory/covered_topics.md` to see what topics were covered in previous runs. No external database needed; persistence is workspace-scoped within Managed Agents.

**Why Memory Stores over Supabase?**
1. **Decoupling**: Session state lives in the platform, not external infrastructure.
2. **Simplicity**: No schema management, no queries—just append-only JSONL files.
3. **Workspace isolation**: Scoped to the workspace, secure by default.
4. **Audit trail**: Each event is immutable; version history built-in.

This aligns with Anthropic's "Scaling Managed Agents: Decoupling the brain from the hands" (Apr 2026).

### 4 · Public web layer (`app.py`, Vercel)

Flask app deployed via the `index.py` serverless entrypoint (`from app import app`). Hosted at `https://ai-weekly-ecru.vercel.app`.

| Method | Route                           | Purpose |
|--------|---------------------------------|---------|
| GET    | `/`                             | Subscribe form (`templates/index.html`) |
| POST   | `/api/subscribe`                | Validates email, enforces **50-subscriber cap**, inserts into `subscribers` |
| GET    | `/unsubscribe?email=<addr>`     | Deletes the row from `subscribers`, shows a styled confirmation page |
| GET    | `/latest`                       | Serves `latest_issue.html` (with subject + sent-at as `X-Issue-*` headers); 404 page if no issue saved yet |
| GET    | `/admin`                        | Plain page listing current subscribers (read-only) |

A second Supabase table backs this layer:

```sql
CREATE TABLE subscribers (
  id     BIGSERIAL PRIMARY KEY,
  email  TEXT      NOT NULL UNIQUE
);
```

The orchestrator only consumes `subscribers` indirectly: the deploy operator copies the `/admin` list into the `RECIPIENT_EMAILS` env var (or the orchestrator could be wired to read the table directly — currently env-driven for simplicity).

### 5 · Execution Models

The orchestrator supports two execution modes:

#### A. Monolithic (GitHub Actions, local runs)
```
python3 orchestrator_v2.py
```
- Runs all 5 steps sequentially in one process
- 30-min timeout (GitHub Actions), no timeout locally
- Used by `.github/workflows/newsletter.yml` (production trigger)

#### B. Step-Based (for future use, manual debug)
```
python3 orchestrator_v2.py --session-id <id> --step evaluate
```
- Runs ONE step per invocation
- Designed for environments with short timeouts (e.g., Vercel Pro 5min, AWS Lambda)
- Can be chained via HTTP (`/api/step?session_id=X&step=Y`)
- Useful for debugging individual steps

**Step config:**
```
STEP_ORDER = ["memory", "research", "evaluate", "write_critique", "deliver"]

STEP_TERMINAL_EVENTS = {
  "memory":         ["covered_topics"],
  "research":       ["launches_researched", "papers_researched"],
  "evaluate":       ["items_evaluated"],
  "write_critique": ["draft_approved"],
  "deliver":        ["email_sent"],
}
```

**Why GitHub Actions over Vercel Cron?**
- Vercel Hobby: 60s function timeout — won't fit our 5-15min pipeline
- GitHub Actions: 6-hour timeout, free, simple YAML config
- Vercel still hosts the web layer (subscribe form, /latest, /admin)

**Endpoints (Vercel - monitoring only):**
| Endpoint | Purpose |
|----------|---------|
| `GET /api/status?session_id=X` | Live state of a specific run |
| `GET /api/status?recent=true` | Last 20 runs with summaries |
| `GET /latest` | Most recent newsletter (HTML) |
| `GET /admin` | Subscriber list |

---

### 6 · Observability

**Module:** `observability.py`

#### Structured Logging
Every event logged as a JSON line with session correlation:

```json
{
  "ts": "2026-05-05T09:00:01Z",
  "level": "INFO",
  "session_id": "newsletter_20260505_090000_abc123",
  "agent": "research_launches",
  "message": "agent_start: research_launches",
  "criticality": "critical",
  "timeout_sec": 300
}
```

- Logs go to **stdout** (Vercel captures) AND **`logs/session_{id}.log`** (persistent)
- Levels: INFO, WARN, ERROR, METRIC
- Always includes session_id + agent for filtering

#### Per-Run Metrics (`RunTracker`)
Tracks per-agent:
- Elapsed time (sec)
- Tokens used (input, output, cache_read)
- Status (success / failed / skipped)
- Errors (with traceback)

Persisted to `runs/{session_id}.json` after each run:
```json
{
  "session_id": "newsletter_20260505_090000_abc123",
  "total_elapsed_sec": 487.3,
  "agent_timings": { "memory": 12.1, "research_launches": 145.2, ... },
  "agent_tokens": { "research_launches": { "input": 45000, "output": 8200, "cache_read": 320000 } },
  "totals": { "input_tokens": 380000, "output_tokens": 95000, "cache_read_tokens": 1200000 },
  "errors": [],
  "success": true
}
```

#### Status Endpoint
```bash
curl https://your-domain.vercel.app/api/status?session_id=newsletter_20260505_090000_abc123
```

Returns current state of any session (live or completed):
```json
{
  "session_id": "...",
  "completed": false,
  "state": {
    "memory_complete": true,
    "launches_complete": true,
    "papers_complete": false,
    "evaluation_complete": false,
    "draft_approved": false,
    "email_sent": false,
    "rejection_count": 0,
    "draft_count": 0
  },
  "events_count": 3,
  "event_types": ["covered_topics", "launches_researched", "..."]
}
```

---

### 7 · Error Handling & Retry

**Module:** `retry.py`

#### Retry Policy
| Error Type | Retry? | Backoff |
|------------|--------|---------|
| Rate limit (429) | Yes (3x) | Exponential (1s → 2s → 4s) |
| Server error (5xx) | Yes (3x) | Exponential |
| Network/timeout | Yes (3x) | Exponential |
| Auth error (401/403) | No | — |
| Validation error (400) | No | — |
| Tool execution error | Yes (2x) | Linear (2s) |

```python
@retry_with_backoff(max_attempts=3, initial_delay=1.0, exponential_base=2.0,
                    is_retryable=is_retryable_anthropic_error)
def call_api(): ...
```

#### Per-Agent Criticality
```python
AGENT_CRITICALITY = {
  "memory": "optional",          # Skip on failure (no past coverage = OK)
  "research_launches": "critical",  # Must succeed
  "research_papers": "optional",    # Can produce 0 papers
  "evaluator": "critical",
  "writer": "critical",
  "critic": "critical",
  "delivery": "critical",
}
```

#### Per-Agent Timeouts
```python
AGENT_TIMEOUTS = {
  "memory": 120,
  "research_launches": 300,
  "research_papers": 300,
  "evaluator": 180,
  "writer": 300,
  "critic": 180,
  "delivery": 120,
}
```

If an agent exceeds its timeout, it's marked failed and the orchestrator continues (or retries based on criticality).

#### Silent Failure Detection (Auto-Insert Placeholder)
If a research agent silently exits without emitting (observed: Papers agent did this in 2/3 early runs), the orchestrator auto-inserts an empty placeholder event:

```python
{
  "event_type": "papers_researched",
  "data": { "papers": [], "auto_inserted": True, "note": "Agent did not emit." }
}
```

This unblocks downstream agents and makes the failure visible in logs.

#### Writer/Critic Loop (State-Driven Retry)
Max 2 retries (3 total writer attempts). Loop driven by event counts:
- `drafts == rejections` → Writer needs to write again
- `drafts > rejections` → Critic needs to review
- `draft_approved` event → Loop exits successfully

#### Resume from Crash
```bash
# Pipeline died mid-run? Restart from last checkpoint:
curl https://your-domain.vercel.app/api/step?session_id=newsletter_...&step=evaluate
```

---

### 8 · Resume support

`orchestrator_v2.py` accepts `--session-id <id>`. On resume:

1. The orchestrator reads all events for that `session_id` from `session_events`.
2. Each step is skipped if its terminal event already exists:
   - `covered_topics` → skip Memory
   - `launches_researched` AND `papers_researched` → skip parallel research
   - `items_evaluated` → skip Evaluator
   - `draft_approved` → skip the Writer/Critic loop entirely
   - `email_sent` → already complete, exit
3. The Writer/Critic loop is **state-driven** by counts of `draft_written` vs `critic_rejection`, so a partial loop picks up correctly (e.g. one rejection + waiting-for-retry → next call writes the new draft).

```bash
# fresh run
python3 orchestrator_v2.py

# resume a specific session that died mid-way
python3 orchestrator_v2.py --session-id newsletter_20260504_160649_80879d6e
```

---

## Workflow (full happy path)

```
1.  Generate session_id  →  newsletter_<UTC-timestamp>_<8-hex>
2.  Memory Agent          reads cross-run history, emits covered_topics
3.  Research Launches  ⎫
                        ⎬  run in parallel, emit launches_researched / papers_researched
4.  Research Papers    ⎭
5.  Evaluator             reads (3 + 4), scores, emits items_evaluated
6.  Writer                reads (5), emits draft_written
7.  Critic                reads (6), emits draft_approved | critic_rejection
       └─ if rejected and retries remain → back to (6)        (max 2 retries)
8.  Delivery              calls send_email_smtp:
                          • renders per-recipient HTML (personalised unsubscribe)
                          • SMTP-sends to RECIPIENT_EMAILS
                          • side-effect: writes latest_issue.{html,md,json}
                          • emits email_sent
9.  Orchestrator          writes briefs/<session_id>_log.json
```

### Public site (independent of pipeline runs)

```
Visitor → /                       subscribe form
       → POST /api/subscribe      validate · check cap · insert into subscribers
       → /unsubscribe?email=…     delete row · confirmation page
       → /latest                  serve latest_issue.html written by step 8 above
Operator → /admin                 see subscriber list
```

---

## Costs (per weekly run, estimated)

| Agent | Model | Est. Cost |
|-------|-------|-----------|
| Memory             | Haiku | $0.20 |
| Research Launches  | Opus  | $2.00 |
| Research Papers    | Opus  | $2.00 |
| Evaluator          | Haiku | $0.30 |
| Writer             | Opus  | $2.50 |
| Critic             | Opus  | $1.50 |
| Delivery           | Haiku | $0.20 |
| **Total**          |       | **~$8.70/run** |

Monthly (4 runs): **~$35**.

Vercel hobby tier and Supabase free tier cover the web layer at $0 for this workload.

Cost dominators: prompt caching is doing real work — typical Critic round shows ~80% cache reads vs full input. If you turn caching off, expect roughly 4–5× these numbers.

---

## Opinions on Managed Agents

### When Managed Agents shines

1. **Multi-agent systems with specialization.** Each agent has one job. Reusable, debuggable, swappable.
2. **Long-horizon tasks needing durable state.** Session log persists across failures. *Resume from last successful event* is implemented here via `--session-id` (see §5 above).
3. **Parallel cognitive work.** Two research agents concurrently is faster and cheaper than sequential.
4. **Critic / feedback loops.** Quality gates between agents catch errors before they propagate.
5. **Hot-swappable components.** Want a better evaluator? Update Agent 4 in the Console; nothing else changes.

### When Managed Agents is overkill

1. **Single-shot tasks.** A summarizer needs one agent, not a hosted runtime.
2. **Workflows you fully control.** Deterministic logic doesn't need an agent runtime.
3. **Low-stakes outputs.** No need for critic loops or memory if output quality doesn't matter.

### The session-as-state insight

The most powerful concept is treating the session as **durable shared state**, not just a conversation log. This is similar to event sourcing in distributed systems — replay events to reconstruct state, never overwrite.

I used Supabase as the shared event store because the SDK doesn't yet expose multi-agent sessions natively. The pure vision: ONE Managed Agents session that all agents `getEvents`/`emitEvent` against.

### What's still missing

1. **Human-in-the-loop approval gate** before Delivery.
2. **Cron / scheduled trigger** — the only trigger today is manual `python3 orchestrator_v2.py`. A Vercel Cron or GitHub Actions workflow would close this.
3. **Observability beyond Claude Console** — token cost per agent over time, alerting, dashboards.
4. **A/B testing** — run two Writer versions, compare quality.
5. **Long-term memory across runs** — Memory Agent reads past `session_events`, but a RAG/embeddings layer over past briefs would be richer than topic strings.
6. **Reading subscribers directly** — currently the orchestrator pulls `RECIPIENT_EMAILS` from env. It could query the `subscribers` table directly so subscribe/unsubscribe takes effect on the next run without an env edit.

---

## Files

| File | Purpose |
|------|---------|
| `orchestrator_v2.py`           | Multi-agent orchestrator (step-based) + AgentRunner with retry/timeout/observability |
| `observability.py`             | Structured JSON logger, RunTracker (per-agent metrics), run history |
| `retry.py`                     | Exponential backoff retry decorator + Anthropic error classifier |
| `credentials.py`               | Credential manager (env vars; Anthropic vault placeholder) |
| `tools/__init__.py`            | Tool exports (many hands pattern) |
| `tools/memory_store.py`        | Hand 1: emit_event, get_events (Memory Stores JSONL) |
| `tools/email.py`               | Hand 2: send_email_smtp (SMTP + per-recipient render + snapshot) |
| `api/orchestrate.py`           | Vercel function: cron entry, triggers step 1 |
| `api/step.py`                  | Vercel function: runs one step, chains next |
| `api/status.py`                | Vercel function: live state + recent runs |
| `email_renderer.py`            | Editorial HTML rendering (per-recipient unsubscribe + Gmail-safe TOC) |
| `app.py`                       | Flask app: `/`, `/api/subscribe` (50-cap), `/unsubscribe`, `/latest`, `/admin` |
| `index.py`                     | Vercel serverless entrypoint for Flask |
| `vercel.json`                  | Cron config (Thursday 9am UTC), function maxDuration, env vars |
| `templates/index.html`         | Subscribe page template |
| `requirements.txt`             | Python deps |
| `.env`, `.env.example`         | Config (SMTP, ANTHROPIC_API_KEY, APP_BASE_URL, RECIPIENT_EMAILS) |
| `briefs/`                      | Per-run JSON logs (gitignored) |
| `logs/`                        | Per-session structured JSON log files (gitignored) |
| `runs/`                        | Per-run summary metrics for /api/status?recent=true (gitignored) |
| `memory_local/`                | Local Memory Stores fallback for testing (gitignored) |
| `latest_issue.{html,md,json}`  | Snapshot of the most recent send, served by `/latest` (gitignored) |
| `architecture_diagram.svg`     | System as a one-page diagram |

### Required env vars

```
ANTHROPIC_API_KEY=...
SUPABASE_URL=...                 # or NEXT_PUBLIC_SUPABASE_URL
SUPABASE_ANON_KEY=...            # or ANON_KEY
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...                # Gmail app password
SMTP_FROM=...                    # defaults to SMTP_USER
RECIPIENT_EMAILS=a@x.com,b@y.com
APP_BASE_URL=https://ai-weekly-ecru.vercel.app
```

---

## How to run

```bash
cd ~/Desktop/agent_app
python3 orchestrator_v2.py                                  # fresh run
python3 orchestrator_v2.py --session-id newsletter_…        # resume
```

Inspect a run:

```sql
SELECT agent_name, event_type, created_at
FROM session_events
WHERE session_id = 'newsletter_…'
ORDER BY created_at;
```

Run the web app locally:

```bash
python3 app.py                  # http://127.0.0.1:5000
```

---

## Conclusion

The hardest part wasn't building agents. It was understanding when each architectural pattern matters. Managed Agents is a tool for systems that need **specialization + coordination + durable state**. If you don't need all three, you don't need Managed Agents.

For the AI Weekly newsletter specifically, multi-agent is overkill at 1 subscriber. But as a learning exercise it's the right project — enough complexity to exercise every Managed Agents pattern (parallel branches, feedback loops, shared state, durable resume, custom tools) without overwhelming the architecture.
