"""
Observability for AI Weekly: Structured logging, metrics, and run tracking.

Goals:
- JSON structured logs for machine parsing
- Per-agent timing and token usage
- Run history tracking
- Health check support
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))


class StructuredLogger:
    """JSON-structured logger with session correlation."""

    def __init__(self, session_id: Optional[str] = None, agent_name: Optional[str] = None):
        self.session_id = session_id
        self.agent_name = agent_name

    def _emit(self, level: str, message: str, **kwargs):
        """Emit a structured log line."""
        record = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": level,
            "session_id": self.session_id,
            "agent": self.agent_name,
            "message": message,
            **kwargs,
        }
        # Print as JSON to stdout (Vercel captures stdout)
        print(json.dumps(record), flush=True)

        # Also persist to local logs directory
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_file = LOG_DIR / f"session_{self.session_id or 'unknown'}.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass  # Don't crash on log write failure

    def info(self, message: str, **kwargs):
        self._emit("INFO", message, **kwargs)

    def warn(self, message: str, **kwargs):
        self._emit("WARN", message, **kwargs)

    def error(self, message: str, error: Optional[Exception] = None, **kwargs):
        if error:
            kwargs["error_type"] = type(error).__name__
            kwargs["error_message"] = str(error)
            kwargs["traceback"] = traceback.format_exc()
        self._emit("ERROR", message, **kwargs)

    def metric(self, name: str, value: float, **kwargs):
        """Emit a metric data point."""
        self._emit("METRIC", f"metric:{name}", metric_name=name, metric_value=value, **kwargs)


class RunTracker:
    """Track run-level metrics: timing, token usage, success/failure per agent."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.run_start = time.time()
        self.agent_timings: Dict[str, float] = {}
        self.agent_tokens: Dict[str, Dict[str, int]] = {}
        self.agent_status: Dict[str, str] = {}  # "success", "failed", "skipped"
        self.errors: list = []

    def start_agent(self, agent_name: str):
        self.agent_timings[agent_name] = time.time()

    def end_agent(self, agent_name: str, status: str = "success",
                  input_tokens: int = 0, output_tokens: int = 0,
                  cache_read_tokens: int = 0, error: Optional[str] = None):
        elapsed = time.time() - self.agent_timings.get(agent_name, time.time())
        self.agent_timings[agent_name] = elapsed
        self.agent_tokens[agent_name] = {
            "input": input_tokens,
            "output": output_tokens,
            "cache_read": cache_read_tokens,
        }
        self.agent_status[agent_name] = status
        if error:
            self.errors.append({"agent": agent_name, "error": error})

    def total_elapsed(self) -> float:
        return time.time() - self.run_start

    def summary(self) -> Dict[str, Any]:
        total_input = sum(t.get("input", 0) for t in self.agent_tokens.values())
        total_output = sum(t.get("output", 0) for t in self.agent_tokens.values())
        total_cache = sum(t.get("cache_read", 0) for t in self.agent_tokens.values())
        return {
            "session_id": self.session_id,
            "total_elapsed_sec": round(self.total_elapsed(), 2),
            "agent_timings": {k: round(v, 2) for k, v in self.agent_timings.items()},
            "agent_status": self.agent_status,
            "agent_tokens": self.agent_tokens,
            "totals": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cache_read_tokens": total_cache,
            },
            "errors": self.errors,
            "success": all(s == "success" for s in self.agent_status.values()),
        }

    def persist(self):
        """Save run summary to disk for /admin/runs endpoint."""
        try:
            runs_dir = Path(os.getenv("RUNS_DIR", "./runs"))
            runs_dir.mkdir(parents=True, exist_ok=True)
            summary = self.summary()
            summary["completed_at"] = datetime.utcnow().isoformat() + "Z"
            run_file = runs_dir / f"{self.session_id}.json"
            with open(run_file, "w") as f:
                json.dump(summary, f, indent=2)
        except Exception:
            pass


def get_recent_runs(limit: int = 20) -> list:
    """Read recent run summaries for /admin/runs endpoint."""
    runs_dir = Path(os.getenv("RUNS_DIR", "./runs"))
    if not runs_dir.exists():
        return []
    files = sorted(runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    runs = []
    for f in files[:limit]:
        try:
            with open(f) as fh:
                runs.append(json.load(fh))
        except Exception:
            continue
    return runs


def get_run_status(session_id: str) -> Optional[Dict[str, Any]]:
    """Get status of a specific run by session_id."""
    runs_dir = Path(os.getenv("RUNS_DIR", "./runs"))
    run_file = runs_dir / f"{session_id}.json"
    if not run_file.exists():
        return None
    try:
        with open(run_file) as f:
            return json.load(f)
    except Exception:
        return None
