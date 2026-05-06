#!/usr/bin/env python3
"""
Quick post-run analyzer. Reads the latest run summary + session events
and prints the metrics most worth quoting in a writeup.

Usage:
    python3 analyze_run.py                  # uses most recent run
    python3 analyze_run.py <session_id>     # specific run
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).parent
RUNS_DIR = REPO / "runs"
LOGS_DIR = REPO / "logs"
MEMORY_DIR = REPO / "memory_local"


def latest_session_id() -> str:
    files = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        sys.exit("No runs found in runs/")
    return files[0].stem


def load_summary(session_id: str) -> dict:
    f = RUNS_DIR / f"{session_id}.json"
    if not f.exists():
        sys.exit(f"No summary for {session_id}")
    return json.loads(f.read_text())


def load_events(session_id: str) -> list:
    f = MEMORY_DIR / f"session_{session_id}.jsonl"
    if not f.exists():
        return []
    events = []
    for line in f.read_text().splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def main():
    session_id = sys.argv[1] if len(sys.argv) > 1 else latest_session_id()
    print(f"=== Run analysis: {session_id} ===\n")

    summary = load_summary(session_id)
    events = load_events(session_id)

    # --- Headlines ---
    print("WALL-CLOCK")
    total = summary.get("total_elapsed_sec", 0)
    print(f"  Total: {total:.1f}s ({total/60:.1f} min)")

    print("\nAGENT TIMING (sec)")
    for agent, secs in summary.get("agent_timings", {}).items():
        print(f"  {agent:25} {secs:6.1f}s")

    # --- Token usage ---
    print("\nTOKEN USAGE (per agent)")
    for agent, tokens in summary.get("agent_tokens", {}).items():
        inp = tokens.get("input", 0)
        out = tokens.get("output", 0)
        cache = tokens.get("cache_read", 0)
        cache_pct = (cache / (inp + cache)) * 100 if (inp + cache) > 0 else 0
        print(f"  {agent:25} input={inp:>7}  cache_read={cache:>7} ({cache_pct:.0f}%)  output={out:>5}")

    totals = summary.get("totals", {})
    total_input = totals.get("input_tokens", 0)
    total_cache = totals.get("cache_read_tokens", 0)
    total_output = totals.get("output_tokens", 0)
    overall_cache_pct = (total_cache / (total_input + total_cache)) * 100 if (total_input + total_cache) > 0 else 0
    print(f"\n  TOTAL                     input={total_input:>7}  cache_read={total_cache:>7} ({overall_cache_pct:.0f}%)  output={total_output:>5}")

    # --- Pipeline state ---
    print("\nEVENT TIMELINE")
    for e in events:
        agent = e.get("agent_name", "?")
        etype = e.get("event_type", "?")
        ts = e.get("created_at", "")
        marker = ""
        data = e.get("data", {})
        if data.get("auto_inserted"):
            marker = " [AUTO-INSERTED PLACEHOLDER]"
        print(f"  {ts}  {agent:25} {etype}{marker}")

    # --- New defenses: did they fire? ---
    print("\nNEW DEFENSES")

    # Citation rejections
    citation_rejections = sum(
        1 for e in events
        if e.get("event_type") == "critic_rejection"
        and "citation" in str(e.get("data", {})).lower()
    )
    total_rejections = sum(1 for e in events if e.get("event_type") == "critic_rejection")
    print(f"  Critic rejections: {total_rejections} (citation-related: {citation_rejections})")

    # Writer attempts
    drafts = sum(1 for e in events if e.get("event_type") == "draft_written")
    print(f"  Writer attempts: {drafts}")

    # Verifier outcomes
    verify_passed = sum(1 for e in events if e.get("event_type") == "verification_passed")
    verify_failed = sum(1 for e in events if e.get("event_type") == "verification_failed")
    print(f"  Verifier passed: {verify_passed}")
    print(f"  Verifier failed: {verify_failed}")

    # If verifier failed, show the bad URLs
    for e in events:
        if e.get("event_type") == "verification_failed":
            invalid = e.get("data", {}).get("invalid_urls", [])
            print(f"\n  Bad URLs caught by Verifier ({len(invalid)}):")
            for item in invalid:
                print(f"    - {item.get('url')} ({item.get('reason')})")

    # Silent failure placeholders
    placeholders = [e for e in events if e.get("data", {}).get("auto_inserted")]
    if placeholders:
        print(f"\n  Auto-inserted placeholders: {len(placeholders)}")
        for p in placeholders:
            print(f"    - {p.get('event_type')} (agent did not emit)")
    else:
        print(f"  Auto-inserted placeholders: 0 (no silent failures this run)")

    # --- Final status ---
    print(f"\nSTATUS: {'✓ SUCCESS' if summary.get('success') else '✗ FAILED'}")
    if summary.get("errors"):
        print("ERRORS:")
        for err in summary["errors"]:
            print(f"  {err}")


if __name__ == "__main__":
    main()
