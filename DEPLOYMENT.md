# Deployment Guide: Crux AI

**Architecture:** GitHub Actions for orchestration + Vercel for web layer.

```
GitHub Actions (Thursday 9am UTC)
     ↓
Runs: python3 orchestrator_v2.py
     ↓
7 Managed Agents → newsletter generated → email sent
     ↓
Commits latest_issue.html back to repo
     ↓
Vercel auto-deploys → /latest endpoint serves new issue
```

**Why this split?**
- Vercel Hobby: 60s function timeout (won't fit our 5-15min pipeline)
- GitHub Actions: 6-hour timeout, free, simple YAML config
- Vercel: still hosts subscribe form, /latest, /admin, /unsubscribe

---

## Prerequisites

- ✅ GitHub repo with this code pushed
- ✅ Vercel account with Hobby tier (free)
- ✅ Vercel project linked to your GitHub repo

---

## Step 1: Configure GitHub Secrets

Go to your **GitHub repo** → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:

| Secret Name | Value | Required |
|-------------|-------|----------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | ✅ Yes |
| `SMTP_USER` | Gmail address | ✅ Yes |
| `SMTP_PASSWORD` | Gmail App Password | ✅ Yes |
| `APP_BASE_URL` | `https://your-domain.vercel.app` | ✅ Yes |
| `SUPABASE_URL` | Your Supabase project URL | ✅ Yes — auto-fetches subscribers |
| `SUPABASE_ANON_KEY` | Supabase anon key | ✅ Yes — auto-fetches subscribers |
| `RECIPIENT_EMAILS` | `email1@x.com,email2@y.com` | ❌ Fallback only (used if Supabase fails) |
| `SMTP_HOST` | `smtp.gmail.com` (default) | ❌ No |
| `SMTP_PORT` | `587` (default) | ❌ No |
| `SMTP_FROM` | Sender (defaults to SMTP_USER) | ❌ No |

**Subscriber List Source:**
- ✅ **Primary:** Supabase `subscribers` table (auto-fetched on every run)
- 🔄 **Fallback:** `RECIPIENT_EMAILS` env var (only if Supabase unavailable)

This means subscribe/unsubscribe via the website automatically takes effect on the next Thursday's send. **No manual env var updates needed.**

### Gmail App Password:
1. Enable 2FA: https://myaccount.google.com/security
2. Generate App Password: https://myaccount.google.com/apppasswords
3. Use the 16-character password as `SMTP_PASSWORD`

---

## Step 2: Configure Vercel Environment Variables

These are needed for the web layer (status endpoint, /latest serving).

Go to **Vercel Dashboard** → Your Project → **Settings** → **Environment Variables**

Same secrets as GitHub (Vercel needs them for the Flask app + status endpoint).

---

## Step 3: Push Code to GitHub

```bash
cd /Users/pranamyavadlamani/Desktop/agent_app
git add .
git commit -m "deploy: github actions + vercel web layer"
git push origin main
```

Vercel auto-deploys the web layer.
GitHub Actions auto-installs the workflow.

---

## Step 4: Verify GitHub Actions Workflow

1. Go to **GitHub repo** → **Actions** tab
2. You should see **"Newsletter Weekly Run"** workflow
3. Click on it → see the schedule (every Thursday 9am UTC)

---

## Step 5: Test Manually (Recommended)

Before waiting until Thursday, trigger manually:

1. Go to **Actions** tab → **Newsletter Weekly Run**
2. Click **"Run workflow"** button (top right)
3. Leave session_id blank, click **Run workflow**
4. Watch the logs

Expected output:
```
✓ Setup Python
✓ Install dependencies
✓ Run orchestrator
  - Memory agent runs
  - Research (parallel) runs
  - Evaluator runs
  - Writer/Critic loop runs
  - Delivery runs (email sent)
✓ Commit latest issue back to repo
✓ Upload run artifacts
```

After successful run:
- ✅ Email sent to RECIPIENT_EMAILS
- ✅ `latest_issue.html` committed to repo
- ✅ Vercel auto-deploys the new issue
- ✅ `/latest` page shows new newsletter

---

## How It Works

### Schedule: Every Thursday 9am UTC

In `.github/workflows/newsletter.yml`:
```yaml
on:
  schedule:
    - cron: '0 9 * * 4'   # Thursday 9am UTC
```

### Manual Trigger / Resume

In GitHub Actions UI, click "Run workflow" with optional `session_id`:
- Empty → fresh run
- With session_id → resume from last checkpoint (uses Memory Stores)

### Run Artifacts

Each run uploads logs + run summary as GitHub artifacts (30-day retention):
- `logs/` — structured JSON logs per session
- `runs/` — run summary metrics
- `briefs/` — full agent output logs
- `memory_local/` — Memory Stores JSONL files

Download from **Actions** → click run → **Artifacts** section

### Monitoring

While a run is happening:
```bash
# Check status (Vercel reads its own logs)
curl https://your-domain.vercel.app/api/status?session_id=<id>

# See recent runs (last 20)
curl https://your-domain.vercel.app/api/status?recent=true
```

Note: Vercel's `/api/status` reads from Vercel's filesystem. Since GitHub Actions runs the orchestrator, Vercel won't have full state unless you set up shared storage. For now:
- **GitHub Actions logs**: full visibility (real-time during run)
- **Vercel `/latest`**: shows last published newsletter
- **Email**: delivered to subscribers

---

## Changing the Schedule

Edit `.github/workflows/newsletter.yml`:
```yaml
on:
  schedule:
    - cron: '0 9 * * 4'  # ← Change this
```

Common schedules:
```
0 9 * * 4      → Every Thursday at 9am UTC
0 9 * * 1      → Every Monday at 9am UTC
0 9 * * 0      → Every Sunday at 9am UTC
0 9 * * 1-5    → Every weekday at 9am UTC
0 0 1 * *      → First of month at midnight UTC
```

Push the change → workflow updates automatically.

---

## Troubleshooting

### Issue: Workflow not running on schedule

**Check:**
1. Repo is not private with restrictions
2. Workflow file syntax is valid (GitHub Actions tab → look for errors)
3. Schedule cron syntax: https://crontab.guru/

**Note:** GitHub Actions cron is best-effort. May run within ~15 min of scheduled time.

### Issue: "Permission denied" when committing latest_issue

**Fix:** Workflow already has `permissions: contents: write`. If still failing:
1. Repo Settings → Actions → General → Workflow permissions
2. Set to **"Read and write permissions"**

### Issue: Email not sending

**Check:**
1. SMTP secrets correct in GitHub Settings → Secrets
2. Gmail: Did you generate an app password? (not regular password)
3. Look at workflow logs for SMTP error

### Issue: Anthropic API errors

**Check:**
1. `ANTHROPIC_API_KEY` is valid and has credits
2. Look at logs for rate limit / quota errors
3. Retry logic handles transient errors (3 attempts)

### Issue: Long-running workflow

GitHub Actions: 6 hour limit (job-level), workflow has `timeout-minutes: 30`. If your pipeline exceeds 30 min, increase `timeout-minutes` in the workflow file.

---

## Costs

| Service | Cost | Notes |
|---------|------|-------|
| GitHub Actions | Free | 2000 min/month for private repos, unlimited for public |
| Vercel Hobby | Free | Web layer only (no cron needed now) |
| Anthropic API | ~$2-8/run | Token usage by Managed Agents |
| Gmail SMTP | Free | Rate limited by Gmail |

**Monthly estimate (4 runs):** ~$8-32 in Anthropic API costs.

---

## Rollback

If something breaks after a deployment:

1. **Web layer (Vercel):** Dashboard → Deployments → Rollback to previous
2. **Orchestrator (GitHub Actions):** `git revert <commit>` then push
3. **Manual emergency stop:** Disable workflow in Actions tab → click ⋯ → Disable workflow

---

## Local Development

```bash
# Run orchestrator locally (uses .env file)
python3 orchestrator_v2.py

# Resume a session
python3 orchestrator_v2.py --session-id newsletter_...

# Run a single step (debug)
python3 orchestrator_v2.py --session-id newsletter_... --step evaluate

# Check status
python3 api/status.py newsletter_...

# Recent runs
python3 api/status.py recent

# Run web app locally
python3 app.py
# → http://127.0.0.1:5000
```

---

## Migration Checklist

If migrating from previous Vercel cron setup:

- [x] Created `.github/workflows/newsletter.yml`
- [x] Removed `crons` from `vercel.json`
- [x] Updated `.gitignore` to allow `latest_issue.*`
- [ ] Add GitHub Secrets (Settings → Secrets and variables → Actions)
- [ ] Push code to GitHub
- [ ] Verify workflow appears in Actions tab
- [ ] Test with manual trigger (Run workflow button)
- [ ] Verify email received + Vercel `/latest` updated
- [ ] Wait for scheduled Thursday 9am run

---

## Questions?

See:
- `ARCHITECTURE.md` — full system design
- `MIGRATION.md` — Phase 1/2/3 migration history
- `orchestrator_v2.py` — implementation
