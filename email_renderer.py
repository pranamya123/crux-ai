"""Editorial HTML email rendering (extracted from run.py)."""
import html as htmllib
import re
from urllib.parse import quote
import markdown as md_lib

SERIF = "'Source Serif Pro', Georgia, 'Times New Roman', serif"
SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
INK = "#111111"
RULE = "#000000"
MUTED = "#6b6b6b"
HAIR = "#dddddd"
PAPER = "#ffffff"


def _md_inline(text: str) -> str:
    rendered = md_lib.markdown(text, extensions=["extra", "sane_lists", "smarty"]).strip()
    if rendered.startswith("<p>") and rendered.endswith("</p>"):
        rendered = rendered[3:-4]
    return _style_inline_html(rendered)


def _md_block(text: str) -> str:
    rendered = md_lib.markdown(text, extensions=["extra", "sane_lists", "smarty"])
    return _style_inline_html(rendered)


def _style_inline_html(html: str) -> str:
    html = re.sub(r"<p>", '<p style="margin:0 0 1.1em 0;">', html)
    html = re.sub(r"<a ", f'<a style="color:{INK};text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;" ', html)
    html = re.sub(r"<strong>", '<strong style="font-weight:700;">', html)
    html = re.sub(r"<em>", '<em style="font-style:italic;">', html)
    html = re.sub(r"<code>", '<code style="font-family:Menlo,Consolas,monospace;background:#f4f1ea;padding:1px 5px;font-size:0.9em;border-radius:2px;">', html)
    html = re.sub(r"<ul>", '<ul style="margin:0.5em 0 1em 1em;padding:0;">', html)
    html = re.sub(r"<ol>", '<ol style="margin:0.5em 0 1em 1em;padding:0;">', html)
    html = re.sub(r"<li>", '<li style="margin:0 0 0.5em 0;">', html)
    return html


def _parse_brief(md_text: str) -> dict:
    out = {"title": "AI Weekly", "date": "", "lead": "", "company_items": [],
           "research_items": [], "build_idea": ""}

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

    def parse_numbered(content):
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
        if "what shipped" in heading or "company items" in heading:
            out["company_items"] = parse_numbered(content)
        elif "worth knowing" in heading or "research" in heading:
            out["research_items"] = parse_numbered(content)
        elif "build" in heading:
            out["build_idea"] = content.strip()

    return out


def _label(text):
    return f'<div style="font-family:{SANS};font-size:10px;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;color:{MUTED};">{htmllib.escape(text)}</div>'


def _back_to_top(link_prefix: str = ""):
    return f'<div style="border-top:1px solid {RULE};padding-top:14px;margin-top:48px;font-family:{SANS};font-size:11px;letter-spacing:1.5px;text-transform:uppercase;"><a href="{link_prefix}#top" style="color:{MUTED};text-decoration:none;">&uarr; Back to contents</a></div>'


def _drop_cap_first(html):
    pattern = re.compile(r'(<p style="[^"]*">)([A-Za-z])')
    cap_style = f"float:left;font-family:{SERIF};font-size:62px;line-height:0.85;padding:6px 8px 0 0;color:{INK};font-weight:700;"
    return pattern.sub(rf'\1<span style="{cap_style}">\2</span>', html, count=1)


def _render_item(item, date, anchor_prefix="item", section_label="What shipped", num_format="No.{n:02d}", link_prefix: str = ""):
    num_label = num_format.format(n=item["number"])
    body = _drop_cap_first(_md_block(item["body_md"]))
    return (
        f'<article id="{anchor_prefix}-{item["number"]}" style="padding:64px 0 32px;border-top:3px solid {RULE};page-break-before:always;">'
        f'{_label(f"{section_label} · {num_label}")}'
        f'<h2 style="font-family:{SERIF};font-size:32px;line-height:1.18;font-weight:400;letter-spacing:-0.5px;margin:14px 0 12px;color:{INK};">{htmllib.escape(item["headline"])}</h2>'
        f'<div style="font-family:{SERIF};font-style:italic;font-size:13px;color:{MUTED};margin-bottom:32px;">By the AI Weekly Bot · {htmllib.escape(date) if date else ""}</div>'
        f'<div style="font-family:{SERIF};font-size:18px;line-height:1.7;color:#1f1f1f;">{body}</div>{_back_to_top(link_prefix)}</article>'
    )


def render_brief_email_html(
    body_markdown: str,
    recipient_email: str | None = None,
    base_url: str | None = None,
) -> str:
    brief = _parse_brief(body_markdown)
    title = brief["title"]
    date = brief["date"]

    # When rendering for an email, in-document anchors (#item-1) are stripped by
    # most clients (Gmail in particular). Resolve them against the public /latest
    # URL so they jump to the web copy of this issue and scroll to the right
    # section. When base_url is not provided (e.g. saving the /latest snapshot),
    # leave plain anchors so they navigate within the same page.
    link_prefix = f"{base_url.rstrip('/')}/latest" if base_url else ""

    masthead = (
        f'<header style="text-align:center;padding:8px 0 32px;border-top:3px solid {RULE};border-bottom:1px solid {RULE};margin-bottom:40px;">'
        f'<div style="font-family:{SANS};font-size:11px;letter-spacing:4px;text-transform:uppercase;font-weight:700;color:{MUTED};padding-top:24px;">{htmllib.escape(title)}</div>'
        f'<h1 style="font-family:{SERIF};font-size:34px;font-weight:400;letter-spacing:-0.5px;margin:14px 0 18px;color:{INK};line-height:1.15;">{htmllib.escape(date)}</h1>'
        f'<div style="font-family:{SANS};font-size:10px;letter-spacing:2px;text-transform:uppercase;color:{MUTED};">Vol. 1 · The Weekly Signal</div>'
        f"</header>"
    )

    lead_html = ""
    if brief["lead"]:
        lead_html = (
            f'<div style="font-family:{SERIF};font-style:italic;font-size:18px;line-height:1.55;color:{INK};text-align:center;max-width:520px;margin:0 auto 48px;padding-bottom:32px;border-bottom:1px solid {HAIR};">'
            f'{_md_inline(brief["lead"])}</div>'
        )

    # TOC
    toc_rows = []
    if brief["company_items"]:
        toc_rows.append(f'<tr><td style="padding:24px 0 6px;"><div style="font-family:{SANS};font-size:10px;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;color:{MUTED};">What shipped</div><div style="border-bottom:1px solid {RULE};margin-top:6px;"></div></td></tr>')
        for it in brief["company_items"]:
            toc_rows.append(f'<tr><td style="border-bottom:1px solid {HAIR};padding:14px 0;"><a href="{link_prefix}#item-{it["number"]}" style="color:{INK};text-decoration:none;display:block;"><table cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td style="font-family:{SANS};font-size:10px;letter-spacing:2px;color:{MUTED};width:60px;vertical-align:top;padding-top:6px;font-weight:700;">No.{it["number"]:02d}</td><td style="font-family:{SERIF};font-size:18px;line-height:1.35;color:{INK};">{htmllib.escape(it["headline"])}</td></tr></table></a></td></tr>')

    if brief["research_items"]:
        toc_rows.append(f'<tr><td style="padding:24px 0 6px;"><div style="font-family:{SANS};font-size:10px;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;color:{MUTED};">Worth knowing</div><div style="border-bottom:1px solid {RULE};margin-top:6px;"></div></td></tr>')
        for it in brief["research_items"]:
            toc_rows.append(f'<tr><td style="border-bottom:1px solid {HAIR};padding:14px 0;"><a href="{link_prefix}#research-{it["number"]}" style="color:{INK};text-decoration:none;display:block;"><table cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td style="font-family:{SANS};font-size:10px;letter-spacing:2px;color:{MUTED};width:60px;vertical-align:top;padding-top:6px;font-weight:700;">R.{it["number"]:02d}</td><td style="font-family:{SERIF};font-size:18px;line-height:1.35;color:{INK};">{htmllib.escape(it["headline"])}</td></tr></table></a></td></tr>')

    if brief["build_idea"]:
        toc_rows.append(f'<tr><td style="padding:24px 0 6px;"><div style="font-family:{SANS};font-size:10px;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;color:{MUTED};">Saturday morning</div><div style="border-bottom:1px solid {RULE};margin-top:6px;"></div></td></tr>')
        toc_rows.append(f'<tr><td style="border-bottom:1px solid {HAIR};padding:14px 0;"><a href="{link_prefix}#build" style="color:{INK};text-decoration:none;display:block;font-family:{SERIF};font-size:18px;font-style:italic;">Your 4-hour build this week &rarr;</a></td></tr>')

    toc = (
        f'<nav id="top" style="margin:0 auto 64px;">'
        f'{_label("In this issue")}<div style="border-bottom:2px solid {RULE};margin:8px 0 0;"></div>'
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%">{"".join(toc_rows)}</table></nav>'
    )

    items_html = "".join(_render_item(it, date, "item", "What shipped", "No.{n:02d}", link_prefix) for it in brief["company_items"])
    items_html += "".join(_render_item(it, date, "research", "Worth knowing", "Research {n:02d}", link_prefix) for it in brief["research_items"])

    build_html = ""
    if brief["build_idea"]:
        body = _md_block(brief["build_idea"])
        build_html = (
            f'<section id="build" style="padding:64px 0 32px;border-top:3px solid {RULE};page-break-before:always;">'
            f'{_label("Section · Saturday morning")}'
            f'<h2 style="font-family:{SERIF};font-size:28px;font-weight:400;letter-spacing:-0.5px;margin:14px 0 28px;color:{INK};">Your 4-hour build this week</h2>'
            f'<div style="font-family:{SERIF};font-size:18px;line-height:1.7;color:#1f1f1f;">{body}</div>{_back_to_top(link_prefix)}</section>'
        )

    unsubscribe_html = ""
    if recipient_email and base_url:
        unsub_url = f"{base_url.rstrip('/')}/unsubscribe?email={quote(recipient_email)}"
        unsubscribe_html = (
            f'<div style="margin-top:14px;font-family:{SANS};font-size:11px;letter-spacing:1px;text-transform:none;color:{MUTED};">'
            f'You are subscribed as {htmllib.escape(recipient_email)}. '
            f'<a href="{htmllib.escape(unsub_url)}" style="color:{MUTED};text-decoration:underline;">Unsubscribe</a>.'
            f'</div>'
        )

    footer = (
        f'<footer style="border-top:1px solid {RULE};margin-top:64px;padding-top:24px;text-align:center;font-family:{SANS};font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:{MUTED};">'
        f'AI Weekly · {htmllib.escape(date) if date else ""} · End of issue'
        f'{unsubscribe_html}'
        f'</footer>'
    )

    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{htmllib.escape(title)} {htmllib.escape(date)}</title></head>"
        f'<body style="background:{PAPER};margin:0;padding:0;color:{INK};">'
        f'<div style="max-width:680px;margin:0 auto;padding:48px 32px;background:{PAPER};">'
        f"{masthead}{lead_html}{toc}{items_html}{build_html}{footer}"
        f"</div></body></html>"
    )
