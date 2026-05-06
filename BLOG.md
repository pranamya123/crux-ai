# Building a Production Multi-Agent Newsletter on Anthropic Managed Agents — Every Mistake I Made

*An autonomous newsletter written by 7 agents coordinating through a shared session log. Here's everything that broke along the way, and the architecture that emerged from fixing it.*

---

## What I Built

Crux AI is a newsletter that ships every Thursday at 9am UTC. I haven't touched it in weeks. Seven specialized AI agents do the work — they research the past week's AI ecosystem, score what matters, write the brief, critique it, and send it to subscribers.

A typical run costs **~$2** in Anthropic API tokens. It runs unattended on GitHub Actions. New subscribers signed up via the website are auto-included in the next send. The output appears at [`/latest`](https://ai-weekly-ecru.vercel.app/latest), shipped autonomously by the pipeline.

This post is about the path from "let me try Managed Agents" to a production system that wakes up weekly. Specifically, the **eleven things that broke** and what I learned fixing them.

---

## Why Multi-Agent at All

Honest answer: I wanted to learn the patterns, and I needed a workload that was complicated enough to exercise them. A weekly newsletter has every interesting property of a real multi-agent system:

- A multi-stage pipeline (research → evaluate → write → critique → deliver)
- Long-horizon execution (10+ minutes)
- Quality gates (a Critic agent that can reject the Writer's draft)
- Cross-run memory (don't repeat last week's stories)
- External integrations (SMTP, web research, subscriber DB)
- Real cost — every run costs money, which forces good engineering

If you don't need at least three of those, you don't need multi-agent.

---

## The Stack (Eventually)

After several wrong turns, the system landed here:

| Layer | Tool | Why |
|-------|------|-----|
| Brains | 7 Anthropic Managed Agents (Opus 4.7 + Haiku 4.5) | Specialized, hot-swappable, hosted runtime |
| Orchestrator | `orchestrator_v2.py` (single Python process) | Stateless, resumable via `--session-id` |
| Tools (hands) | Per-tool modules under `tools/` | Independent failure boundaries |
| Session log | Anthropic Memory Stores (`/mnt/memory/*.jsonl`) | Workspace-scoped, append-only event sourcing |
| Trigger | GitHub Actions cron | 6-hour timeout, free, no cold starts |
| Web layer | Flask on Vercel | Subscribe / `/latest` / `/admin` |
| Subscriber DB | Supabase | Live source of truth, queried per run |

The route to *eventually* was the interesting part.

---

## Problem 1: The Papers Agent Disappeared

In the first three production runs, the Research Papers agent emitted no `papers_researched` event in **two of them**. Not an error. Not a timeout. Just... silent.

The downstream Evaluator was waiting for the event and stalled. The Launches agent worked fine in all three runs.

**Root cause** (took an hour to find): the Papers agent had a stricter quality gate than Launches. When it found nothing meeting the bar, it just exited without emitting. No "I found zero papers" event — nothing at all.

**Fix, two layers:**

1. **Prompt-level:** Added explicit "ALWAYS call emit_event before finishing, even if the array is empty" to the system prompt.
2. **Orchestrator-level:** Added a *post-condition fallback*. After the research step, if the terminal event is missing, the orchestrator inserts a placeholder:

```python
{
  "event_type": "papers_researched",
  "data": {
    "papers": [],
    "auto_inserted": True,
    "note": "Auto-inserted: research agent did not emit a terminal event"
  }
}
```

The Evaluator runs on the placeholder. The pipeline ships an issue with launches only. The failure stays loud in the logs.

**Result:** silent-failure rate went from **33% to 0%**.

**Lesson:** Two-layer defense is essential. Fixing the prompt isn't enough — the orchestrator must handle the case where the prompt fails.

---

## Problem 2: Gmail Strips `<id>` Attributes

I added a TOC to the email with anchor links (`#item-1`). Looked great in browsers. In Gmail, the links did absolutely nothing.

After some digging: Gmail's HTML sandbox strips `id` attributes from elements for security. So `<a href="#item-1">` had no target to jump to.

**Fix:** Conditional URL rewriting in the email renderer. When rendering for an email recipient, rewrite `#item-N` to `https://app.url/latest#item-N` (an absolute URL to the public `/latest` page, where the same anchor *does* work because it's a real page load). When rendering for `/latest` itself, leave the anchors plain.

```python
if recipient_email and base_url:
    body_html = body_html.replace('href="#item-', f'href="{base_url}/latest#item-')
```

**Lesson:** Email rendering is a different beast from web rendering. Test in Gmail (not just the browser) before declaring victory.

---

## Problem 3: Vercel Cron's 60-Second Cap

My initial deployment plan was Vercel Cron triggering the orchestrator. Worked fine in dev. Then I read the docs more carefully.

| Plan | Function timeout |
|------|------------------|
| Hobby | 60 seconds |
| Pro | 5 minutes ($20/mo) |
| Enterprise | 15 minutes |

My pipeline takes **8–15 minutes**. Even Pro wouldn't reliably fit it.

I went through three abandoned alternatives:

1. **Single big Vercel function** → would timeout
2. **Step-based: 5 functions chained via async HTTP** → fragile (if the next-step trigger drops, pipeline stalls invisibly)
3. **Upgrade to Pro** → $20/mo for a hobby project

The eventual answer: **GitHub Actions**.

| Property | Vercel Hobby Cron | GitHub Actions |
|----------|-------------------|----------------|
| Timeout | 60 sec | 6 hours |
| Cost | $0 (capped) / $20/mo (Pro) | $0 (free tier) |
| Setup | Cron + function | One YAML file |

The workflow is 50 lines:

```yaml
on:
  schedule:
    - cron: '0 9 * * 4'  # Thursday 9am UTC
jobs:
  generate-newsletter:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install -r requirements.txt
      - env: { ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}, ... }
        run: python3 orchestrator_v2.py
      - run: |  # commit latest_issue.html back to repo so Vercel deploys it
          git add -f latest_issue.*
          git commit -m "newsletter: weekly update" && git push
```

That's it. Vercel still hosts the web layer (subscribe, `/latest`, `/admin`). Orchestrator runs on GitHub Actions. The two are linked by a `git push` after each successful run.

**Lesson:** Match the platform to the workload duration. Vercel Cron is for HTTP-style work, not long pipelines. GitHub Actions is the right answer for weekly cron jobs in the 1–60 minute range.

---

## Problem 4: Anthropic's "Vault" Is Not for SMTP

When I read about Managed Agents credential vaults, I assumed I could store the SMTP password there. Read the docs, opened the Console — and discovered the vault is bound to **MCP server URLs** for OAuth/Bearer tokens. Slack, Gmail OAuth, GitHub. SMTP user/password authentication doesn't fit.

**Fix:** Stored SMTP creds as plain environment variables in GitHub Secrets and Vercel. Created a `credentials.py` abstraction with a fallback chain (vault placeholder → env vars → defaults), so when the platform eventually supports static secrets, swapping in a real vault is one function change.

**Lesson:** Read the docs before assuming what a feature does. "Vault" is overloaded — Anthropic's vault is specifically about delegated user authentication for MCP services.

---

## Problem 5: The Evaluator Was a Black Box

The first version of the Evaluator agent had a four-line prompt: "Score and rank items. Pick the best." When I reviewed the output, I had no way to tell *why* anything was selected or rejected.

I rewrote the prompt as a structured rubric:

- **Relevance** (1–10) — Does this matter to engineers/PMs?
- **Technical depth** (1–10) — Does it reveal non-obvious capability?
- **Novelty** (1–10) — New this week, not in covered topics?
- Threshold: total < 18 → reject

And forced the output to include reasoning per item, plus a `rejected_items` array with the same scoring breakdown. So now I get:

```json
{
  "rank": 1,
  "name": "GPT-5 Released",
  "total_score": 29,
  "scores": { "relevance": 10, "technical_depth": 10, "novelty": 9 },
  "reasoning": "[RELEVANCE: 10/10] New flagship model. [TECHNICAL_DEPTH: 10/10] 2× benchmark improvement with architecture details. [NOVELTY: 9/10] Not covered before. [DECISION: ACCEPT at 29]",
  "decision": "ACCEPT"
}
```

Plus an `acceptance_rate` summary so I can detect drift week-over-week.

**Lesson:** If you can't tell why an agent made a decision, you can't trust the system. Force structured reasoning into the output.

---

## Problem 6: My PAT Couldn't Push the Workflow

When I tried to `git push` the new `.github/workflows/newsletter.yml`, GitHub rejected it:

> remote rejected: refusing to allow a Personal Access Token to create or update workflow `.github/workflows/newsletter.yml` without `workflow` scope

Turns out GitHub treats CI/CD config as more sensitive than code. My PAT had `repo` scope but not `workflow`.

**Fix:** Updated the PAT scopes (or create a new one with both). Then push worked.

**Lesson:** Scope your tokens for what you actually need. `repo` ≠ `workflow`.

---

## Problem 7: Vercel.json's `default` Syntax Is Invalid

I wrote this in `vercel.json`:

```json
"SMTP_HOST": { "default": "smtp.gmail.com" }
```

Vercel's response on deploy: `Invalid request: env.SMTP_HOST should be string`. The `{ "default": ... }` syntax I'd seen in some docs isn't supported by their schema.

**Fix:** Removed the entire `env` section from `vercel.json`. Vercel reads env vars from the Dashboard — declaring them in the file isn't necessary unless you're using the `@secret_name` reference syntax.

**Lesson:** Vercel reads env vars from the Dashboard regardless. Don't put them in `vercel.json` unless you have a reason.

---

## Problem 8: Vercel Python ≠ AWS Lambda

I'd written my serverless functions in the AWS Lambda style: `def handler(request)`. Vercel rejected them with:

> The pattern "api/orchestrate.py" defined in `functions` doesn't match any Serverless Functions inside the `api` directory.

Vercel's Python runtime expects either a `BaseHTTPRequestHandler` subclass or a Flask/FastAPI app. The `handler(request)` pattern is AWS-specific.

**Fix:** Since I'd already moved orchestration to GitHub Actions, I just deleted the Vercel API functions entirely. They were leftovers from the abandoned step-based architecture.

**Lesson:** Different platforms have different conventions for "what is a serverless function." Don't assume.

---

## Problem 9: The Subscriber List Was a Manual Chore

Originally, the orchestrator read recipients from a `RECIPIENT_EMAILS` env var. New subscribers would sign up at `/`, get added to Supabase, show up at `/admin` — and then I'd have to **manually copy them into a GitHub Secret** before the next Thursday run.

Five minutes a week. Plus, if I forgot, new subscribers wouldn't get the issue.

**Fix:** Created `tools/subscribers.py`:

```python
def get_subscribers() -> List[str]:
    # 1. Try Supabase (live source of truth)
    emails = _fetch_from_supabase()
    if emails:
        return _normalize(emails)
    # 2. Fall back to RECIPIENT_EMAILS env var
    return _normalize(_get_from_env())
```

And updated the email tool to use it. Now: subscribe → next run includes you. Zero manual updates.

**Lesson:** Closing operational loops eliminates entire categories of human error. Worth 30 minutes of implementation.

---

## Problem 10: No Resume = Wasted Money

Mid-pipeline, the orchestrator died once at the Critic stage. Restarting from scratch cost another $2 — paying for the same memory pull, the same research, the same evaluation, all over again.

**Fix:** Added `--session-id` resume support. The orchestrator now:

1. On startup, reads all events for the given session ID
2. Skips any step whose terminal event already exists
3. For the Writer/Critic loop specifically, drives the loop based on event counts:

```python
drafts = count_events("draft_written")
rejections = count_events("critic_rejection")

if drafts == 0 or rejections >= drafts:
    # Writer needs to write (initial or after rejection)
elif drafts > rejections:
    # Critic needs to review the latest draft
```

This is a state-driven loop, not a turn-driven one. The orchestrator can pick up exactly where it left off, even mid-Writer/Critic exchange.

**Lesson:** Event sourcing makes resume nearly free. For any pipeline > 1 minute, it's worth the design effort.

---

## Problem 11: Tools-as-One-File Was Wrong

My initial structure had all tool handlers (`emit_event`, `get_events`, `send_email_smtp`) as functions in `orchestrator_v2.py`. Worked fine. But:

- All tools shared the same retry policy. SMTP and filesystem have very different error profiles — they need different policies.
- Couldn't test a tool in isolation without importing the orchestrator.
- Failures were entangled. A bug in `send_email_smtp` could affect `emit_event` reading.

After reading Anthropic's "decoupling brain from hands" article, I refactored:

```
tools/
├── __init__.py          # exports
├── memory_store.py      # emit_event, get_events  (3 retries, 0.5s backoff)
├── email.py             # send_email_smtp          (2 retries, 2s backoff)
└── subscribers.py       # get_subscribers          (Supabase + env fallback)
```

Each tool is now its own module with its own retry policy. They share nothing. A failure in one is bounded.

**Lesson:** "Tools as cattle" — each is an independent failure boundary. Treat them that way in code, not just conceptually.

---

## The Numbers

After all the fixes, the system has been running cleanly. Here are the real metrics:

| Metric | Value | Notes |
|--------|-------|-------|
| Avg cost / run | **$2.08** | Range: $0.77 (heavy cache hits) – $3.91 (Critic retries) |
| Cache hit rate | **83%** | On Opus inputs (Anthropic's prompt caching, automatic) |
| Cost reduction from caching | **~76%** | $8.70 estimated → $2.08 actual |
| Cost reduction from Opus+Haiku tiering | **~40%** | vs all-Opus |
| Wall-clock reduction from parallel research | **~50%** | 5 min sequential → 2.5 min parallel |
| Wall-clock per run | **8 min (no retry)** to **12 min (one Critic retry)** | |
| Silent-failure rate (Papers agent) | **33% → 0%** | After two-layer fix |
| Vercel Cron $$ saved by GitHub Actions | **$240/year** | $20/mo Pro upgrade avoided |
| Engineering time to MVP | **~3 days** | vs estimated 2-3 weeks for custom harness |
| Lines of orchestration code | **~600** | vs ~1500 estimated for custom harness |
| External infrastructure dependencies | **2** | Anthropic + Supabase. (GitHub Actions, Vercel are CI/hosting, not state) |

Most of these wins were free or near-free. **Prompt caching** is the biggest single lever — 76% cost reduction with zero engineering. Just structure your prompts so the static parts come first.

---

## What I Got "For Free" From Managed Agents

The headline benefit of Anthropic Managed Agents isn't intelligence — it's that the platform handles the parts of agent infrastructure that are tedious to build:

| Capability | Without Managed Agents I'd need |
|------------|-------------------------------|
| Tool execution loop | Custom agent loop with retry, parsing, error handling |
| Session persistence | Custom state store, lock management |
| Hot-swappable prompts | Code deploys for prompt iteration |
| Prompt caching | Manual context management |
| Multi-agent parallelism | Worker pool, coordination logic |
| Type-safe tool definitions | Custom validators |

Estimated time savings to MVP: **~2 weeks**. Estimated infrastructure savings: **~$30/month** (no Lambda + DynamoDB + scheduler stack).

Whether this is worth it depends entirely on your workload. For my 7-agent newsletter, it was. For a single-prompt summarizer, it absolutely isn't.

---

## When To Use This Pattern (And When Not)

**Use it when you have:**

- Multiple specialized agents (not just "one big prompt")
- Long-horizon execution (multi-minute pipelines)
- Quality gates between agents (a Critic that can reject the Writer)
- Cross-run memory (this week vs last week)
- Operational quietness as a goal

**Don't use it for:**

- Single-shot tasks (a summarizer needs one agent, not a hosted runtime)
- Workflows you fully control (deterministic logic doesn't need an agent)
- Low-stakes outputs (no Critic needed if a misfire costs nothing)
- High-frequency tasks (sub-minute pipelines don't benefit from session overhead)

Crux AI is intentionally over-engineered for one subscriber. The point was to exercise every Managed Agents pattern in production — parallel branches, feedback loops, shared session state, durable resume, custom tools. As a learning exercise, it's the right shape. As a 1-subscriber newsletter, it absolutely is not.

---

## What I'd Do Differently

A few things I'd start with next time:

1. **Build resume from day one.** Mid-pipeline crashes are inevitable. Adding `--session-id` later required refactoring the Writer/Critic loop into a state-driven pattern. Cheaper to design that way upfront.

2. **Structured logging from the first commit.** I added it after I needed it. Would have saved hours of `print()`-debugging.

3. **Per-tool modules from the start.** Refactoring an embedded tool into its own module is annoying. Starting with `tools/email.py` from the beginning costs nothing.

4. **GitHub Actions before Vercel Cron.** I tried Vercel first because I was already there. Should have benchmarked timeouts before assuming.

5. **Read the docs before assuming what a vendor feature does.** I lost a few hours to "Vault" being narrower than I thought.

---

## The Meta-Lesson

The hardest part of this project wasn't building agents. It was **understanding which architectural patterns matter for which workloads**.

Managed Agents is a tool for systems that need three things together: **specialization**, **coordination**, and **durable state**. If you don't need all three, you don't need Managed Agents. A `requests.post()` to the Messages API will do.

But if you do need all three — and the next batch of "AI features" being built into products often will — the patterns this project exercised will recur. Event-sourced session logs. Per-tool retry. Cattle-not-pets orchestrators. Critic loops with bounded retries. Auto-fetched live state instead of static config. Quality rubrics with structured reasoning.

These aren't novel patterns. They're 30 years of distributed systems wisdom, applied to a new substrate. The substrate happens to be agents, but the engineering is the same.

---

*Code: [github.com/pranamya123/crux-ai](https://github.com/pranamya123/crux-ai). Live: [ai-weekly-ecru.vercel.app](https://ai-weekly-ecru.vercel.app). Architecture doc in `ARCHITECTURE.md`.*
