#!/usr/bin/env python3
"""POST a local markdown file to Beehiiv as a draft post.

Usage:  python3 publish_only.py path/to/draft.md \
            --title "AI Weekly: April 23-30, 2026" \
            [--subtitle "..."] [--preview "..."] [--confirmed]

Env: BEEHIIV_API_KEY, BEEHIIV_PUBLICATION_ID
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

import markdown as md

BEEHIIV_API = "https://api.beehiiv.com/v2"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("markdown_file", help="Path to a markdown file to publish.")
    p.add_argument("--title", required=True, help="Post title.")
    p.add_argument("--subtitle", default=None)
    p.add_argument("--preview", default=None, help="Email preview text.")
    p.add_argument("--confirmed", action="store_true",
                   help="Publish immediately and email subscribers (default: draft).")
    args = p.parse_args()

    api_key = os.environ.get("BEEHIIV_API_KEY")
    pub_id = os.environ.get("BEEHIIV_PUBLICATION_ID")
    if not api_key or not pub_id:
        print("ERROR: BEEHIIV_API_KEY or BEEHIIV_PUBLICATION_ID not set", file=sys.stderr)
        return 1
    if not pub_id.startswith("pub_"):
        print(f"ERROR: BEEHIIV_PUBLICATION_ID must start with 'pub_' (got: {pub_id!r})",
              file=sys.stderr)
        return 1

    with open(args.markdown_file) as f:
        body_md = f.read()

    body_html = md.markdown(body_md, extensions=["extra", "sane_lists", "smarty"])

    payload = {
        "title": args.title,
        "body_content": body_html,
        "status": "confirmed" if args.confirmed else "draft",
    }
    if args.subtitle:
        payload["subtitle"] = args.subtitle
    if args.preview:
        payload["email_settings"] = {"preview_text": args.preview}

    req = urllib.request.Request(
        f"{BEEHIIV_API}/publications/{pub_id}/posts",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        post_id = data.get("data", {}).get("id")
        print(f"OK status={payload['status']} post_id={post_id}")
        return 0
    except urllib.error.HTTPError as e:
        print(f"FAIL {e.code} {e.read().decode(errors='replace')[:500]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FAIL {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
