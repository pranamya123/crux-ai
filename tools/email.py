"""
Email Hand: send_email_smtp.

Independent tool for sending emails via SMTP.
Can fail/be replaced independently of other tools.

Side effects:
- Per-recipient HTML rendering with personalized unsubscribe links
- Snapshot save: latest_issue.{html,md,json} for /latest endpoint
"""

import json
import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

from credentials import get_credential_manager
from email_renderer import render_brief_email_html
from retry import retry_with_backoff
from tools.subscribers import get_subscribers


def _save_latest_issue(subject: str, body_markdown: str, body_html_public: str) -> None:
    """Persist the latest issue so /latest can serve it on the website."""
    try:
        out_dir = os.path.dirname(os.path.abspath(__file__))
        # Save in parent directory (agent_app/) not in tools/
        out_dir = os.path.dirname(out_dir)
        with open(os.path.join(out_dir, "latest_issue.html"), "w", encoding="utf-8") as f:
            f.write(body_html_public)
        with open(os.path.join(out_dir, "latest_issue.md"), "w", encoding="utf-8") as f:
            f.write(body_markdown)
        with open(os.path.join(out_dir, "latest_issue_meta.json"), "w", encoding="utf-8") as f:
            json.dump({
                "subject": subject,
                "sent_at": datetime.utcnow().isoformat() + "Z",
            }, f, indent=2)
        print(f"[issue] Saved latest issue: {subject}", flush=True)
    except Exception as e:
        print(f"[issue] Failed to save latest issue: {e}", flush=True)


@retry_with_backoff(max_attempts=2, initial_delay=2.0)
def handle_send_email_smtp(args: dict) -> Dict[str, Any]:
    """
    Send email via SMTP. Renders per-recipient HTML and saves snapshot.

    Args:
        args: {
            "subject": str,
            "body_markdown": str,
            "recipients": Optional[List[str]]  # falls back to RECIPIENT_EMAILS env
        }

    Returns: {"ok": bool, "recipients": [sent], "failures": [...], "subject": ...}
    """
    cred_mgr = get_credential_manager()
    try:
        creds = cred_mgr.get_smtp_credentials()
        host = creds["host"]
        port = int(creds["port"])
        user = creds["user"]
        password = creds["password"]
        from_addr = creds.get("from_addr") or user
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": f"SMTP credential error: {e}"}

    base_url = os.environ.get("APP_BASE_URL", "http://localhost:5000")

    if not user or not password:
        return {"ok": False, "error": "SMTP_USER or SMTP_PASSWORD not set"}

    # Resolve recipients with priority:
    # 1. Explicit args.recipients (if provided by agent)
    # 2. Supabase 'subscribers' table (live source of truth)
    # 3. RECIPIENT_EMAILS env var (fallback)
    recipients = args.get("recipients") or get_subscribers()
    if not recipients:
        return {"ok": False, "error": "No recipients (Supabase + env fallback both empty)"}

    subject = args["subject"]
    body_markdown = args["body_markdown"]

    # Save snapshot before sending
    public_html = render_brief_email_html(body_markdown)
    _save_latest_issue(subject, body_markdown, public_html)

    print(f"[SMTP] Sending to recipients: {recipients}", flush=True)
    sent: List[str] = []
    failures: List[Dict[str, str]] = []

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(user, password)
            for recipient in recipients:
                try:
                    body_html = render_brief_email_html(
                        body_markdown,
                        recipient_email=recipient,
                        base_url=base_url,
                    )
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"] = from_addr
                    msg["To"] = recipient
                    msg.attach(MIMEText(body_markdown, "plain", "utf-8"))
                    msg.attach(MIMEText(body_html, "html", "utf-8"))
                    server.sendmail(from_addr, [recipient], msg.as_string())
                    sent.append(recipient)
                except Exception as e:
                    failures.append({"recipient": recipient, "error": f"{type(e).__name__}: {e}"})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    print(f"[SMTP] Sent successfully to: {sent}", flush=True)
    if failures:
        print(f"[SMTP] Failed for: {failures}", flush=True)

    return {
        "ok": len(sent) > 0,
        "recipients": sent,
        "failures": failures,
        "subject": subject,
    }
