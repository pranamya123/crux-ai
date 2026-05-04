#!/usr/bin/env python3
"""
Multi-Agent Newsletter Orchestrator v2 - Shared Session Pattern.

True "many brains, one session" pattern:
- All 7 agents read/write to a SHARED session log (Supabase session_events table)
- Each agent emits its work as events
- Subsequent agents read prior events via get_events()
- Orchestrator handles custom tools: emit_event, get_events, send_email_smtp
"""
import json
import os
import smtplib
import ssl
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import anthropic
from dotenv import load_dotenv
from supabase import create_client, Client
from email_renderer import render_brief_email_html

load_dotenv()

# ============================================================================
# CONFIG
# ============================================================================
ENVIRONMENT_ID = "env_01VeZNPz8LZMFQbR6LdPNV2x"

AGENTS = {
    "memory": "agent_011CahyVRd4a9UPrs6hVjBCD",
    "research_launches": "agent_011CahyZiK2LmFw634VAPqaG",
    "research_papers": "agent_011CahyagB4S23JRUyVw5ikT",
    "evaluator": "agent_011CahybXQjvG9FEzNch8iqh",
    "writer": "agent_011CahyeAHLRe13m3nZ2jSSz",
    "critic": "agent_011CahyewGmj313dgDipAwxw",
    "delivery": "agent_011CahyfZR9qeUvsHuMFS2Co",
}

# ============================================================================
# SUPABASE CONNECTION (Shared Session Log)
# ============================================================================
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("ANON_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================================
# CUSTOM TOOL HANDLERS
# ============================================================================
def handle_emit_event(session_id: str, agent_name: str, event_type: str, data: dict) -> Dict[str, Any]:
    try:
        result = supabase.table("session_events").insert({
            "session_id": session_id,
            "agent_name": agent_name,
            "event_type": event_type,
            "data": data,
        }).execute()
        return {"ok": True, "event_id": result.data[0]["id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_get_events(session_id: str, agent_name: Optional[str] = None,
                       event_type: Optional[str] = None) -> Dict[str, Any]:
    try:
        query = supabase.table("session_events").select("*").eq("session_id", session_id)
        if agent_name:
            query = query.eq("agent_name", agent_name)
        if event_type:
            query = query.eq("event_type", event_type)
        result = query.order("created_at").execute()
        return {"ok": True, "events": result.data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_send_email_smtp(args: dict) -> Dict[str, Any]:
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
        return {"ok": False, "error": "SMTP_USER or SMTP_PASSWORD not set"}

    recipients = args.get("recipients") or [
        r.strip() for r in recipients_env.split(",") if r.strip()
    ]
    if not recipients:
        return {"ok": False, "error": "No recipients"}

    subject = args["subject"]
    body_markdown = args["body_markdown"]

    body_html = render_brief_email_html(body_markdown)
    print(f"[SMTP] Sending to recipients: {recipients}", flush=True)

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
        print(f"[SMTP] Sent successfully to: {recipients}", flush=True)
        return {"ok": True, "recipients": recipients, "subject": subject}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ============================================================================
# AGENT RUNNER
# ============================================================================
class AgentRunner:
    def __init__(self, client: anthropic.Anthropic, shared_session_id: str):
        self.client = client
        self.shared_session_id = shared_session_id

    def handle_tool_call(self, event, session_id: str, agent_name: str):
        """Route custom tool calls to handlers."""
        tool_name = getattr(event, "tool_name", None) or getattr(event, "name", None)
        tool_input = event.input or {}

        if tool_name == "emit_event":
            result = handle_emit_event(
                session_id=tool_input.get("session_id", self.shared_session_id),
                agent_name=agent_name,
                event_type=tool_input.get("event_type", ""),
                data=tool_input.get("data", {}),
            )
        elif tool_name == "get_events":
            result = handle_get_events(
                session_id=tool_input.get("session_id", self.shared_session_id),
                agent_name=tool_input.get("agent_name"),
                event_type=tool_input.get("event_type"),
            )
        elif tool_name == "send_email_smtp":
            result = handle_send_email_smtp(tool_input)
        else:
            result = {"ok": False, "error": f"Unknown tool: {tool_name}"}

        self.client.beta.sessions.events.send(
            session_id=session_id,
            events=[{
                "type": "user.custom_tool_result",
                "custom_tool_use_id": event.id,
                "content": [{"type": "text", "text": json.dumps(result)}],
                "is_error": not result.get("ok", False),
            }],
        )
        return result

    def run(self, agent_id: str, agent_name: str, prompt: str) -> Dict[str, Any]:
        """Run a single agent with shared_session_id passed in initial message."""
        full_prompt = f"session_id: {self.shared_session_id}\n\n{prompt}"

        print(f"\n[{agent_name}] Creating session...", flush=True)
        start = time.time()

        session = self.client.beta.sessions.create(
            agent=agent_id,
            environment_id=ENVIRONMENT_ID,
            title=f"{agent_name}_{self.shared_session_id}",
        )
        session_id = session.id
        print(f"[{agent_name}] Session: {session_id}", flush=True)

        final_text = ""
        tool_calls = 0

        try:
            with self.client.beta.sessions.events.stream(session_id=session_id) as stream:
                # Send initial message
                self.client.beta.sessions.events.send(
                    session_id=session_id,
                    events=[{
                        "type": "user.message",
                        "content": [{"type": "text", "text": full_prompt}],
                    }],
                )

                for event in stream:
                    etype = event.type

                    if etype == "agent.message":
                        for block in event.content:
                            if getattr(block, "type", None) == "text":
                                final_text += block.text

                    elif etype == "agent.custom_tool_use":
                        tool_calls += 1
                        tool_name = getattr(event, "tool_name", None) or getattr(event, "name", None)
                        print(f"[{agent_name}] Tool call #{tool_calls}: {tool_name}", flush=True)
                        self.handle_tool_call(event, session_id, agent_name)

                    elif etype == "session.status_idle":
                        reason_type = getattr(getattr(event, "stop_reason", None), "type", None)
                        if reason_type == "requires_action":
                            continue
                        # Agent is done
                        break

                    elif etype == "session.status_terminated":
                        break

                    elif etype == "session.error":
                        print(f"[{agent_name}] ERROR: {getattr(event, 'error', event)}", flush=True)
                        break

        except Exception as e:
            print(f"[{agent_name}] Exception: {type(e).__name__}: {e}", flush=True)

        elapsed = time.time() - start
        print(f"[{agent_name}] Done in {elapsed:.1f}s ({tool_calls} tool calls)", flush=True)

        return {
            "agent_session_id": session_id,
            "output": final_text,
            "elapsed": elapsed,
            "tool_calls": tool_calls,
        }


# ============================================================================
# ORCHESTRATOR
# ============================================================================
class OrchestratorV2:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.session_id = f"newsletter_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self.runner = AgentRunner(self.client, self.session_id)
        print(f"\n{'=' * 70}")
        print(f"SHARED SESSION ID: {self.session_id}")
        print(f"{'=' * 70}")

    def step_memory(self):
        return self.runner.run(
            AGENTS["memory"], "memory",
            "Read past briefs (if any) and emit covered_topics event."
        )

    def step_research_parallel(self):
        print("\n>>> Running Research agents in parallel...", flush=True)
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_l = executor.submit(self.runner.run, AGENTS["research_launches"], "research_launches",
                "Find AI launches from past 7 days. Emit launches_researched event."
            )
            f_p = executor.submit(self.runner.run, AGENTS["research_papers"], "research_papers",
                "Find AI research findings from past 7 days. Emit papers_researched event."
            )
            return f_l.result(), f_p.result()

    def step_evaluate(self):
        return self.runner.run(
            AGENTS["evaluator"], "evaluator",
            "Read prior events with get_events, score and rank items, emit items_evaluated event."
        )

    def step_write(self, retry_msg: str = ""):
        prompt = "Read prior events with get_events, write the brief, emit draft_written event."
        if retry_msg:
            prompt += f"\n\nNOTE: {retry_msg}"
        return self.runner.run(AGENTS["writer"], "writer", prompt)

    def step_critique(self):
        return self.runner.run(
            AGENTS["critic"], "critic",
            "Read draft_written event, review quality, emit draft_approved or critic_rejection event."
        )

    def step_deliver(self):
        return self.runner.run(
            AGENTS["delivery"], "delivery",
            "Read draft_approved event, send email via send_email_smtp tool, emit email_sent event."
        )

    def check_critic_decision(self) -> bool:
        result = handle_get_events(session_id=self.session_id, event_type="draft_approved")
        return result.get("ok") and len(result.get("events", [])) > 0

    def orchestrate(self):
        print("\n" + "=" * 70)
        print("MULTI-AGENT ORCHESTRATOR V2 - SHARED SESSION")
        print("=" * 70)
        total_start = time.time()
        results = {}

        results["memory"] = self.step_memory()
        results["launches"], results["papers"] = self.step_research_parallel()
        results["evaluator"] = self.step_evaluate()
        results["writer_v1"] = self.step_write()

        max_retries = 2
        approved = False
        for attempt in range(max_retries + 1):
            results[f"critic_v{attempt + 1}"] = self.step_critique()
            if self.check_critic_decision():
                print(f"\n>>> Critic APPROVED on attempt {attempt + 1}", flush=True)
                approved = True
                break
            if attempt < max_retries:
                print(f"\n>>> Critic REJECTED. Re-running writer...", flush=True)
                results[f"writer_v{attempt + 2}"] = self.step_write(
                    retry_msg="Previous draft was rejected. Read critic_rejection event and address all issues."
                )

        if not approved:
            print("\n>>> Max retries reached. Stopping.", flush=True)
            return

        results["delivery"] = self.step_deliver()

        total = time.time() - total_start
        print("\n" + "=" * 70)
        print(f"COMPLETE. Total: {total:.1f}s")
        print(f"Session: {self.session_id}")
        print("=" * 70)

        os.makedirs("briefs", exist_ok=True)
        log_path = f"briefs/{self.session_id}_log.json"
        with open(log_path, "w") as f:
            json.dump({
                "session_id": self.session_id,
                "total_elapsed": total,
                "approved": approved,
                "agents": {n: {k: v for k, v in r.items() if k != "output"}
                          for n, r in results.items()},
            }, f, indent=2)
        print(f"\nLog saved: {log_path}")


if __name__ == "__main__":
    OrchestratorV2().orchestrate()
