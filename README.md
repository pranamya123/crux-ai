# Crux AI

Crux AI is an autonomous weekly AI newsletter. Eight specialized Anthropic Managed Agents coordinate through a shared event-sourced session log to research, evaluate, write, critique, verify, and deliver one issue every Thursday. Nobody touches it.

🌐 **Live:** [crux-ai-weekly.vercel.app](https://crux-ai-weekly.vercel.app)
📰 **Latest issue:** [crux-ai-weekly.vercel.app/latest](https://crux-ai-weekly.vercel.app/latest)
📐 **Design docs:** [ARCHITECTURE.md](./ARCHITECTURE.md) · [DEPLOYMENT.md](./DEPLOYMENT.md)

---

## Architecture

<img width="1536" height="1024" alt="Crux AI architecture" src="https://github.com/user-attachments/assets/507d18b2-edf7-4cf2-8d70-81257d372e09" />

For the full system design, component breakdown, and end-to-end data flow, see [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## Numbers

| Metric | Value |
|--------|-------|
| Cost per run (steady state) | **$2.08** average; range $0.77 to $3.91 |
| Cache hit rate on Opus inputs | **83%** (automatic, via Anthropic prompt caching) |
| Silent agent-failure rate | **0%** (down from 33% in early runs after the placeholder fix) |
| Wall-clock per run | **8 to 11 minutes** |
| Total project spend | **$49.04** in Anthropic API credits across the entire project lifecycle |
| Time to working MVP on Managed Agents | **~3 days** (vs. estimated 2 to 3 weeks for an equivalent custom harness) |
| Lines of orchestration code | **~600** (vs. ~1,500 estimated for an equivalent custom harness) |

---

## What it does

Each Thursday at 9am UTC, GitHub Actions triggers a six-step pipeline: research, evaluate, write, critique, verify, deliver. The pipeline runs roughly ten minutes, costs about two dollars in API credits, and produces an issue of *The Weekly Signal* that is emailed to subscribers and published at `/latest`.

A small Flask app on Vercel handles the public web layer (subscribe form, latest issue, admin page, unsubscribe link). Supabase holds the subscriber list, which the Delivery agent queries on every run.

**Stack:** Anthropic Managed Agents (Opus 4.7 + Haiku 4.5), Memory Stores (JSONL), GitHub Actions, Flask + Vercel, Supabase, SMTP, Python 3.11.

---

## The eight agents

| # | Agent | Model | Role |
|---|-------|-------|------|
| 1 | Memory | Haiku 4.5 | Reads coverage from the previous twelve issues so the pipeline avoids repetition |
| 2 | Research Launches | Opus 4.7 | Finds significant AI ecosystem developments from the past seven days |
| 3 | Research Papers | Opus 4.7 | Finds research papers an engineer or PM could act on in production |
| 4 | Evaluator | Opus 4.7 | Scores and ranks candidates against a structured rubric (relevance, depth, novelty) |
| 5 | Writer | Opus 4.7 | Drafts the brief in markdown with mandatory citations on every claim |
| 6 | Critic | Opus 4.7 | Reviews quality, structure, citations, and banned words; can reject back to the Writer |
| 7 | Verifier | Haiku 4.5 | HTTP HEAD-checks every URL in the approved draft; queries the arXiv API for paper IDs; rejects on bad links |
| 8 | Delivery | Haiku 4.5 | Per-recipient HTML rendering with personalized unsubscribe; SMTP send |

All eight agents coordinate through a single append-only event log at `/mnt/memory/session_{id}.jsonl`. Each agent emits its work as an event. Downstream agents read those events through custom tools rather than through any conversational context. Treating the session as a database rather than as a context window turned out to be the most consequential decision in the project: resume from crash, replay debugging, and cross-agent coordination all become straightforward consequences of state living outside the agents themselves.

---

## Design choices

A handful of decisions materially shaped the system.

**The session log is an append-only JSONL file.** The full session lives in `/mnt/memory/session_{id}.jsonl`, streamable, trivially inspectable with `cat | jq`, with zero schema migrations and portable to any storage backend. There are no native indexes, but at roughly thirty events per run that has not been a problem in practice.

**Three of the eight agents run on Haiku.** Memory, Verifier, and Delivery handle mechanical work (reading prior coverage, HTTP-checking URLs, sending email) and run on Haiku 4.5. The other five run on Opus 4.7 for the work that requires actual judgment (research, evaluation, writing, critique). The split cuts about forty percent off cost compared to all-Opus, with no observable quality drop on the Haiku-assigned tasks.

**Each tool has its own retry policy.** SMTP and filesystem failures have completely different error profiles, so a single global retry policy would be wrong for one of them. The memory store retries three times with a half-second initial backoff. SMTP retries twice with two seconds. The retry decorator (about fifty lines in `retry.py`) recognizes Anthropic's transient error patterns (rate limits, 5xx, "overloaded") and treats authentication failures and 4xx errors as immediate, non-retryable failures.

**Each agent has a criticality class.** When the Memory agent fails, the issue ships without covered topics, which is acceptable. When the Delivery agent fails, the issue does not go out and the orchestrator aborts. Optional agents getting wedged on a timeout do not bring down the whole pipeline; critical failures stop it.

**The orchestrator inserts placeholder events for silent agent failures.** In two of the first three production runs, the Research Papers agent silently exited without emitting its terminal event (no error, no timeout, no exception in the logs). The orchestrator now checks for the expected terminal event after each step and inserts a placeholder if missing, so the rest of the pipeline can continue and the failure stays loud in the logs. Silent failure rate dropped from one in three runs to zero.

**A dedicated Verifier agent grounds every URL.** The Verifier runs after Critic approval and before delivery. It HTTP HEAD-checks every URL in the approved draft and validates arXiv links against the arXiv API by paper ID. Fabricated citations get caught before they reach subscribers. The Verifier runs on Haiku, which keeps its cost at about two cents per run.

**The Writer's output and the email renderer share a structural contract.** The Writer's markdown is parsed by `email_renderer.py` via regex. A late prompt change once silently broke the renderer; the email arrived completely empty under the section headers. The contract is now enforced from both ends. The Writer prompt mandates the `### N. Title` format, the Critic rejects drafts that do not conform, and a renderer smoke test runs before any prompt change ships.

**GitHub Actions handles the weekly cron, not Vercel.** Vercel Hobby caps function execution at sixty seconds and the pipeline runs eight to fifteen minutes. After three abandoned designs (a single monolithic Vercel function, a step-based architecture chained through async HTTP calls, and a Pro upgrade), GitHub Actions turned out to be the right answer: a six-hour timeout, free, single YAML config.

**The pipeline can resume from any step via `--session-id`.** The Writer/Critic loop is state-driven, deciding what to run next based on event counts in the session log, so resume always lands on the correct state, even mid-loop.

The full discussion of these decisions, including alternatives considered, lives in [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## Running it

Crux AI runs as a deployed service. GitHub Actions handles the weekly cron and runs the orchestrator on the GitHub-hosted runner; Vercel hosts the web layer and auto-deploys on every push. The orchestrator also runs locally for development.

### Production (autonomous, every Thursday 9am UTC)

GitHub Actions triggers `python3 orchestrator_v2.py` on schedule. After a successful run, the workflow commits `latest_issue.html` back to the repo, and Vercel auto-deploys the new issue.

Manual trigger: GitHub Actions tab → "Newsletter Weekly Run" → "Run workflow".

Full setup in [DEPLOYMENT.md](./DEPLOYMENT.md).

### Local (development)

```bash
git clone https://github.com/pranamya123/crux-ai.git
cd crux-ai
pip install -r requirements.txt
cp .env.example .env   # fill in the keys below
python3 orchestrator_v2.py
```

Required env vars:

```bash
ANTHROPIC_API_KEY=sk-ant-...
SMTP_USER=you@gmail.com
SMTP_PASSWORD=...               # Gmail app password
SMTP_FROM=you@gmail.com
RECIPIENT_EMAILS=a@x.com,b@y.com   # fallback; live subscribers come from Supabase
APP_BASE_URL=https://your-domain.vercel.app
SUPABASE_URL=https://...
SUPABASE_ANON_KEY=...
LOCAL_MEMORY_DIR=./memory_local    # local fallback; production uses /mnt/memory
```

Resume a partial run:
```bash
python3 orchestrator_v2.py --session-id newsletter_20260507_090000_a1b2c3d4
```

Run a single step (debug):
```bash
python3 orchestrator_v2.py --session-id newsletter_... --step evaluate
```

Run the web layer locally:
```bash
python3 app.py   # http://127.0.0.1:5000
```

Inspect the most recent run:
```bash
python3 analyze_run.py
```

---

## Repo layout

```
orchestrator_v2.py              # Step-based orchestrator + AgentRunner
observability.py                # StructuredLogger + RunTracker
retry.py                        # Exponential backoff decorator
credentials.py                  # Credential manager
email_renderer.py               # Editorial HTML rendering
analyze_run.py                  # Post-run analysis helper
app.py                          # Flask web layer
index.py                        # Vercel entrypoint
tools/
  memory_store.py               # emit_event, get_events
  email.py                      # send_email_smtp
  subscribers.py                # get_subscribers (Supabase live fetch)
  verifier.py                   # verify_links (HTTP HEAD + arXiv API)
api/
  orchestrate.py                # (Vercel) Manual trigger
  step.py                       # (Vercel) Single-step runner
  status.py                     # (Vercel) Run status
.github/workflows/
  newsletter.yml                # GitHub Actions cron + manual trigger
ARCHITECTURE.md                 # Full design write-up
DEPLOYMENT.md                   # Setup instructions
architecture_diagram.svg        # One-page system diagram
```

---

## Limitations and future work

A few things are honestly missing or broken at the time of writing.

The system was built and tested at single-digit-subscriber scale and has not been load-tested at thousands of subscribers. There is no A/B testing on Writer prompts and no structured alerting beyond GitHub Actions email-on-failure. Per-agent token counts log as 0 because the Anthropic SDK does not populate `usage` on the `session.status_idle` events the orchestrator captures; the total cost remains correct via the Anthropic billing dashboard. The Verifier-failure retry path also has a known bug: when verification fails, the loop short-circuits on the existing `draft_approved` event instead of forcing a new Writer attempt. It has not been hit in production, but it is worth fixing before relying on Verifier loops in higher-stakes pipelines.

Two upgrades are scoped but deferred. RAG over past issues using Supabase pgvector would give the Memory agent semantic dedup rather than string matching; today nothing catches that "Anthropic's flagship release" and "Claude 4 launches" describe the same event. GitHub MCP for the Research Launches agent would give it structured access to live repo data (star counts, commit activity, recent releases) rather than web-search snippets filtered through other people's blog posts.

A public run-history endpoint reading from `runs/*.json` for cost and timing trends over time would be a nice operational addition once there is enough run history to make trends meaningful.

---

## Read more

- **Architecture deep-dive:** [ARCHITECTURE.md](./ARCHITECTURE.md)
- **Deployment guide:** [DEPLOYMENT.md](./DEPLOYMENT.md)
- **Technical writeup (Medium):** [I built an autonomous newsletter to stress-test Anthropic Managed Agents](https://medium.com/@vadlamanipranamya/i-built-an-autonomous-newsletter-to-stress-test-anthropic-managed-agents-332bf683d9e9)
- **Technical writeup (Substack):** [Crux AI: A Production Experiment with Anthropic Managed Agents](https://substack.com/home/post/p-196714067)

---

## Contact

Built by [Pranamya Vadlamani](https://www.linkedin.com/in/pvadlamani1/). Reach out on LinkedIn if you have questions about the project or want to chat.

---

## License

MIT.
