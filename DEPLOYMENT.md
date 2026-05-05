# Deployment Guide: AI Weekly on Vercel

Automated weekly newsletter generation via Vercel Crons.

---

## Prerequisites

- Vercel account (free tier works)
- GitHub repo connected to Vercel (or use Vercel CLI)
- Environment variables ready (see below)

---

## Step 1: Set Up Environment Variables in Vercel

Go to **Vercel Dashboard** → Your Project → **Settings** → **Environment Variables**

Add these variables:

| Variable | Value | Required |
|----------|-------|----------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | ✅ Yes |
| `SMTP_USER` | Gmail address (or SMTP user) | ✅ Yes |
| `SMTP_PASSWORD` | Gmail app password (or SMTP password) | ✅ Yes |
| `RECIPIENT_EMAILS` | `email1@x.com,email2@y.com` | ✅ Yes |
| `APP_BASE_URL` | `https://your-domain.vercel.app` | ✅ Yes |
| `SMTP_HOST` | `smtp.gmail.com` (default) | ❌ No |
| `SMTP_PORT` | `587` (default) | ❌ No |
| `SMTP_FROM` | Sender email (defaults to SMTP_USER) | ❌ No |

### For Gmail Users:
1. Enable 2FA on your Google account
2. Generate an App Password: https://myaccount.google.com/apppasswords
3. Use the 16-character app password as `SMTP_PASSWORD`

---

## Step 2: Deploy to Vercel

### Option A: GitHub Integration (Recommended)

1. Push your code to GitHub:
   ```bash
   cd /Users/pranamyavadlamani/Desktop/agent_app
   git add .
   git commit -m "feat: add Vercel orchestrator with crons"
   git push origin main
   ```

2. Vercel auto-deploys on push (if configured)

3. Verify deployment:
   - Vercel Dashboard → Deployments → Check latest is green ✅

### Option B: Vercel CLI

1. Install CLI (if not already):
   ```bash
   npm install -g vercel
   ```

2. Deploy:
   ```bash
   cd /Users/pranamyavadlamani/Desktop/agent_app
   vercel --prod
   ```

3. Follow prompts to link project

---

## Step 3: Verify Cron is Active

1. Go to **Vercel Dashboard** → Your Project
2. Look for **Crons** tab (or **Settings** → **Crons**)
3. You should see:
   ```
   /api/orchestrate
   Schedule: 0 9 * * 4 (Every Thursday at 9:00 AM UTC)
   Status: Active ✅
   ```

---

## Step 4: Test the Orchestrator (Optional)

### Test the endpoint manually:

```bash
curl https://your-domain.vercel.app/api/orchestrate
```

Response should be:
```json
{
  "success": true,
  "session_id": "newsletter_YYYYMMDD_HHMMSS_xxxxxxxx",
  "message": "Newsletter generated and sent successfully"
}
```

### Monitor logs:

Vercel Dashboard → Your Project → **Functions** → `api/orchestrate.py` → View logs

---

## How It Works

### Weekly Schedule:
- **Time:** Every Thursday at 9:00 AM UTC
- **What happens:**
  1. Vercel triggers `/api/orchestrate`
  2. `orchestrate.py` runs `OrchestratorV2().orchestrate()`
  3. 7 Managed Agents run: Memory → Launches → Papers → Evaluator → Writer → Critic → Delivery
  4. Newsletter emailed to all subscribers
  5. `latest_issue.html` saved for `/latest` endpoint
  6. Session log written to `/memory_local/` or `/mnt/memory/`

### Changing the Schedule:

Edit `vercel.json`:
```json
"crons": [
  {
    "path": "/api/orchestrate",
    "schedule": "0 9 * * 4"  ← Change this (4 = Thursday)
  }
]
```

Redeploy and verify in Vercel Dashboard.

**Common schedules:**
```
0 9 * * 0      → Every Sunday at 9am UTC
0 9 * * 1      → Every Monday at 9am UTC
0 9 * * 4      → Every Thursday at 9am UTC
0 9 * * 1-5    → Every weekday at 9am UTC
0 0 1 * *      → First of month at midnight UTC
*/30 * * * *   → Every 30 minutes
```

---

## Troubleshooting

### Issue: Cron not running

**Check:**
1. Is the deployment green (successful)? → Vercel Dashboard → Deployments
2. Is the cron enabled? → Settings → Crons → `/api/orchestrate` should show "Active"
3. Check logs: Functions → `orchestrate.py` → Logs

### Issue: Email not sending

**Check:**
1. SMTP credentials correct in Vercel Environment Variables
2. Gmail: Did you generate an app password? (not your regular password)
3. Logs: Look for SMTP error messages

### Issue: Managed Agent fails

**Check:**
1. `ANTHROPIC_API_KEY` is valid
2. API key has sufficient quota/credits
3. Agent IDs in `orchestrator_v2.py` match your Console agents
4. Logs: Full error message in Vercel Functions logs

---

## Monitoring & Alerts

### Option 1: Vercel Alerts (Built-in)

Vercel can email you on cron failure. Enable in:
- **Project Settings** → **Notifications** → Configure alerts

### Option 2: Manual Monitoring

Check logs weekly:
1. Vercel Dashboard → Functions → `orchestrate.py`
2. Look for "Newsletter generated and sent successfully" message
3. Check `/latest` endpoint to see most recent issue

### Option 3: Email Confirmation

Each newsletter email includes subject line with send timestamp. If you don't receive the email by expected time, something failed.

---

## Cost & Billing

- **Vercel Functions:** Free tier includes serverless execution (no extra cost)
- **Vercel Crons:** Free tier includes up to 100 cron invocations/month
- **Anthropic API:** You pay for tokens used (Managed Agents)
- **Email:** SMTP via Gmail (free, rate-limited)

**Estimated monthly cost:** ~$8-35 (depends on agent complexity and token usage per run)

---

## Next Steps

1. ✅ Set environment variables in Vercel Dashboard
2. ✅ Push code to GitHub / Deploy via Vercel
3. ✅ Verify cron is active
4. ✅ Wait for Thursday 9am UTC (or change schedule to test sooner)
5. ✅ Check logs to confirm success
6. ✅ Verify email received and `/latest` endpoint updated

---

## Updating the Code

When you make changes to `orchestrator_v2.py` or any agent prompts:

1. Commit and push to GitHub:
   ```bash
   git add .
   git commit -m "update: improve agent prompts"
   git push origin main
   ```

2. Vercel auto-deploys

3. Your cron will use the new code on the next scheduled run

---

## Rollback

If something breaks on the new deployment:

1. Vercel Dashboard → Deployments → Find last known good deployment
2. Click "Rollback" or redeploy from earlier commit

---

## Questions?

See `MIGRATION.md` for architecture details and `orchestrator_v2.py` for implementation.
