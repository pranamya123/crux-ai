# I Built a Newsletter That Writes Itself

I find it hard to keep up with what's happening in AI. New models drop weekly. Research papers stack up faster than I can read them.

So I built a weekly newsletter that surfaces the meaningful work and pairs it with two research papers I can actually apply in production. It runs every Thursday at 9am UTC. I don't touch it. Each issue costs about $2 in API credits.

This was also an excuse to use Anthropic's Managed Agents on a real workload. The "[Decoupling the brain from the hands](https://www.anthropic.com/engineering/scaling-managed-agents)" article from April had been sitting in my head — stateless harnesses, brains separate from tools, a session log that outlasts both. I wanted to feel that in practice.

## How I started

I started with one agent in the Claude Console, just to feel the surface area.

A few decisions in the first hour: SMTP via Gmail because I didn't want vendor lock-in for an MVP. Many agents eventually, but starting with one and splitting roles as the boundaries became obvious. Where state lives — I didn't have an answer yet.

By Sunday, I had a single agent dumping markdown to a file. By Monday, a Flask app on Vercel for subscribers and a working pipeline that emailed me a brief.

Then I realized I was using Managed Agents like a fancier API client. Durable sessions, hot-swappable agents, the brain/hands/session decoupling — none of it was showing up in my code.

So I rewrote it.

## The architecture

Seven agents, each with one job:

- **Memory** (Haiku) reads what we covered last week
- **Research Launches** (Opus) finds AI ecosystem developments
- **Research Papers** (Opus) finds actionable research
- **Evaluator** (Opus) scores and ranks with a rubric
- **Writer** (Opus) drafts the brief in markdown
- **Critic** (Opus) reviews and can reject
- **Delivery** (Haiku) renders per-recipient and sends via SMTP

All seven coordinate through one shared event log in Anthropic Memory Stores (`/mnt/memory/session_{id}.jsonl`). Each agent emits its work as an event. Downstream agents read prior events through custom tools. Everything important lives in the log; the orchestrator just routes between agents.

That single choice — treating the session as a database — turned out to be the most consequential decision in the project. Resume from crash, replay debugging, cross-agent coordination all become trivial when state lives outside the agents.

The orchestrator itself is a stateless Python process running on GitHub Actions. Vercel hosts the web layer (subscribe form, `/latest`, `/admin`). Supabase holds the subscriber list. That's the whole system.

The mental model from the Anthropic article maps directly: the **brain** is a Managed Agent. The **hand** is a custom tool in `tools/*.py`. The **session** is the JSONL event log. The **harness** is `orchestrator_v2.py`. Each can fail or be replaced without the others noticing.

## Making it production-ready

A working prototype isn't a production system. The gap is observability, error handling, scalability, and the ability to debug at 9am Thursday when something breaks.

### Observability

Two primitives, both deliberately minimal. A `StructuredLogger` emits one JSON line per event with session_id, agent name, timing, token usage. Logs go to stdout (GitHub Actions captures them) and to per-session log files uploaded as workflow artifacts with 30-day retention.

A `RunTracker` accumulates per-agent timing, token counts, retry counts, and success/failure into a single `runs/{session_id}.json` summary after each run.

I considered Datadog, Sentry, OpenTelemetry. At four runs a month, the integration cost outweighs the value. Structured JSON to stdout stays portable for whichever vendor I'd adopt later.

### Error handling, three layers

**Per-tool retry.** Each tool gets its own retry policy because SMTP failures and filesystem failures have very different error profiles. Memory store retries 3 times with 0.5s initial backoff. SMTP retries twice with 2s. The retry decorator (~50 lines in `retry.py`) recognizes Anthropic's transient errors — rate limits, 5xx, "overloaded" — and lets auth failures and 400s fail immediately.

**Per-agent timeout and criticality.** Each agent has a criticality class. Memory failing means we ship without covered topics, which is fine. Delivery failing means the issue doesn't go out, so the orchestrator aborts. Optional agents getting wedged on a timeout shouldn't kill the pipeline.

**Pipeline-level fallbacks.** This is the most important defense, and almost no one talks about it.

In two of my first three production runs, the Research Papers agent silently exited without emitting its terminal event. No error. No timeout. The agent just stopped. The Evaluator sat waiting forever.

The fix needed two layers. First, I added "ALWAYS call emit_event before finishing, even if the array is empty" to the agent's prompt. Second, after each step the orchestrator checks for the terminal event and inserts a placeholder if it's missing:

```python
{
  "event_type": "papers_researched",
  "data": { "papers": [], "auto_inserted": True }
}
```

The Evaluator runs on the placeholder. The pipeline ships an issue with launches only. The failure stays loud in the logs. Silent failure rate went from 33% to zero.

The deeper lesson: agents amplify failures. A bad env var in a normal script is one HTTP 400 and a clean exit. In an agent loop, the agent reacts to the 400, those reactions cost retries, and the session can poison itself. Validate at the orchestrator boundary with a synchronous error before the agent ever sees it.

### Scalability

I made one good decision early and one bad decision late.

Early: every agent runs a fresh Managed Agents session. The orchestrator process is stateless. The runner is ephemeral. Everything is replaceable.

Late: I tried to deploy the orchestrator on Vercel Cron. Vercel Hobby caps functions at 60 seconds. My pipeline runs 8–15 minutes. Pro caps at 5 minutes. I went through three abandoned designs — single big function, step-based with async HTTP chaining, Pro upgrade — before landing on GitHub Actions. Six-hour timeout, free, single YAML file. The lesson is to match the orchestration platform to the workload duration.

### Debugging

Three habits made this tractable. First, resume from any step via `--session-id`. If something breaks at the Critic, I rerun from there instead of replaying everything before it. Saves about $2 per crash.

Second, the Writer/Critic loop is state-driven. It counts events in the session log to decide what runs next: if drafts equal rejections, the Writer needs to write. If drafts exceed rejections, the Critic needs to review. Resume picks up at the correct state automatically, even mid-loop.

Third, log every silently-handled exception. Every `except: pass` is a future debugging session where I'd wonder why the program ended. A single line to stderr is cheap insurance.

## What I learned

I instrumented enough to know what actually moved the needle. Five things stand out.

**Prompt caching is the biggest free lever.** It cut my cost from an estimated $8.70 per run to an actual $2.08 — about 76% — with zero engineering. Cache hit rate sits at 83% on Opus inputs. All I had to do was structure prompts so the static parts come first. Most teams worry about token costs while leaving cacheability on the table.

**Tier your models.** Three of seven agents run on Haiku, four on Opus. That cut another 40% off the cost with no observable quality loss. Save Opus for the agents doing actual judgment — research, writing, critique. Use Haiku for the mechanical work — memory pulls, SMTP sends, simple transformations.

**Parallelize where work is independent.** Research Launches and Research Papers run in a `ThreadPoolExecutor`. That cut the research step's wall-clock in half. Trivial code change.

**Critic loops are worth their cost when audience matters.** Adding the Critic adds about $0.58 per run, roughly 30%, and catches an estimated half of the bad drafts. If you're shipping to humans, it's worth it. If output quality doesn't matter, skip it.

**Resume support pays for itself fast.** Event sourcing makes it nearly free once you've committed to it. Worth the design effort for any pipeline over a minute.

A few numbers worth keeping:

- Cost per run: $2.08 average, $0.77 lowest, $3.91 highest
- Wall-clock: 8 minutes typical, 12 minutes with a Critic retry
- Cache hit rate: 83% on Opus inputs
- Silent failure rate: 33% before the placeholder fix, 0% after
- Time to MVP on Managed Agents: ~3 days. Estimated time on a custom harness: 2–3 weeks

## When this pattern fits

This pattern fits when you have multiple specialized agents with genuinely different jobs, long-horizon execution, quality gates between agents, cross-run memory needs, and operational quietness as a goal.

It's overkill for single-shot tasks, deterministic workflows, low-stakes outputs, and high-frequency pipelines where session overhead dominates.

AI Weekly is intentionally over-engineered for one subscriber. The point was to exercise every Managed Agents pattern in production — parallel branches, feedback loops, shared session state, durable resume, custom tools. As a learning exercise, it's the right shape. As a one-subscriber newsletter, it absolutely is not.

The hardest part of this project was figuring out which architectural patterns matter for which workloads. Managed Agents fits systems that need specialization, coordination, and durable state together. If you only need one or two of those, a direct call to the Messages API will do the job at a fraction of the complexity.

When you do need all three, the patterns here will recur. Event-sourced session logs. Per-tool retry. Stateless orchestrators with replaceable runners. Critic loops with bounded retries. Auto-fetched live state. Quality rubrics with structured reasoning.

None of these patterns are new. It's 30 years of distributed systems wisdom on a new substrate. The substrate happens to be agents. The engineering carries over.

---

*Code: [github.com/pranamya123/ai-weekly](https://github.com/pranamya123/ai-weekly). Live: [ai-weekly-ecru.vercel.app](https://ai-weekly-ecru.vercel.app).*
