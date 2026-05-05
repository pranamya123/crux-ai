# AI Weekly

A weekly AI newsletter, written end-to-end by **7 Anthropic Managed Agents** coordinating through a shared session log (Anthropic Memory Stores).

🌐 **Live:** [ai-weekly-ecru.vercel.app](https://ai-weekly-ecru.vercel.app)
📐 **Design:** [ARCHITECTURE.md](./ARCHITECTURE.md) · [Deployment](./DEPLOYMENT.md) · [diagram](./architecture_diagram.svg)

---

## What it does

Generates one issue of *The Weekly Signal* per run — research → evaluate → write → critique → deliver, autonomous. Runs every Thursday 9am UTC via GitHub Actions. A small Flask app on Vercel handles subscribe / unsubscribe / latest-issue / admin.

## How it's built

```
GitHub Actions Cron (Thursday 9am UTC)
  ↓
Memory  →  Research Launches  ⎫
                              ⎬→  Evaluator → Writer ⇄ Critic → Delivery
           Research Papers    ⎭                  (max 2 retries)
  ↓
Email sent + latest_issue.html committed → Vercel auto-deploys
```

Seven specialized Managed Agents talk through a shared event log (Anthropic Memory Stores) via custom tools (`emit_event`, `get_events`, `send_email_smtp`). Each tool is its own decoupled module ("many hands" pattern). Full design in [ARCHITECTURE.md](./ARCHITECTURE.md).

**Stack:** Anthropic Managed Agents (Opus 4.7 + Haiku 4.5) · Memory Stores · GitHub Actions · Flask + Vercel · SMTP · Python 3.11.

## Quick start

```bash
git clone https://github.com/pranamya123/ai-weekly.git
cd ai-weekly
pip install -r requirements.txt
cp .env.example .env       # fill in the keys below
```

Required env vars:

```
ANTHROPIC_API_KEY=...
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...                 # Gmail app password (or retrieve from Anthropic vault)
SMTP_FROM=...                     # defaults to SMTP_USER
RECIPIENT_EMAILS=a@x.com,b@y.com
APP_BASE_URL=https://your-vercel-deployment.vercel.app
LOCAL_MEMORY_DIR=./memory_local   # (optional) for local testing; uses /mnt/memory in Managed Agents
```

Note: Session state is persisted in **Anthropic Memory Stores** (`/mnt/memory/` in Managed Agents environment), not Supabase. Credentials (SMTP password) should be stored in Anthropic's credential vault for production.

## Run

```bash
# Local: full pipeline
python3 orchestrator_v2.py

# Local: resume a partial run
python3 orchestrator_v2.py --session-id newsletter_…

# Local: run a single step (debug)
python3 orchestrator_v2.py --session-id newsletter_… --step evaluate

# Web layer locally
python3 app.py                  # http://127.0.0.1:5000

# Production: GitHub Actions runs every Thursday 9am UTC automatically
# Manual trigger: GitHub Actions tab → "Newsletter Weekly Run" → Run workflow
```

## Routes (Vercel web layer)

| Route                         | Method | What it does |
|-------------------------------|--------|--------------|
| `/`                           | GET    | Subscribe form |
| `/api/subscribe`              | POST   | Add email (50-subscriber cap) |
| `/unsubscribe?email=<addr>`   | GET    | Remove email |
| `/latest`                     | GET    | Serve the most recent issue |
| `/admin`                      | GET    | Subscriber list |
| `/api/status?session_id=<id>` | GET    | Live state of a run |
| `/api/status?recent=true`     | GET    | Last 20 runs |

## Files

| Path | Purpose |
|---|---|
| `orchestrator_v2.py`           | Multi-agent orchestrator (step-based) + AgentRunner |
| `tools/memory_store.py`        | Hand 1: emit_event, get_events |
| `tools/email.py`               | Hand 2: send_email_smtp |
| `observability.py`             | Structured JSON logger, RunTracker, run history |
| `retry.py`                     | Exponential backoff retry decorator |
| `credentials.py`               | Credential manager |
| `email_renderer.py`            | Editorial HTML email rendering |
| `app.py`                       | Flask web app |
| `index.py`                     | Vercel serverless entrypoint |
| `api/orchestrate.py`           | (Vercel) Manual trigger endpoint |
| `api/step.py`                  | (Vercel) Single-step runner |
| `api/status.py`                | (Vercel) Run status / recent runs |
| `.github/workflows/newsletter.yml` | GitHub Actions: weekly cron + manual trigger |
| `vercel.json`                  | Vercel config (web layer only, no cron) |
| `templates/`                   | Subscribe page template |
| `briefs/`                      | Per-run JSON logs (gitignored) |
| `logs/`                        | Per-session structured logs (gitignored) |
| `runs/`                        | Run summary metrics (gitignored) |
| `memory_local/`                | Local Memory Stores fallback (gitignored) |
| `latest_issue.*`               | Latest newsletter (committed by GitHub Actions) |
| `ARCHITECTURE.md`              | Full design write-up |
| `DEPLOYMENT.md`                | Setup instructions |
| `architecture_diagram.svg`     | One-page system diagram |

## Cost

~$2 per run. Weekly cadence ≈ $8/month.

## License

MIT.
