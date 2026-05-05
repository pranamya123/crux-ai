"""
Subscribers Hand: fetch subscriber list from Supabase.

Independent tool for retrieving the list of email recipients.
Falls back to RECIPIENT_EMAILS env var if Supabase unavailable.

Usage:
    from tools.subscribers import get_subscribers
    emails = get_subscribers()  # Returns list of email strings
"""

import os
from typing import List, Optional

from retry import retry_with_backoff


def _get_from_env() -> List[str]:
    """Fallback: read recipient emails from RECIPIENT_EMAILS env var."""
    recipients_env = os.environ.get("RECIPIENT_EMAILS", "")
    return [r.strip() for r in recipients_env.split(",") if r.strip()]


@retry_with_backoff(max_attempts=2, initial_delay=1.0)
def _fetch_from_supabase() -> Optional[List[str]]:
    """Fetch subscriber emails from Supabase. Returns None on failure."""
    supabase_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("ANON_KEY")

    if not supabase_url or not supabase_key:
        return None

    try:
        from supabase import create_client
        client = create_client(supabase_url, supabase_key)
        response = client.table("subscribers").select("email").execute()
        emails = [row["email"] for row in response.data if row.get("email")]
        return emails
    except Exception as e:
        print(f"[subscribers] Supabase fetch failed: {type(e).__name__}: {e}", flush=True)
        return None


def get_subscribers() -> List[str]:
    """
    Get the list of subscriber emails.

    Order of resolution:
    1. Try Supabase 'subscribers' table (single source of truth)
    2. Fall back to RECIPIENT_EMAILS env var
    3. Return empty list if both fail

    Returns: list of email strings (deduplicated, lowercased)
    """
    # Try Supabase first
    try:
        emails = _fetch_from_supabase()
        if emails:
            print(f"[subscribers] Loaded {len(emails)} subscribers from Supabase", flush=True)
            return _normalize(emails)
    except Exception as e:
        print(f"[subscribers] Supabase unavailable, falling back to env: {e}", flush=True)

    # Fallback to env var
    emails = _get_from_env()
    if emails:
        print(f"[subscribers] Loaded {len(emails)} subscribers from RECIPIENT_EMAILS env", flush=True)
    else:
        print(f"[subscribers] WARNING: No subscribers found", flush=True)
    return _normalize(emails)


def _normalize(emails: List[str]) -> List[str]:
    """Lowercase, strip, dedupe."""
    seen = set()
    result = []
    for e in emails:
        clean = e.strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
