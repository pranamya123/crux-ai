# AI Weekly Migration: Supabase → Anthropic Memory Stores + Credential Vault

**Status:** ✅ Phase 1 Complete | 🔄 Phase 2 In Progress | ✅ Phase 3 Ready to Deploy

**Alignment:** Follows Anthropic's "Scaling Managed Agents: Decoupling the brain from the hands" (Apr 2026).

---

## Summary of Changes

### Phase 1: Memory Stores Migration ✅

**What changed:**
1. **Removed Supabase dependency**
   - Deleted `from supabase import create_client, Client` import
   - Removed SUPABASE_URL and SUPABASE_KEY initialization
   - `.env` no longer requires SUPABASE_* variables

2. **Migrated to Anthropic Memory Stores (JSONL)**
   - Events now stored at `/mnt/memory/session_{session_id}.jsonl` (append-only)
   - Replaced Supabase queries with JSONL file operations
   - Fallback to `./memory_local/` directory for local testing

3. **Refactored custom tools**
   - `handle_emit_event()` now appends to JSONL file + returns event_id
   - `handle_get_events()` now reads JSONL file with filtering
   - Same tool interface (unchanged for agents); different backend
   - Added error handling for file I/O

4. **Updated orchestrator state tracking**
   - `_has_event()` and `_count_events()` unchanged (call handle_get_events internally)
   - Automatically work with new JSONL backend

**Files modified:**
- ✅ `orchestrator_v2.py` (lines 1-30, 47-130)
- ✅ `ARCHITECTURE.md` (section "3 · Shared session log")
- ✅ `README.md` (env vars section)
- ✅ `.gitignore` (added `memory_local/`)

**Files created:**
- ✅ `credentials.py` (credential manager with vault support)

**Testing:**
- ✅ Unit tests pass: emit_event, get_events, filtering by agent_name and event_type

---

### Phase 2: Credential Vault Integration 🔄

**What's done:**
1. **Created `credentials.py`** — Centralized credential manager
   - Fallback chain: Anthropic vault → environment variables → defaults
   - `CredentialManager.get_credential()` retrieves from multiple sources
   - `get_smtp_credentials()` returns validated SMTP config
   - Placeholder for Anthropic vault API (waiting on API availability)

2. **Updated `orchestrator_v2.py`**
   - `handle_send_email_smtp()` now uses credential manager
   - Imports `get_credential_manager()`
   - Graceful error handling for missing credentials

**What's pending:**
1. **Register credentials in Anthropic Managed Credentials vault**
   - Once vault API is available in Claude Console, register:
     - `SMTP_USER`
     - `SMTP_PASSWORD`
     - (Optional: `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`)

2. **Test credential retrieval**
   - Run orchestrator and confirm SMTP credentials are retrieved correctly
   - Verify email sends without `.env` SMTP_PASSWORD

3. **Remove SMTP credentials from `.env`**
   - `.env` should contain non-secret config only (APP_BASE_URL, RECIPIENT_EMAILS)
   - Secrets move to vault

**Files involved:**
- 🔄 `orchestrator_v2.py` (handle_send_email_smtp updated)
- 🔄 `credentials.py` (placeholder for vault integration)

---

### Phase 3: Orchestrator Hosting (Vercel Crons) ✅ READY

**What's done:**
1. **Created `api/orchestrate.py`** — Vercel serverless function
   - Wraps OrchestratorV2, runs fresh newsletter generation
   - Returns JSON response with session_id and status
   - Error handling for missing environment variables
   - Can be tested locally: `python3 api/orchestrate.py`

2. **Created `vercel.json`** — Cron configuration
   - Schedule: `0 9 * * 4` (Thursday 9am UTC)
   - Can be customized (see cron format below)
   - Env vars defined with `@` secrets syntax

**How to deploy:**
1. Push changes to GitHub (if using GitHub integration)
2. Or: `vercel deploy` from CLI
3. Once deployed, set environment variables in Vercel Dashboard:
   - Settings → Environment Variables
   - Add: ANTHROPIC_API_KEY, SMTP_USER, SMTP_PASSWORD, RECIPIENT_EMAILS, APP_BASE_URL
4. Verify cron is enabled: Vercel Dashboard → Crons → Check `/api/orchestrate` status

**Cron schedule format** (POSIX):
```
0 9 * * 1
│ │ │ │ │
│ │ │ │ └─ Day of week (0=Sunday, 1=Monday, ..., 6=Saturday)
│ │ │ └─── Month (1-12)
│ │ └───── Day of month (1-31)
│ └─────── Hour (0-23, UTC)
└───────── Minute (0-59)

Examples:
  0 9 * * 1     → Every Monday at 9am UTC
  0 9 * * 1-5   → Weekdays at 9am UTC
  0 0 1 * *     → First day of month at midnight UTC
  0 * * * *     → Every hour on the hour
```

**To change the schedule:**
Edit `vercel.json`, change the `schedule` value, then redeploy.

---

## How to Test Locally

### Test 1: Fresh Run with Memory Stores

```bash
cd /Users/pranamyavadlamani/Desktop/agent_app
python3 orchestrator_v2.py
```

**Expected behavior:**
- Creates `memory_local/session_newsletter_YYYYMMDD_HHMMSS_xxxxxxxx.jsonl`
- Agents emit events to JSONL file
- Orchestrator reads events and determines next steps
- Resume works: `python3 orchestrator_v2.py --session-id <id>`

### Test 2: Verify JSONL Structure

```bash
cat memory_local/session_*.jsonl | jq '.'
```

**Expected:** Each line is a valid JSON object with fields:
- `id`, `session_id`, `agent_name`, `event_type`, `data`, `created_at`

### Test 3: Test Credential Manager

```bash
python3 << 'EOF'
from credentials import get_credential_manager

mgr = get_credential_manager()
print(mgr.get_smtp_credentials())
print(f"Valid: {mgr.validate_smtp_credentials()}")
EOF
```

---

## Environment Variables (Updated)

**Required:**
```
ANTHROPIC_API_KEY=...
SMTP_USER=...
SMTP_PASSWORD=...             # or retrieve from vault when available
RECIPIENT_EMAILS=a@x.com,b@y.com
APP_BASE_URL=https://...
```

**Optional:**
```
SMTP_HOST=smtp.gmail.com      # defaults to this
SMTP_PORT=587                 # defaults to this
SMTP_FROM=...                 # defaults to SMTP_USER
LOCAL_MEMORY_DIR=./memory_local  # for testing; /mnt/memory in Managed Agents
```

**Removed (no longer needed):**
```
SUPABASE_URL
SUPABASE_ANON_KEY
NEXT_PUBLIC_SUPABASE_URL
ANON_KEY
```

---

## Next Steps

### Immediate (This Week)
1. ✅ Test Phase 1 changes locally: `python3 orchestrator_v2.py`
2. ✅ Verify JSONL files are created in `memory_local/`
3. ✅ Verify resume works: `python3 orchestrator_v2.py --session-id <id>`
4. Run a full test with real agents (if available)

### Phase 2 (Next Week)
1. Wait for Anthropic Managed Credentials vault API to be available
2. Once available:
   - Register SMTP credentials in vault (via Claude Console)
   - Update `credentials.py` with vault API implementation
   - Test credential retrieval without `.env`
3. Remove SMTP credentials from `.env`

### Phase 3 (Later)
1. Decide on orchestrator hosting platform
2. Move `orchestrator_v2.py` to serverless (Vercel / Lambda)
3. Set up cron trigger for weekly runs
4. Update deployment docs

---

## Architecture Improvements Realized

| Aspect | Before | After |
|--------|--------|-------|
| **Session State** | Supabase (external DB) | Anthropic Memory Stores (workspace-scoped) |
| **Credentials** | `.env` file (less secure) | Credential vault (TBD: Anthropic or AWS) |
| **Dependencies** | Supabase SDK + Python package | None (built-in to Managed Agents) |
| **Decoupling** | Some coupling to Supabase | Full decoupling: brain (harness) separate from hands (sandbox) |
| **Cost** | Supabase free tier | Included in Managed Agents (no extra cost) |
| **Audit Trail** | Manual query of session_events | Built-in: JSONL append-only history |

---

## References

- **Anthropic Article:** [Scaling Managed Agents: Decoupling the brain from the hands](https://www.anthropic.com/engineering/scaling-managed-agents) (Apr 2026)
- **Session-as-State Pattern:** Event sourcing paradigm applied to multi-agent coordination
- **Memory Stores Docs:** TBD (once public docs available)
- **Credential Vault:** Awaiting Anthropic API announcement

---

## Issues & Limitations

1. **Orchestrator can't read `/mnt/memory/` directly** (runs on local machine)
   - **Solution:** JSONL files stored locally in `./memory_local/` for now
   - **Future:** Orchestrator runs as Managed Agent itself, has direct `/mnt/memory/` access

2. **Anthropic credential vault API not yet available**
   - **Workaround:** `credentials.py` falls back to environment variables
   - **Timeline:** Once announced, implement `_get_from_anthropic_vault()`

3. **Cross-session memory (`covered_topics.md`)**
   - **Status:** Agents should write to `/mnt/memory/covered_topics.md`
   - **Not yet implemented:** Waiting for agent prompt updates to include file I/O

---

## Questions & Discussion

**Q: Why not just use Memory Stores directly in agents?**
A: We are. Agents write to `/mnt/memory/`. The custom tools are just thin wrappers that confirm receipt.

**Q: When should I migrate to Vercel/Lambda?**
A: After Phase 1 is stable (local testing works). Hosting is orthogonal to Memory Stores + credentials.

**Q: Can I use AWS Secrets Manager instead of Anthropic vault?**
A: Yes! Update `credentials.py` to check AWS first, or wait for Anthropic vault.

