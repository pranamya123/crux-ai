"""
Vercel Serverless Function: Newsletter Orchestrator

Triggered by Vercel Crons on a schedule: Every Thursday at 9am UTC.
Runs the multi-agent orchestrator to generate and send the newsletter.

Environment Variables Required:
- ANTHROPIC_API_KEY
- SMTP_USER
- SMTP_PASSWORD (or from Anthropic vault)
- RECIPIENT_EMAILS
- APP_BASE_URL

See vercel.json for cron schedule configuration.
"""

import json
import os
import sys
from pathlib import Path

# Add parent directory to path so we can import orchestrator_v2
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator_v2 import OrchestratorV2


def handler(request):
    """
    Vercel serverless function handler.

    Called by Vercel Crons on schedule. Runs a fresh newsletter generation.

    Returns:
        JSON response with session_id, status, and summary
    """

    # Verify required environment variables
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
        print("[orchestrate] Starting newsletter generation...", flush=True)

        # Initialize and run orchestrator (fresh session, no resume)
        orchestrator = OrchestratorV2(resume_session_id=None)
        session_id = orchestrator.session_id

        # Run the full pipeline
        orchestrator.orchestrate()

        print(f"[orchestrate] Session {session_id} completed successfully", flush=True)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "success": True,
                "session_id": session_id,
                "message": "Newsletter generated and sent successfully",
            }),
        }

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"[orchestrate] ERROR: {error_msg}", flush=True)

        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "error": error_msg,
                "message": "Failed to generate newsletter",
            }),
        }


# For local testing (e.g., `python3 api/orchestrate.py`)
if __name__ == "__main__":
    result = handler(None)
    print(json.dumps(result, indent=2))
