# AI Weekly: Multi-Agent Newsletter on Managed Agents

## Project Goal

Apply Anthropic's Managed Agents to build a production-grade multi-agent newsletter system, learning agentic engineering patterns and forming opinions on when Managed Agents matter.

## TL;DR

- **Backend pipeline:** 7 Managed Agents share one Supabase event log (`session_events`). Each agent emits its work as an event; downstream agents read prior events via custom tools (`emit_event`, `get_events`). Resume-from-failure is supported by passing `--session-id`.
- **Public web layer:** A small Flask app (`app.py`, deployed on Vercel) handles subscribe / unsubscribe / latest-issue / admin against a separate Supabase `subscribers` table. Per-recipient unsubscribe links and a `/latest` snapshot endpoint close the loop between the pipeline and the public site.
- **Key learning:** Managed Agents shines when you have multiple specialized brains coordinating through durable shared state. Single-agent systems don't need it.

---

## System at a glance

```
                       Trigger (manual: python3 orchestrator_v2.py)
                                       │
                                       v
        ┌──────────────────────────────────────────────────────────┐
        │  OrchestratorV2  (host process, stateless)               │
        │  • generates / resumes a shared_session_id               │
        │  • routes 3 custom tools to handlers                     │
        │  • writes briefs/<session_id>_log.json + latest_issue.*  │
        └────────────────┬─────────────────────────────────────────┘
                         │ all 7 agents read/write here
                         v
       ┌────────────────────────────────────┐
       │  Supabase · session_events  (shared event bus)             │
       │  { id, session_id, agent_name, event_type, data, ts }       │
       └────────────────────────────────────┘
                         ▲
                         │ via emit_event / get_events custom tools
   ┌──────┬──────┬───────┴────────┬────────┬────────┬────────┐
   │ 1    │ 2    │ 3              │ 4      │ 5      │ 6      │ 7
Memory  Launches  Papers        Evaluator  Writer  Critic  Delivery
            (parallel)                              ↑   │
                                                    │   │ rejected
                                                    └───┘ (max 2 retries)
                                                            │
                                                            v
                                              send_email_smtp custom tool
                                              ├─> SMTP → recipient inboxes
                                              └─> _save_latest_issue() →
                                                  latest_issue.{html,md,json}

                ───────────  Public web layer (Vercel)  ───────────

   Subscribe form  ──> POST /api/subscribe  ──> Supabase · subscribers (50 cap)
   Email footer    ──> GET  /unsubscribe?email=…  ──> delete from subscribers
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

### 2 · Custom tools (handled by the orchestrator)

```python
emit_event(session_id, event_type, data)            # write event row
get_events(session_id, agent_name?, event_type?)    # read prior events
send_email_smtp(subject, body_markdown, recipients?) # SMTP delivery + side effect
```

`send_email_smtp` has two side effects beyond the actual SMTP send:

1. **Per-recipient rendering.** Each email is rendered separately so the footer can include `Unsubscribe`, with a personalised link `https://<APP_BASE_URL>/unsubscribe?email=<that recipient>`. TOC and back-to-top anchors are also rewritten to absolute `…/latest#item-N` URLs (Gmail strips in-document `id`s, breaking plain `#anchor` links).
2. **Snapshot save.** Before sending, a non-personalised copy is rendered and written to `latest_issue.html` / `latest_issue.md` / `latest_issue_meta.json` so the public site's `/latest` endpoint can serve the most recent issue.

### 3 · Shared session log (Supabase · `session_events`)

```sql
CREATE TABLE session_events (
  id          BIGSERIAL    PRIMARY KEY,
  session_id  TEXT         NOT NULL,
  agent_name  TEXT         NOT NULL,
  event_type  TEXT         NOT NULL,
  data        JSONB        NOT NULL,
  created_at  TIMESTAMPTZ  DEFAULT NOW()
);
```

Every agent's output is one row. Read the full run with one query:

```sql
SELECT agent_name, event_type, created_at
FROM session_events
WHERE session_id = 'newsletter_20260504_160649_80879d6e'
ORDER BY created_at;
```

The same table is also how the **next week's** Memory Agent reads previous coverage — events are not session-scoped from the database's point of view.

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

### 5 · Resume support

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
| `orchestrator_v2.py`           | Multi-agent orchestrator + custom tool handlers + resume |
| `email_renderer.py`            | Editorial HTML email rendering (per-recipient unsubscribe + Gmail-safe TOC links) |
| `app.py`                       | Flask app: `/`, `/api/subscribe` (50-cap), `/unsubscribe`, `/latest`, `/admin` |
| `index.py`                     | Vercel serverless entrypoint (`from app import app`) |
| `templates/index.html`         | Subscribe page template |
| `requirements.txt`             | Python deps |
| `.env`, `.env.example`         | Config (SMTP, Supabase, ANTHROPIC_API_KEY, APP_BASE_URL, RECIPIENT_EMAILS) |
| `briefs/`                      | Per-run JSON logs (gitignored) |
| `latest_issue.{html,md,json}`  | Snapshot of the most recent send, served by `/latest` (gitignored) |
| `architecture_diagram.svg`     | This system as a one-page diagram |

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
