"""
Verifier Hand: verify_links.

Verifies that every URL in a markdown document actually exists.
Used by the Verifier agent to catch hallucinated URLs and fake paper citations
before the newsletter ships.

Two checks:
- HTTP HEAD request (with GET fallback for servers that reject HEAD)
- For arXiv URLs: query the arXiv API to confirm the paper exists

Costs: zero. All HTTP calls; no Claude tokens used by this module.
"""

import re
import urllib.error
import urllib.request
from typing import Any, Dict

from retry import retry_with_backoff

# Match Markdown links: [text](url)
URL_PATTERN = re.compile(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)')

# Match arXiv IDs in URLs: arxiv.org/abs/2401.12345 or arxiv.org/abs/2401.12345v2
ARXIV_PATTERN = re.compile(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,6})(?:v\d+)?')

USER_AGENT = "Mozilla/5.0 (compatible; AIWeeklyVerifier/1.0)"
DEFAULT_TIMEOUT = 8


def _check_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    HEAD request to verify URL exists. Falls back to GET if server rejects HEAD.
    Returns: {"ok": bool, "status": int|None, "reason": str}
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.status
            return {"ok": 200 <= status < 400, "status": status, "reason": "OK"}
    except urllib.error.HTTPError as e:
        # Some servers reject HEAD (405); try GET
        if e.code in (405, 403):
            try:
                req = urllib.request.Request(url, method="GET", headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    status = response.status
                    return {"ok": 200 <= status < 400, "status": status, "reason": "OK (via GET)"}
            except Exception as e2:
                return {"ok": False, "status": None, "reason": f"GET fallback failed: {type(e2).__name__}"}
        return {"ok": False, "status": e.code, "reason": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "status": None, "reason": f"URL error: {e.reason}"}
    except TimeoutError:
        return {"ok": False, "status": None, "reason": "Timeout"}
    except Exception as e:
        return {"ok": False, "status": None, "reason": f"{type(e).__name__}: {e}"}


def _check_arxiv(arxiv_id: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Verify arXiv paper exists via the arXiv API."""
    api_url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="ignore")
            # arXiv returns <entry>...</entry> blocks for found papers.
            # An ID query for a missing paper returns 0 entries.
            has_entry = "<entry>" in content and "<id>" in content
            return {"ok": has_entry, "status": 200, "reason": "Found" if has_entry else "arXiv ID not found"}
    except Exception as e:
        return {"ok": False, "status": None, "reason": f"arXiv API error: {type(e).__name__}: {e}"}


@retry_with_backoff(max_attempts=2, initial_delay=1.0)
def handle_verify_links(args: dict) -> Dict[str, Any]:
    """
    Extract every Markdown link from the input and verify it exists.

    Args:
        args: {"markdown": str}

    Returns:
        {
          "ok": bool,
          "all_valid": bool,
          "checked": int,
          "valid": [{"url": str, "status": int}, ...],
          "invalid": [{"url": str, "reason": str}, ...]
        }
    """
    markdown = args.get("markdown", "")
    if not markdown:
        return {"ok": False, "error": "No markdown provided"}

    matches = URL_PATTERN.findall(markdown)
    if not matches:
        return {
            "ok": True,
            "all_valid": True,
            "checked": 0,
            "valid": [],
            "invalid": [],
            "note": "No URLs found in document",
        }

    valid = []
    invalid = []
    seen = set()

    for _link_text, url in matches:
        # Normalize: strip trailing punctuation that often gets pulled into the URL
        url = url.rstrip(".,;:!?)\"'")
        if url in seen:
            continue
        seen.add(url)

        # Special handling for arXiv links — use the API instead of HEAD
        arxiv_match = ARXIV_PATTERN.search(url)
        if arxiv_match:
            arxiv_id = arxiv_match.group(1)
            result = _check_arxiv(arxiv_id)
        else:
            result = _check_url(url)

        if result["ok"]:
            valid.append({"url": url, "status": result.get("status")})
        else:
            invalid.append({"url": url, "reason": result["reason"]})

    return {
        "ok": True,
        "all_valid": len(invalid) == 0,
        "checked": len(seen),
        "valid": valid,
        "invalid": invalid,
    }
