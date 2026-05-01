#!/usr/bin/env python3
"""Driver for the AI Weekly Brief Bot.

Streams the agent's output and intercepts send_email_smtp tool calls,
rendering the brief into a NYT-style paginated email and delivering via SMTP
using credentials from the host's environment.

Resume an existing session with: python3 run.py --session-id sesn_... [message]

Env: ANTHROPIC_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
     SMTP_FROM, RECIPIENT_EMAILS (comma-separated)
"""
import argparse
import html as htmllib
import json
import os
import re
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import markdown as md_lib

AGENT_ID = "agent_011CaaLRjtDQefQZ9oUTwbGV"
ENVIRONMENT_ID = "env_01VeZNPz8LZMFQbR6LdPNV2x"

SERIF = "'Source Serif Pro', Georgia, 'Times New Roman', serif"
SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
INK = "#111111"
RULE = "#000000"
MUTED = "#6b6b6b"
HAIR = "#dddddd"
PAPER = "#ffffff"


def _md_inline(text: str) -> str:
    """Render simple inline markdown (links, bold, italic, code) safely for email."""
    rendered = md_lib.markdown(
        text, extensions=["extra", "sane_lists", "smarty"]
    ).strip()
    if rendered.startswith("<p>") and rendered.endswith("</p>"):
        rendered = rendered[3:-4]
    return _style_inline_html(rendered)


def _md_block(text: str) -> str:
    """Render block-level markdown to HTML and inject email-friendly inline styles."""
    rendered = md_lib.markdown(text, extensions=["extra", "sane_lists", "smarty"])
    return _style_inline_html(rendered)


def _style_inline_html(html: str) -> str:
    html = re.sub(r"<p>", f'<p style="margin:0 0 1.1em 0;">', html)
    html = re.sub(
        r"<a ",
        f'<a style="color:{INK};text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;" ',
        html,
    )
    html = re.sub(r"<strong>", '<strong style="font-weight:700;">', html)
    html = re.sub(r"<em>", '<em style="font-style:italic;">', html)
    html = re.sub(
        r"<code>",
        '<code style="font-family:Menlo,Consolas,monospace;background:#f4f1ea;padding:1px 5px;font-size:0.9em;border-radius:2px;">',
        html,
    )
    html = re.sub(r"<ul>", '<ul style="margin:0.5em 0 1em 1em;padding:0;">', html)
    html = re.sub(r"<ol>", '<ol style="margin:0.5em 0 1em 1em;padding:0;">', html)
    html = re.sub(r"<li>", '<li style="margin:0 0 0.5em 0;">', html)
    return html


def _parse_brief(md_text: str) -> dict:
    """Parse the brief markdown into structured sections.

    Handles v7 (two-section: company items + research findings) and remains
    tolerant of v6 (items that matter, worth your time later, ecosystem, cutting room floor).
    """
    out = {
        "title": "AI Weekly",
        "date": "",
        "lead": "",
        "company_items": [],
        "research_items": [],
        "worth_later": [],
        "ecosystem": [],
        "cutting_room": [],
        "build_idea": "",
    }

    title_match = re.match(r"^#\s+(.+?)\s*$", md_text, re.MULTILINE)
    if title_match:
        full_title = title_match.group(1).strip()
        date_match = re.match(r"^(.+?):\s*(.+)$", full_title)
        if date_match:
            out["title"] = date_match.group(1).strip()
            out["date"] = date_match.group(2).strip()
        else:
            out["title"] = full_title
        text_after = md_text[title_match.end():].lstrip()
    else:
        text_after = md_text

    parts = re.split(r"^##\s+(.+?)\s*$", text_after, flags=re.MULTILINE)
    out["lead"] = parts[0].strip()

    def parse_numbered(content: str) -> list:
        item_parts = re.split(r"^###\s+(\d+)\.\s+(.+?)\s*$", content, flags=re.MULTILINE)
        items = []
        for j in range(1, len(item_parts), 3):
            if j + 2 >= len(item_parts):
                break
            items.append({
                "number": int(item_parts[j]),
                "headline": item_parts[j + 1].strip(),
                "body_md": item_parts[j + 2].strip(),
            })
        return items

    for i in range(1, len(parts), 2):
        if i + 1 >= len(parts):
            break
        heading = parts[i].strip().lower()
        content = parts[i + 1].strip()

        if ("what shipped" in heading or "company items" in heading
                or "items that matter" in heading or "five that matter" in heading):
            out["company_items"] = parse_numbered(content)
        elif "worth knowing" in heading or "research findings" in heading or "research items" in heading:
            out["research_items"] = parse_numbered(content)
        elif "worth your time" in heading:
            out["worth_later"] = _parse_list(content)
        elif "ecosystem" in heading:
            out["ecosystem"] = _parse_list(content)
        elif "cutting room" in heading:
            out["cutting_room"] = _parse_list(content)
        elif "build" in heading:
            out["build_idea"] = content.strip()

    return out


def _parse_list(content: str) -> list:
    lines = []
    for line in content.split("\n"):
        s = line.strip()
        if s.startswith("- "):
            lines.append(s[2:].strip())
        elif s.startswith("* "):
            lines.append(s[2:].strip())
    return lines


def _label(text: str) -> str:
    return (
        f'<div style="font-family:{SANS};font-size:10px;letter-spacing:2.5px;'
        f'text-transform:uppercase;font-weight:700;color:{MUTED};">{htmllib.escape(text)}</div>'
    )


def _back_to_top() -> str:
    return (
        f'<div style="border-top:1px solid {RULE};padding-top:14px;margin-top:48px;'
        f'font-family:{SANS};font-size:11px;letter-spacing:1.5px;text-transform:uppercase;">'
        f'<a href="#top" style="color:{MUTED};text-decoration:none;">&uarr; Back to contents</a>'
        f"</div>"
    )


def _render_masthead(title: str, date: str) -> str:
    return (
        f'<header style="text-align:center;padding:8px 0 32px;border-top:3px solid {RULE};'
        f'border-bottom:1px solid {RULE};margin-bottom:40px;">'
        f'<div style="font-family:{SANS};font-size:11px;letter-spacing:4px;text-transform:uppercase;'
        f'font-weight:700;color:{MUTED};padding-top:24px;">{htmllib.escape(title)}</div>'
        f'<h1 style="font-family:{SERIF};font-size:34px;font-weight:400;letter-spacing:-0.5px;'
        f'margin:14px 0 18px;color:{INK};line-height:1.15;">{htmllib.escape(date)}</h1>'
        f'<div style="font-family:{SANS};font-size:10px;letter-spacing:2px;text-transform:uppercase;'
        f'color:{MUTED};">Vol. 1 · The Weekly Signal</div>'
        f"</header>"
    )


def _render_lead(lead: str) -> str:
    if not lead:
        return ""
    return (
        f'<div style="font-family:{SERIF};font-style:italic;font-size:18px;line-height:1.55;'
        f"color:{INK};text-align:center;max-width:520px;margin:0 auto 48px;"
        f'padding-bottom:32px;border-bottom:1px solid {HAIR};">'
        f'{_md_inline(lead)}</div>'
    )


def _toc_section_header(label: str) -> str:
    return (
        f'<tr><td style="padding:24px 0 6px;">'
        f'<div style="font-family:{SANS};font-size:10px;letter-spacing:2.5px;'
        f'text-transform:uppercase;font-weight:700;color:{MUTED};">{label}</div>'
        f'<div style="border-bottom:1px solid {RULE};margin-top:6px;"></div>'
        f"</td></tr>"
    )


def _toc_item_row(anchor: str, num_label: str, headline: str) -> str:
    return (
        f'<tr><td style="border-bottom:1px solid {HAIR};padding:14px 0;">'
        f'<a href="#{anchor}" style="color:{INK};text-decoration:none;display:block;">'
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">'
        f'<tr>'
        f'<td style="font-family:{SANS};font-size:10px;letter-spacing:2px;color:{MUTED};'
        f'width:60px;vertical-align:top;padding-top:6px;font-weight:700;">{num_label}</td>'
        f'<td style="font-family:{SERIF};font-size:18px;line-height:1.35;color:{INK};">'
        f"{htmllib.escape(headline)}</td>"
        f"</tr></table></a></td></tr>"
    )


def _toc_link_row(anchor: str, label: str) -> str:
    return (
        f'<tr><td style="border-bottom:1px solid {HAIR};padding:14px 0;">'
        f'<a href="#{anchor}" style="color:{INK};text-decoration:none;display:block;'
        f'font-family:{SERIF};font-size:18px;font-style:italic;">'
        f"{htmllib.escape(label)} &rarr;</a></td></tr>"
    )


def _render_toc(brief: dict) -> str:
    rows = []

    if brief["company_items"]:
        rows.append(_toc_section_header("What shipped"))
        for item in brief["company_items"]:
            rows.append(_toc_item_row(
                f"item-{item['number']}",
                f"No.{item['number']:02d}",
                item["headline"],
            ))

    if brief["research_items"]:
        rows.append(_toc_section_header("Worth knowing"))
        for item in brief["research_items"]:
            rows.append(_toc_item_row(
                f"research-{item['number']}",
                f"R.{item['number']:02d}",
                item["headline"],
            ))

    extras = []
    if brief["worth_later"]:
        extras.append(("worth-later", "Worth your time later"))
    if brief["ecosystem"]:
        extras.append(("ecosystem", "Ecosystem moves"))
    if brief["cutting_room"]:
        extras.append(("cutting-room", "Cutting room floor"))
    if brief["build_idea"]:
        extras.append(("build", "Your 4-hour build this week"))

    if extras:
        rows.append(_toc_section_header("Saturday morning"))
        for anchor, label in extras:
            rows.append(_toc_link_row(anchor, label))

    return (
        f'<nav id="top" style="margin:0 auto 64px;">'
        f'{_label("In this issue")}'
        f'<div style="border-bottom:2px solid {RULE};margin:8px 0 0;"></div>'
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse;">{"".join(rows)}</table>'
        f"</nav>"
    )


def _drop_cap_first(html: str) -> str:
    """Wrap the first character of the first <p> in a drop cap span."""
    pattern = re.compile(r'(<p style="[^"]*">)([A-Za-z])')
    cap_style = (
        f"float:left;font-family:{SERIF};font-size:62px;line-height:0.85;"
        f"padding:6px 8px 0 0;color:{INK};font-weight:700;"
    )
    replacement = rf'\1<span style="{cap_style}">\2</span>'
    return pattern.sub(replacement, html, count=1)


def _render_item(item: dict, date: str, *, anchor_prefix: str = "item",
                 section_label: str = "What shipped",
                 num_format: str = "No.{n:02d}") -> str:
    num_label = num_format.format(n=item["number"])
    body = _drop_cap_first(_md_block(item["body_md"]))
    return (
        f'<article id="{anchor_prefix}-{item["number"]}" '
        f'style="padding:64px 0 32px;border-top:3px solid {RULE};'
        f'page-break-before:always;-webkit-page-break-before:always;">'
        f'{_label(f"{section_label} · {num_label}")}'
        f'<h2 style="font-family:{SERIF};font-size:32px;line-height:1.18;font-weight:400;'
        f'letter-spacing:-0.5px;margin:14px 0 12px;color:{INK};">'
        f'{htmllib.escape(item["headline"])}</h2>'
        f'<div style="font-family:{SERIF};font-style:italic;font-size:13px;color:{MUTED};'
        f'margin-bottom:32px;">By the AI Weekly Bot · {htmllib.escape(date) if date else ""}</div>'
        f'<div style="font-family:{SERIF};font-size:18px;line-height:1.7;color:#1f1f1f;">'
        f"{body}"
        f"</div>"
        f"{_back_to_top()}"
        f"</article>"
    )


def _render_list_section(anchor: str, label: str, heading: str, items: list) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<li style="padding:14px 0;border-bottom:1px solid {HAIR};font-family:{SERIF};'
        f'font-size:16px;line-height:1.5;color:{INK};list-style:none;">'
        f"{_md_inline(it)}</li>"
        for it in items
    )
    return (
        f'<section id="{anchor}" '
        f'style="padding:64px 0 32px;border-top:3px solid {RULE};'
        f'page-break-before:always;-webkit-page-break-before:always;">'
        f'{_label(label)}'
        f'<h2 style="font-family:{SERIF};font-size:28px;font-weight:400;letter-spacing:-0.5px;'
        f'margin:14px 0 28px;color:{INK};">{htmllib.escape(heading)}</h2>'
        f'<ul style="list-style:none;padding:0;margin:0;">{rows}</ul>'
        f"{_back_to_top()}"
        f"</section>"
    )


def _render_build(brief: dict) -> str:
    if not brief["build_idea"]:
        return ""
    body = _md_block(brief["build_idea"])
    return (
        f'<section id="build" '
        f'style="padding:64px 0 48px;border-top:3px solid {RULE};'
        f'page-break-before:always;-webkit-page-break-before:always;">'
        f'{_label("Section · Saturday morning")}'
        f'<h2 style="font-family:{SERIF};font-size:28px;font-weight:400;letter-spacing:-0.5px;'
        f'margin:14px 0 28px;color:{INK};">Your 4-hour build this week</h2>'
        f'<div style="font-family:{SERIF};font-size:18px;line-height:1.7;color:#1f1f1f;">{body}</div>'
        f"{_back_to_top()}"
        f"</section>"
    )


def _render_footer(date: str) -> str:
    return (
        f'<footer style="border-top:1px solid {RULE};margin-top:64px;padding-top:24px;'
        f'text-align:center;font-family:{SANS};font-size:11px;letter-spacing:1.5px;'
        f'text-transform:uppercase;color:{MUTED};">'
        f"AI Weekly · {htmllib.escape(date) if date else ''} · End of issue"
        f"</footer>"
    )


def render_brief_email_html(body_markdown: str) -> str:
    brief = _parse_brief(body_markdown)
    title = brief["title"]
    date = brief["date"]

    body_html = (
        _render_masthead(title, date)
        + _render_lead(brief["lead"])
        + _render_toc(brief)
        + "".join(
            _render_item(it, date,
                         anchor_prefix="item",
                         section_label="What shipped",
                         num_format="No.{n:02d}")
            for it in brief["company_items"]
        )
        + "".join(
            _render_item(it, date,
                         anchor_prefix="research",
                         section_label="Worth knowing",
                         num_format="Research {n:02d}")
            for it in brief["research_items"]
        )
        + _render_list_section("worth-later", "Section · Briefly noted", "Worth your time later", brief["worth_later"])
        + _render_list_section("ecosystem", "Section · The business", "Ecosystem moves", brief["ecosystem"])
        + _render_list_section("cutting-room", "Section · Editorial notes", "Cutting room floor", brief["cutting_room"])
        + _render_build(brief)
        + _render_footer(date)
    )

    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{htmllib.escape(title)} {htmllib.escape(date)}</title></head>"
        f'<body style="background:{PAPER};margin:0;padding:0;color:{INK};">'
        f'<div style="max-width:680px;margin:0 auto;padding:48px 32px;background:{PAPER};">'
        f"{body_html}"
        f"</div></body></html>"
    )


def send_email_smtp(args: dict) -> dict:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError:
        return {"ok": False, "error": "SMTP_PORT must be an integer"}
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("SMTP_FROM", user or "")
    recipients_env = os.environ.get("RECIPIENT_EMAILS", "")

    if not user or not password:
        return {"ok": False, "error": "SMTP_USER or SMTP_PASSWORD not set in host env"}

    recipients = args.get("recipients") or [
        r.strip() for r in recipients_env.split(",") if r.strip()
    ]
    if not recipients:
        return {"ok": False, "error": "No recipients (set RECIPIENT_EMAILS env or pass via tool)"}

    subject = args["subject"]
    body_markdown = args["body_markdown"]
    body_html = render_brief_email_html(body_markdown)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_markdown, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())
        return {"ok": True, "recipients": recipients, "subject": subject}
    except smtplib.SMTPAuthenticationError as e:
        return {"ok": False, "error": f"SMTP auth failed (check SMTP_USER and SMTP_PASSWORD; "
                                       f"Gmail requires an App Password): {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def read_user_line(prompt: str = "> ") -> str | None:
    try:
        tty = open("/dev/tty", "r")
    except (OSError, FileNotFoundError):
        try:
            return input(prompt).strip()
        except EOFError:
            return None
    try:
        try:
            import termios
            termios.tcflush(tty.fileno(), termios.TCIFLUSH)
        except (ImportError, OSError, AttributeError):
            pass
        while True:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            line = tty.readline()
            if not line:
                return None
            stripped = line.rstrip("\n").strip()
            if stripped:
                return stripped
    finally:
        tty.close()


def handle_custom_tool(event, send) -> None:
    tool_name = getattr(event, "tool_name", None) or getattr(event, "name", None)
    if tool_name == "send_email_smtp":
        print("\n[sending email via SMTP...]", file=sys.stderr, flush=True)
        result = send_email_smtp(event.input)
        log_result = {k: v for k, v in result.items() if k != "body_markdown"}
        print(f"[smtp result] {log_result}", file=sys.stderr, flush=True)
        send([{
            "type": "user.custom_tool_result",
            "custom_tool_use_id": event.id,
            "content": [{"type": "text", "text": json.dumps(result)}],
            "is_error": not result.get("ok", False),
        }])
    else:
        send([{
            "type": "user.custom_tool_result",
            "custom_tool_use_id": event.id,
            "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
            "is_error": True,
        }])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AI Weekly Brief Bot.")
    parser.add_argument("prompt", nargs="*", help="Message to send (default: weekly kickoff).")
    parser.add_argument("--title", default=None, help="Optional session title (new sessions only).")
    parser.add_argument("--session-id", default=None,
                        help="Resume an existing session by ID instead of creating a new one.")
    parser.add_argument("--preview-html", default=None, metavar="MD_FILE",
                        help="Render a markdown file to NYT-style HTML and write to PATH.html, then exit.")
    parser.add_argument("--preview-out", default="preview.html",
                        help="Output path for --preview-html (default: preview.html).")
    parser_args = parser.parse_args()

    if parser_args.preview_html:
        with open(parser_args.preview_html) as f:
            md_text = f.read()
        out = render_brief_email_html(md_text)
        with open(parser_args.preview_out, "w") as f:
            f.write(out)
        print(f"wrote {parser_args.preview_out} ({len(out)} bytes)", file=sys.stderr)
        return 0

    client = anthropic.Anthropic()

    if parser_args.session_id:
        session_id = parser_args.session_id
        print(f"[resuming session {session_id}]", file=sys.stderr, flush=True)
        msg_to_send = " ".join(parser_args.prompt) if parser_args.prompt else None
    else:
        session = client.beta.sessions.create(
            agent=AGENT_ID,
            environment_id=ENVIRONMENT_ID,
            title=parser_args.title,
        )
        session_id = session.id
        print(f"[session {session_id}]", file=sys.stderr, flush=True)
        msg_to_send = " ".join(parser_args.prompt) if parser_args.prompt else (
            "Run this week's brief and email it via the send_email_smtp tool."
        )

    def send(events):
        client.beta.sessions.events.send(session_id=session_id, events=events)

    if parser_args.session_id:
        try:
            history = list(client.beta.sessions.events.list(session_id=session_id))
            answered = {
                getattr(e, "custom_tool_use_id", None)
                for e in history if e.type == "user.custom_tool_result"
            }
            pending = [
                e for e in history
                if e.type == "agent.custom_tool_use" and e.id not in answered
            ]
            if pending:
                msg_to_send = None
                for e in pending:
                    print(f"[resume: handling pending tool call {e.id}]",
                          file=sys.stderr, flush=True)
                    handle_custom_tool(e, send)
        except Exception as exc:
            print(f"[warn: pending-tool check failed: {type(exc).__name__}: {exc}]",
                  file=sys.stderr)

    try:
        with client.beta.sessions.events.stream(session_id=session_id) as stream:
            if msg_to_send:
                send([{"type": "user.message",
                       "content": [{"type": "text", "text": msg_to_send}]}])
            for event in stream:
                etype = event.type
                if etype == "agent.message":
                    for block in event.content:
                        if getattr(block, "type", None) == "text":
                            print(block.text, end="", flush=True)
                elif etype == "agent.custom_tool_use":
                    handle_custom_tool(event, send)
                elif etype == "session.error":
                    print(f"\n[session.error] {getattr(event, 'error', event)}",
                          file=sys.stderr)
                    return 1
                elif etype == "session.status_terminated":
                    print("\n[session terminated]", file=sys.stderr)
                    return 0
                elif etype == "session.status_idle":
                    reason_type = getattr(getattr(event, "stop_reason", None), "type", None)
                    if reason_type == "requires_action":
                        continue
                    if reason_type == "retries_exhausted":
                        print("\n[idle: retries_exhausted]", file=sys.stderr)
                        return 1
                    print()
                    user_input = read_user_line("> ")
                    if user_input is None:
                        print(f"[stdin closed -- exiting. Resume later with: "
                              f"python3 run.py --session-id {session_id} <your message>]",
                              file=sys.stderr)
                        return 0
                    if user_input.lower() in ("q", "quit", "exit"):
                        print(f"[exiting. Resume later with: "
                              f"python3 run.py --session-id {session_id} <your message>]",
                              file=sys.stderr)
                        return 0
                    send([{"type": "user.message",
                           "content": [{"type": "text", "text": user_input}]}])
    except anthropic.APIError as e:
        print(f"\n[api error] {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
