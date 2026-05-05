"""
Vercel Serverless Function: Run Status Endpoint.

Returns status of a specific session, or recent runs.

URLs:
- /api/status?session_id=<id>  → get specific run status
- /api/status?recent=true      → get recent runs (last 20)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from observability import get_recent_runs, get_run_status
from tools.memory_store import handle_get_events


def handler(request):
    """Status endpoint for monitoring runs."""
    query = {}
    if hasattr(request, "args"):
        query = dict(request.args)
    elif hasattr(request, "query"):
        query = dict(request.query)
    elif isinstance(request, dict):
        query = request.get("query", request.get("queryStringParameters", {})) or {}

    session_id = query.get("session_id")
    recent = query.get("recent", "").lower() in ("true", "1", "yes")

    if recent:
        runs = get_recent_runs(limit=20)
        return {
            "statusCode": 200,
            "body": json.dumps({
                "runs": runs,
                "count": len(runs),
            }, default=str),
        }

    if not session_id:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Missing query param: session_id (or use ?recent=true for recent runs)",
            }),
        }

    # Get run summary if completed
    run_summary = get_run_status(session_id)

    # Get event log for current state (even mid-run)
    events_result = handle_get_events(session_id=session_id)
    events = events_result.get("events", [])

    # Build state map
    event_types = [e.get("event_type") for e in events]
    state = {
        "memory_complete": "covered_topics" in event_types,
        "launches_complete": "launches_researched" in event_types,
        "papers_complete": "papers_researched" in event_types,
        "evaluation_complete": "items_evaluated" in event_types,
        "draft_approved": "draft_approved" in event_types,
        "email_sent": "email_sent" in event_types,
        "rejection_count": event_types.count("critic_rejection"),
        "draft_count": event_types.count("draft_written"),
    }

    response = {
        "session_id": session_id,
        "completed": state["email_sent"],
        "state": state,
        "events_count": len(events),
        "event_types": event_types,
    }

    if run_summary:
        response["summary"] = run_summary

    return {
        "statusCode": 200,
        "body": json.dumps(response, default=str),
    }


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        if sys.argv[1] == "recent":
            result = handler({"query": {"recent": "true"}})
        else:
            result = handler({"query": {"session_id": sys.argv[1]}})
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python3 api/status.py <session_id> | recent")
