#!/usr/bin/env python3
"""
Multi-Agent Newsletter Orchestrator.

Architecture:
    Memory --> [Research Launches || Research Papers] --> Evaluator --> Writer --> Critic (loop) --> Delivery

Each agent is a separate Managed Agent in Claude Console. The orchestrator:
1. Creates a session per agent
2. Streams events
3. Handles custom tool calls (send_email_smtp)
4. Passes data between agents via initial messages
"""
import json
import os
import smtplib
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# AGENT REGISTRY
# ============================================================================
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
# SMTP TOOL HANDLER (for delivery agent)
# ============================================================================
def send_email_smtp(subject: str, body_markdown: str) -> Dict[str, Any]:
    """Send email via configured SMTP server."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)
    recipients = [e.strip() for e in os.getenv("RECIPIENT_EMAILS", "").split(",") if e.strip()]

    if not all([smtp_host, smtp_user, smtp_pass, recipients]):
        return {"ok": False, "error": "Missing SMTP config or recipients"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_markdown, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return {"ok": True, "recipients": recipients, "subject": subject}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ============================================================================
# AGENT RUNNER
# ============================================================================
class AgentRunner:
    """Run a single Managed Agent and capture its output."""

    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def run(self, agent_id: str, initial_message: str, agent_name: str = "agent") -> Dict[str, Any]:
        """Run agent, return final response and any session artifacts."""
        print(f"\n[{agent_name}] Starting session...", flush=True)
        start = time.time()

        session = self.client.beta.agents.sessions.create(
            agent_id=agent_id,
            input=initial_message,
        )
        session_id = session.id
        print(f"[{agent_name}] Session: {session_id}", flush=True)

        final_text = ""
        with self.client.beta.agents.sessions.stream(session_id) as stream:
            for event in stream:
                event_type = getattr(event, "type", "")

                if event_type == "tool_use":
                    tool_name = getattr(event, "name", "")
                    tool_input = getattr(event, "input", {})

                    if tool_name == "send_email_smtp":
                        print(f"[{agent_name}] Sending email...", flush=True)
                        result = send_email_smtp(**tool_input)
                        self.client.beta.agents.sessions.tool_result(
                            session_id=session_id,
                            tool_use_id=event.id,
                            content=json.dumps(result),
                        )
                        print(f"[{agent_name}] Email result: {result}", flush=True)

                elif event_type == "message":
                    content = getattr(event, "content", "")
                    if isinstance(content, list):
                        for block in content:
                            if hasattr(block, "text"):
                                final_text += block.text
                    elif isinstance(content, str):
                        final_text += content

                elif event_type == "session_complete":
                    break

        elapsed = time.time() - start
        print(f"[{agent_name}] Done in {elapsed:.1f}s", flush=True)

        return {
            "session_id": session_id,
            "output": final_text,
            "elapsed": elapsed,
        }


# ============================================================================
# ORCHESTRATOR
# ============================================================================
class MultiAgentOrchestrator:
    """Coordinates 7 agents to produce and deliver the newsletter."""

    def __init__(self):
        self.client = anthropic.Anthropic()
        self.runner = AgentRunner(self.client)
        self.results = {}

    def step_1_memory(self) -> str:
        """Memory agent: read past briefs, return covered topics."""
        result = self.runner.run(
            AGENTS["memory"],
            "List all topics covered in newsletter briefs from the past 4 weeks. Return JSON.",
            "memory"
        )
        self.results["memory"] = result
        return result["output"]

    def step_2_3_research_parallel(self) -> tuple:
        """Research Launches + Papers in PARALLEL."""
        print("\n>>> Running Research Launches + Papers in parallel...", flush=True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_launches = executor.submit(
                self.runner.run,
                AGENTS["research_launches"],
                "Find 5-7 significant AI company launches from the past 7 days. Return JSON.",
                "research_launches"
            )
            future_papers = executor.submit(
                self.runner.run,
                AGENTS["research_papers"],
                "Find 2-3 actionable AI research findings from the past 7 days. Return JSON.",
                "research_papers"
            )

            launches_result = future_launches.result()
            papers_result = future_papers.result()

        self.results["launches"] = launches_result
        self.results["papers"] = papers_result
        return launches_result["output"], papers_result["output"]

    def step_4_evaluate(self, launches: str, papers: str, covered: str) -> str:
        """Evaluator: score and rank items, filter dupes."""
        prompt = (
            f"Evaluate the following items. Score each, drop duplicates, return top selections.\n\n"
            f"LAUNCHES:\n{launches}\n\n"
            f"PAPERS:\n{papers}\n\n"
            f"COVERED TOPICS (skip these):\n{covered}"
        )
        result = self.runner.run(AGENTS["evaluator"], prompt, "evaluator")
        self.results["evaluations"] = result
        return result["output"]

    def step_5_write(self, launches: str, papers: str, evaluations: str, feedback: str = "") -> str:
        """Writer: produce the brief. Optional feedback for revision."""
        feedback_section = f"\n\nCRITIC FEEDBACK (address these):\n{feedback}" if feedback else ""
        prompt = (
            f"Write the newsletter brief from these inputs:\n\n"
            f"LAUNCHES:\n{launches}\n\n"
            f"PAPERS:\n{papers}\n\n"
            f"EVALUATIONS (use selected_launches and selected_papers):\n{evaluations}"
            f"{feedback_section}"
        )
        result = self.runner.run(AGENTS["writer"], prompt, "writer")
        self.results["draft"] = result
        return result["output"]

    def step_6_critique(self, draft: str) -> Dict[str, Any]:
        """Critic: review draft, return APPROVE/REJECT decision."""
        result = self.runner.run(
            AGENTS["critic"],
            f"Review this draft brief. Return JSON with decision and feedback:\n\n{draft}",
            "critic"
        )
        self.results["critique"] = result

        output = result["output"]
        decision = "APPROVE" if "APPROVE" in output.upper() else "REJECT"
        return {"decision": decision, "feedback": output}

    def step_7_deliver(self, final_brief: str) -> Dict[str, Any]:
        """Delivery: send email with the final brief."""
        result = self.runner.run(
            AGENTS["delivery"],
            f"Send this brief via email. Use the send_email_smtp tool:\n\n{final_brief}",
            "delivery"
        )
        self.results["delivery"] = result
        return result

    def orchestrate(self):
        """Run the full multi-agent workflow."""
        print("=" * 70)
        print("MULTI-AGENT NEWSLETTER ORCHESTRATOR")
        print("=" * 70)
        total_start = time.time()

        # Step 1: Memory
        covered = self.step_1_memory()

        # Steps 2 & 3: Research in parallel
        launches, papers = self.step_2_3_research_parallel()

        # Step 4: Evaluate
        evaluations = self.step_4_evaluate(launches, papers, covered)

        # Step 5: Write
        draft = self.step_5_write(launches, papers, evaluations)

        # Step 6: Critic loop (max 2 retries)
        for retry in range(2):
            critique = self.step_6_critique(draft)
            if critique["decision"] == "APPROVE":
                print(f"\n>>> Critic APPROVED on attempt {retry + 1}", flush=True)
                break
            print(f"\n>>> Critic REJECTED. Revising (attempt {retry + 2}/3)...", flush=True)
            draft = self.step_5_write(launches, papers, evaluations, feedback=critique["feedback"])
        else:
            print("\n>>> Max retries reached. Proceeding with last draft.", flush=True)

        # Step 7: Deliver
        delivery = self.step_7_deliver(draft)

        total_elapsed = time.time() - total_start
        print("\n" + "=" * 70)
        print(f"COMPLETE. Total time: {total_elapsed:.1f}s")
        print("=" * 70)

        # Save final brief locally
        os.makedirs("briefs", exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        brief_path = f"briefs/{date_str}.md"
        with open(brief_path, "w") as f:
            f.write(draft)
        print(f"Saved brief: {brief_path}")

        # Save run log
        log_path = f"briefs/{date_str}_log.json"
        log = {
            "date": date_str,
            "total_elapsed": total_elapsed,
            "agents": {
                name: {
                    "session_id": r.get("session_id"),
                    "elapsed": r.get("elapsed"),
                }
                for name, r in self.results.items()
            },
        }
        with open(log_path, "w") as f:
            json.dump(log, f, indent=2)
        print(f"Saved log: {log_path}")


if __name__ == "__main__":
    orchestrator = MultiAgentOrchestrator()
    orchestrator.orchestrate()
