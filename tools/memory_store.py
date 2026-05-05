"""
Memory Store Hand: emit_event, get_events.

Independent tool for accessing the shared session log (Anthropic Memory Stores).
Can fail/be replaced independently of other tools.

Storage:
- Production: /mnt/memory/session_{id}.jsonl (Anthropic Memory Stores)
- Local: ./memory_local/session_{id}.jsonl (fallback for testing)
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from retry import retry_with_backoff

MEMORY_DIR = Path("/mnt/memory")
LOCAL_MEMORY_DIR = Path(os.getenv("LOCAL_MEMORY_DIR", "./memory_local"))


def _get_session_file(session_id: str) -> Path:
    """Resolve the JSONL file path for a session, preferring /mnt/memory if available."""
    if MEMORY_DIR.exists():
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            return MEMORY_DIR / f"session_{session_id}.jsonl"
        except (OSError, PermissionError):
            pass
    LOCAL_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_MEMORY_DIR / f"session_{session_id}.jsonl"


@retry_with_backoff(max_attempts=3, initial_delay=0.5)
def handle_emit_event(session_id: str, agent_name: str, event_type: str, data: dict) -> Dict[str, Any]:
    """
    Append an event to the session JSONL file.

    Returns: {"ok": True, "event_id": uuid} or {"ok": False, "error": str}
    """
    try:
        event_id = str(uuid.uuid4())
        event = {
            "id": event_id,
            "session_id": session_id,
            "agent_name": agent_name,
            "event_type": event_type,
            "data": data,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        memory_file = _get_session_file(session_id)
        with open(memory_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        return {"ok": True, "event_id": event_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@retry_with_backoff(max_attempts=3, initial_delay=0.5)
def handle_get_events(
    session_id: str,
    agent_name: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Read events from the session JSONL file with optional filtering.

    Args:
        session_id: Required - filter to specific session
        agent_name: Optional - filter by agent
        event_type: Optional - filter by event type
        limit: Optional - return only the last N matching events

    Returns: {"ok": True, "events": [...]} or {"ok": False, "error": str}
    """
    try:
        events = []
        # Try /mnt/memory first, then fallback
        candidates = []
        if MEMORY_DIR.exists():
            candidates.append(MEMORY_DIR / f"session_{session_id}.jsonl")
        candidates.append(LOCAL_MEMORY_DIR / f"session_{session_id}.jsonl")

        memory_file = next((p for p in candidates if p.exists()), None)
        if memory_file is None:
            return {"ok": True, "events": []}

        with open(memory_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if event.get("session_id") != session_id:
                        continue
                    if agent_name and event.get("agent_name") != agent_name:
                        continue
                    if event_type and event.get("event_type") != event_type:
                        continue
                    events.append(event)
                except json.JSONDecodeError:
                    continue

        events.sort(key=lambda e: e.get("created_at", ""))
        if limit:
            events = events[-limit:]
        return {"ok": True, "events": events}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def has_event(session_id: str, event_type: str) -> bool:
    """Helper: check if any event of given type exists for session."""
    result = handle_get_events(session_id=session_id, event_type=event_type)
    return result.get("ok", False) and len(result.get("events", [])) > 0


def count_events(session_id: str, event_type: str) -> int:
    """Helper: count events of a given type for session."""
    result = handle_get_events(session_id=session_id, event_type=event_type)
    return len(result.get("events", [])) if result.get("ok") else 0
