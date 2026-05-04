# AI Weekly

A weekly AI newsletter, written end-to-end by **7 Anthropic Managed Agents** coordinating through a shared event log in Supabase.

🌐 **Live:** [ai-weekly-ecru.vercel.app](https://ai-weekly-ecru.vercel.app)
📐 **Design:** [ARCHITECTURE.md](./ARCHITECTURE.md) · [diagram](./architecture_diagram.svg)

---

## What it does

Generates one issue of *The Weekly Signal* per run — research → evaluate → write → critique → deliver, autonomous. A small Flask app on Vercel handles subscribe / unsubscribe / latest-issue / admin.

## How it's built

```
Memory  →  Research Launches  ⎫
                              ⎬→  Evaluator → Writer ⇄ Critic → Delivery
           Research Papers    ⎭                  (max 2 retries)
```

Seven specialized Managed Agents talk through a shared Supabase event log (`session_events`) via three custom tools: `emit_event`, `get_events`, `send_email_smtp`. Full design in [ARCHITECTURE.md](./ARCHITECTURE.md).

**Stack:** Anthropic Managed Agents (Opus 4.7 + Haiku 4.5) · Supabase · Flask + Vercel · SMTP · Python 3.11.

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
SUPABASE_URL=...                  # or NEXT_PUBLIC_SUPABASE_URL
SUPABASE_ANON_KEY=...             # or ANON_KEY
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...                 # Gmail app password
SMTP_FROM=...                     # defaults to SMTP_USER
RECIPIENT_EMAILS=a@x.com,b@y.com
APP_BASE_URL=https://your-vercel-deployment.vercel.app
```

## Run

```bash
python3 orchestrator_v2.py                                # generate + send
python3 orchestrator_v2.py --session-id newsletter_…      # resume a partial run
python3 app.py                                            # subscribe site, http://127.0.0.1:5000
```

## Routes (web layer)

| Route                         | Method | What it does |
|-------------------------------|--------|--------------|
| `/`                           | GET    | Subscribe form |
| `/api/subscribe`              | POST   | Add email (50-subscriber cap) |
| `/unsubscribe?email=<addr>`   | GET    | Remove email |
| `/latest`                     | GET    | Serve the most recent issue |
| `/admin`                      | GET    | Subscriber list |

## Files

| Path | Purpose |
|---|---|
| `orchestrator_v2.py`         | Multi-agent orchestrator + custom tools + resume |
| `email_renderer.py`          | Editorial HTML email rendering |
| `app.py`                     | Flask app: subscribe / unsubscribe / latest / admin |
| `index.py`                   | Vercel serverless entrypoint |
| `templates/`                 | Subscribe page template |
| `briefs/`                    | Per-run JSON logs (gitignored) |
| `latest_issue.*`             | Snapshot of the most recent send (gitignored) |
| `ARCHITECTURE.md`            | Full design write-up |
| `architecture_diagram.svg`   | One-page system diagram |

## Cost

~$2 per run. Weekly cadence ≈ $8/month.

## License

MIT.
