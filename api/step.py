"""
Vercel Serverless Function: Run a single orchestration step.

Each step runs in its own function invocation, avoiding Vercel timeouts.
After completion, triggers the next step.

URL: /api/step?session_id=<id>&step=<step_name>

Steps (in order):
1. memory          → emits covered_topics
2. research        → parallel: launches_researched + papers_researched
3. evaluate        → emits items_evaluated
4. write_critique  → writer/critic loop, emits draft_approved
5. deliver         → emits email_sent
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import urllib.request

from orchestrator_v2 import OrchestratorV2, STEP_ORDER
from observability import StructuredLogger


def _trigger_next_step(base_url: str, session_id: str, next_step: str, logger: StructuredLogger):
    """Fire-and-forget HTTP call to trigger the next step."""
    if not base_url or not next_step:
        return
    try:
        url = f"{base_url}/api/step?session_id={session_id}&step={next_step}"
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=2)
        logger.info(f"next_step_triggered", next_step=next_step, url=url)
    except Exception as e:
        logger.warn(
            f"could_not_trigger_next_step",
            next_step=next_step,
            error_type=type(e).__name__,
            error_message=str(e),
        )


def handler(request):
    """Run one orchestration step."""
    # Parse query params (Vercel passes via request)
    query = {}
    if hasattr(request, "args"):
        query = dict(request.args)
    elif hasattr(request, "query"):
        query = dict(request.query)
    elif isinstance(request, dict):
        query = request.get("query", request.get("queryStringParameters", {})) or {}

    session_id = query.get("session_id")
    step_name = query.get("step")

    if not session_id or not step_name:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Missing required query params: session_id, step",
            }),
        }

    if step_name not in STEP_ORDER:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": f"Invalid step: {step_name}. Must be one of {STEP_ORDER}",
            }),
        }

    base_url = os.getenv("APP_BASE_URL", "")
    logger = StructuredLogger(session_id=session_id, agent_name=f"step_{step_name}")

    try:
        orch = OrchestratorV2(resume_session_id=session_id)
        result = orch.run_step(step_name)

        # If this step completed and there's a next step, trigger it
        if result.get("completed") and result.get("next_step"):
            _trigger_next_step(base_url, session_id, result["next_step"], logger)
        elif result.get("completed") and not result.get("next_step"):
            logger.info("pipeline_complete", session_id=session_id)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "session_id": session_id,
                "step": step_name,
                "completed": result.get("completed"),
                "next_step": result.get("next_step"),
                "monitor_url": f"{base_url}/api/status?session_id={session_id}" if base_url else None,
            }, default=str),
        }

    except Exception as e:
        logger.error(f"step_handler_failed: {step_name}", error=e)
        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "session_id": session_id,
                "step": step_name,
                "error": f"{type(e).__name__}: {str(e)}",
            }),
        }


if __name__ == "__main__":
    # Local testing: python3 api/step.py session_id step_name
    if len(sys.argv) >= 3:
        result = handler({"query": {"session_id": sys.argv[1], "step": sys.argv[2]}})
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python3 api/step.py <session_id> <step_name>")
