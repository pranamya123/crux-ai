"""
Vercel Serverless Function: Newsletter Orchestrator Trigger

Triggered by Vercel Crons every Thursday 9am UTC.
Starts a fresh newsletter session, then triggers the first step.

Why split into steps?
- Vercel function timeout (10s/60s/900s depending on plan)
- Full pipeline can take 5-15 min
- Solution: each step runs in its own short function call
- This function returns immediately after triggering step 1
"""

import json
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import urllib.request

from orchestrator_v2 import OrchestratorV2, STEP_ORDER
from observability import StructuredLogger


def handler(request):
    """Cron trigger: start a fresh newsletter run, kick off step 1."""
    base_url = os.getenv("APP_BASE_URL", "")

    # Verify required env vars
    required_vars = ["ANTHROPIC_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "RECIPIENT_EMAILS"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": "Missing required environment variables",
                "missing": missing,
            }),
        }

    try:
        # Create session (does not run any agent yet — just initializes)
        orch = OrchestratorV2(resume_session_id=None)
        session_id = orch.session_id
        logger = StructuredLogger(session_id=session_id, agent_name="cron_trigger")

        logger.info(
            "newsletter_run_started",
            trigger="vercel_cron",
            first_step=STEP_ORDER[0],
        )

        # Fire-and-forget: trigger step 1 without waiting
        # (in production, use a queue like QStash, AWS SQS, or background fn)
        first_step = STEP_ORDER[0]
        if base_url:
            try:
                step_url = f"{base_url}/api/step?session_id={session_id}&step={first_step}"
                # Use timeout=1 — we want fire-and-forget
                req = urllib.request.Request(step_url, method="GET")
                urllib.request.urlopen(req, timeout=2)
            except Exception as e:
                # Don't fail — step can be triggered manually if needed
                logger.warn(f"could_not_trigger_step_async", error_type=type(e).__name__, error_message=str(e))

        return {
            "statusCode": 200,
            "body": json.dumps({
                "success": True,
                "session_id": session_id,
                "first_step": first_step,
                "message": f"Newsletter run started. Triggered {first_step} step asynchronously.",
                "monitor_url": f"{base_url}/api/status?session_id={session_id}" if base_url else None,
            }),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "error": f"{type(e).__name__}: {str(e)}",
            }),
        }


if __name__ == "__main__":
    print(json.dumps(handler(None), indent=2))
