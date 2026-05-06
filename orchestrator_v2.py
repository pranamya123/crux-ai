#!/usr/bin/env python3
"""
Multi-Agent Newsletter Orchestrator v3 - Step-Based Architecture.

Aligns with Anthropic's "Scaling Managed Agents: Decoupling the brain from the hands":
- Each step is independent and stateless (cattle, not pets)
- Session log = durable shared state (Memory Stores JSONL)
- Each "hand" (tool) is its own module, can fail/retry independently
- Steps can run in separate Vercel functions (avoids timeout)
- Full observability: structured JSON logs + per-agent metrics
- Retry with exponential backoff on transient failures
- Resume from any step via session_id

Architecture:
- Brain = Managed Agents (7 specialized agents)
- Hands = tools/ modules (memory_store, email)
- Session = JSONL files in /mnt/memory/ or ./memory_local/
- Harness = this file (or split api/ functions for Vercel)
"""

import argparse
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

import anthropic
from dotenv import load_dotenv

from observability import StructuredLogger, RunTracker
from retry import retry_call, is_retryable_anthropic_error
from tools.memory_store import (
    handle_emit_event,
    handle_get_events,
    has_event,
    count_events,
)
from tools.email import handle_send_email_smtp
from tools.verifier import handle_verify_links

load_dotenv(override=True)

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
    "verifier": "agent_011CakPwT4eodJzVGduhySgq",
    "delivery": "agent_011CahyfZR9qeUvsHuMFS2Co",
}

# Steps in order. Each step has terminal event(s) that mark completion.
# Used by step-based orchestration (one Vercel function per step).
STEP_ORDER = ["memory", "research", "evaluate", "write_critique", "verify", "deliver"]

STEP_TERMINAL_EVENTS = {
    "memory": ["covered_topics"],
    "research": ["launches_researched", "papers_researched"],
    "evaluate": ["items_evaluated"],
    "write_critique": ["draft_approved"],
    "verify": ["verification_passed"],
    "deliver": ["email_sent"],
}

# Critical agents must succeed; optional ones can be skipped on failure.
AGENT_CRITICALITY = {
    "memory": "optional",
    "research_launches": "critical",
    "research_papers": "optional",  # Can produce 0 papers
    "evaluator": "critical",
    "writer": "critical",
    "critic": "critical",
    "verifier": "critical",  # Must pass before delivery — catches hallucinated URLs
    "delivery": "critical",
}

# Per-agent timeouts (seconds). If an agent exceeds, we mark as failed.
AGENT_TIMEOUTS = {
    "memory": 120,
    "research_launches": 300,
    "research_papers": 300,
    "evaluator": 180,
    "writer": 300,
    "critic": 180,
    "verifier": 120,  # Mostly HTTP HEAD requests; should be fast
    "delivery": 120,
}

# Max times the Verifier can fail before we abort (Writer fixes URLs each retry).
MAX_VERIFICATION_RETRIES = 2


# ============================================================================
# AGENT RUNNER
# ============================================================================
class AgentRunner:
    """Runs a single agent with retry, timeout, and observability."""

    def __init__(self, client: anthropic.Anthropic, shared_session_id: str,
                 logger: Optional[StructuredLogger] = None,
                 tracker: Optional[RunTracker] = None):
        self.client = client
        self.shared_session_id = shared_session_id
        self.logger = logger or StructuredLogger(session_id=shared_session_id)
        self.tracker = tracker

    def handle_tool_call(self, event, session_id: str, agent_name: str):
        """Route custom tool calls to handlers (independent hands)."""
        tool_name = getattr(event, "tool_name", None) or getattr(event, "name", None)
        tool_input = event.input or {}

        self.logger.info(
            f"tool_call: {tool_name}",
            agent=agent_name,
            tool=tool_name,
        )

        try:
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
                    limit=tool_input.get("limit"),
                )
            elif tool_name == "send_email_smtp":
                result = handle_send_email_smtp(tool_input)
            elif tool_name == "verify_links":
                result = handle_verify_links(tool_input)
            else:
                result = {"ok": False, "error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            result = {"ok": False, "error": f"Tool execution failed: {type(e).__name__}: {e}"}
            self.logger.error(f"tool_call failed: {tool_name}", error=e, agent=agent_name)

        if not result.get("ok"):
            self.logger.warn(
                f"tool_result error: {tool_name}",
                agent=agent_name,
                tool=tool_name,
                error=result.get("error"),
            )

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
        """Run a single agent with timeout, retry, and observability."""
        full_prompt = f"session_id: {self.shared_session_id}\n\n{prompt}"
        timeout = AGENT_TIMEOUTS.get(agent_name, 300)
        criticality = AGENT_CRITICALITY.get(agent_name, "optional")

        self.logger.info(
            f"agent_start: {agent_name}",
            agent=agent_name,
            criticality=criticality,
            timeout_sec=timeout,
        )
        if self.tracker:
            self.tracker.start_agent(agent_name)

        start = time.time()

        try:
            # Retry on transient API errors (rate limits, 5xx, network)
            session = retry_call(
                lambda: self.client.beta.sessions.create(
                    agent=agent_id,
                    environment_id=ENVIRONMENT_ID,
                    title=f"{agent_name}_{self.shared_session_id}",
                ),
                max_attempts=3,
                initial_delay=2.0,
                is_retryable=is_retryable_anthropic_error,
                logger=self.logger,
                func_name=f"create_session_{agent_name}",
            )
        except Exception as e:
            self.logger.error(f"agent_start failed: {agent_name}", error=e, agent=agent_name)
            if self.tracker:
                self.tracker.end_agent(agent_name, status="failed", error=str(e))
            return {"output": "", "elapsed": 0, "tool_calls": 0, "error": str(e)}

        session_id = session.id

        final_text = ""
        tool_calls = 0
        usage = {}

        try:
            with self.client.beta.sessions.events.stream(session_id=session_id) as stream:
                self.client.beta.sessions.events.send(
                    session_id=session_id,
                    events=[{
                        "type": "user.message",
                        "content": [{"type": "text", "text": full_prompt}],
                    }],
                )

                for event in stream:
                    # Check timeout
                    if time.time() - start > timeout:
                        self.logger.error(
                            f"agent_timeout: {agent_name}",
                            agent=agent_name,
                            elapsed=time.time() - start,
                            limit=timeout,
                        )
                        break

                    etype = event.type

                    if etype == "agent.message":
                        for block in event.content:
                            if getattr(block, "type", None) == "text":
                                final_text += block.text
                    elif etype == "agent.custom_tool_use":
                        tool_calls += 1
                        self.handle_tool_call(event, session_id, agent_name)
                    elif etype == "session.status_idle":
                        stop_reason = getattr(event, "stop_reason", None)
                        if stop_reason and getattr(stop_reason, "type", None) == "requires_action":
                            continue
                        # Try to capture usage if available
                        if hasattr(event, "usage"):
                            usage = {
                                "input_tokens": getattr(event.usage, "input_tokens", 0),
                                "output_tokens": getattr(event.usage, "output_tokens", 0),
                                "cache_read_input_tokens": getattr(event.usage, "cache_read_input_tokens", 0),
                            }
                        break
                    elif etype == "session.status_terminated":
                        break
                    elif etype == "session.error":
                        err = getattr(event, "error", None)
                        self.logger.error(
                            f"session_error: {agent_name}",
                            agent=agent_name,
                            session_error=str(err),
                        )
                        break
        except Exception as e:
            self.logger.error(f"agent_run failed: {agent_name}", error=e, agent=agent_name)
            if self.tracker:
                self.tracker.end_agent(agent_name, status="failed", error=str(e))
            return {"output": final_text, "elapsed": time.time() - start, "tool_calls": tool_calls, "error": str(e)}

        elapsed = time.time() - start
        self.logger.info(
            f"agent_end: {agent_name}",
            agent=agent_name,
            elapsed_sec=round(elapsed, 2),
            tool_calls=tool_calls,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        )
        if self.tracker:
            self.tracker.end_agent(
                agent_name,
                status="success",
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            )

        return {
            "agent": agent_name,
            "session_id": session_id,
            "output": final_text,
            "elapsed": elapsed,
            "tool_calls": tool_calls,
            "usage": usage,
        }


# ============================================================================
# ORCHESTRATOR (Step-Based)
# ============================================================================
class OrchestratorV2:
    """
    Step-based orchestrator that can run end-to-end OR one step at a time.

    For local/long-running: call orchestrate() to run full pipeline.
    For Vercel (timeout-bound): call run_step(step_name) for each step.
    """

    def __init__(self, resume_session_id: Optional[str] = None):
        self.client = anthropic.Anthropic()
        if resume_session_id:
            existing = handle_get_events(session_id=resume_session_id)
            if existing.get("ok") and existing.get("events"):
                self.session_id = resume_session_id
                self.resumed = True
                event_types = [e["event_type"] for e in existing["events"]]
                print(f"\n>>> RESUMING session {self.session_id}", flush=True)
                print(f"    {len(existing['events'])} prior events: {event_types}", flush=True)
            else:
                print(f"\n>>> No prior events for {resume_session_id}; starting fresh", flush=True)
                self.session_id = self._new_session_id()
                self.resumed = False
        else:
            self.session_id = self._new_session_id()
            self.resumed = False

        self.logger = StructuredLogger(session_id=self.session_id)
        self.tracker = RunTracker(self.session_id)
        self.runner = AgentRunner(
            self.client, self.session_id,
            logger=self.logger, tracker=self.tracker,
        )

        self.logger.info(
            f"orchestrator_init",
            session_id=self.session_id,
            resumed=self.resumed,
        )

    @staticmethod
    def _new_session_id() -> str:
        return f"newsletter_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    def _has_event(self, event_type: str) -> bool:
        return has_event(self.session_id, event_type)

    def _count_events(self, event_type: str) -> int:
        return count_events(self.session_id, event_type)

    # ========================================================================
    # INDIVIDUAL STEPS (each runs independently, can be one Vercel fn each)
    # ========================================================================
    def step_memory(self):
        return self.runner.run(
            AGENTS["memory"], "memory",
            "Read past briefs (if any) and emit covered_topics event."
        )

    def step_research_parallel(self):
        self.logger.info("step_research_parallel_start")
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_l = executor.submit(self.runner.run, AGENTS["research_launches"], "research_launches",
                "Find 5-7 significant AI ecosystem developments from past 7 days "
                "(models, products, infra, hardware, $100M+ funding, big-tech AI moves). "
                "Emit launches_researched event."
            )
            f_p = executor.submit(self.runner.run, AGENTS["research_papers"], "research_papers",
                "Find 2-3 AI research findings from past 7 days that a working engineer or PM "
                "can act on (eval, prompting, agents, reliability, safety, efficiency, rag, finetune). "
                "Emit papers_researched event."
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

    def step_verify(self):
        return self.runner.run(
            AGENTS["verifier"], "verifier",
            "Read the latest draft_approved event. Call verify_links on the brief content. "
            "Emit verification_passed if all_valid; otherwise emit verification_failed with the invalid URLs."
        )

    def step_deliver(self):
        return self.runner.run(
            AGENTS["delivery"], "delivery",
            "Read draft_approved event, send email via send_email_smtp tool, emit email_sent event."
        )

    def check_critic_decision(self) -> bool:
        return self._has_event("draft_approved")

    # ========================================================================
    # STEP-BASED EXECUTION (one step per call, for Vercel)
    # ========================================================================
    def run_step(self, step_name: str) -> Dict[str, Any]:
        """
        Run one logical step. Used by Vercel to chain functions.

        Returns:
            {
                "step": str,
                "completed": bool,
                "next_step": Optional[str],
                "needs_retry": bool,  # for write_critique loop
                "results": dict,
                "session_id": str,
            }
        """
        self.logger.info(f"step_run: {step_name}", step=step_name)
        results: Dict[str, Any] = {}

        # Already complete check (idempotent)
        terminal_events = STEP_TERMINAL_EVENTS.get(step_name, [])
        if terminal_events and all(self._has_event(e) for e in terminal_events):
            self.logger.info(f"step_already_complete: {step_name}", step=step_name)
            return {
                "step": step_name,
                "completed": True,
                "next_step": self._get_next_step(step_name),
                "results": {"skipped": True},
                "session_id": self.session_id,
            }

        try:
            if step_name == "memory":
                results["memory"] = self.step_memory()

            elif step_name == "research":
                results["launches"], results["papers"] = self.step_research_parallel()
                # Post-condition fallbacks
                for required, key in (("launches_researched", "launches"),
                                       ("papers_researched", "papers")):
                    if not self._has_event(required):
                        self.logger.warn(
                            f"auto_insert_placeholder: {required}",
                            event_type=required,
                            reason="research agent did not emit terminal event",
                        )
                        handle_emit_event(
                            session_id=self.session_id,
                            agent_name="orchestrator",
                            event_type=required,
                            data={
                                key: [],
                                "note": "Auto-inserted by orchestrator: research agent did not emit a terminal event.",
                                "auto_inserted": True,
                            },
                        )

            elif step_name == "evaluate":
                results["evaluator"] = self.step_evaluate()

            elif step_name == "write_critique":
                results.update(self._run_write_critique_loop())

            elif step_name == "verify":
                results.update(self._run_verify_loop())

            elif step_name == "deliver":
                results["delivery"] = self.step_deliver()

            else:
                return {
                    "step": step_name,
                    "completed": False,
                    "error": f"Unknown step: {step_name}",
                    "session_id": self.session_id,
                }

            completed = all(self._has_event(e) for e in terminal_events) if terminal_events else True
            return {
                "step": step_name,
                "completed": completed,
                "next_step": self._get_next_step(step_name) if completed else None,
                "results": results,
                "session_id": self.session_id,
            }

        except Exception as e:
            self.logger.error(f"step_failed: {step_name}", error=e, step=step_name)
            return {
                "step": step_name,
                "completed": False,
                "error": str(e),
                "session_id": self.session_id,
            }

    def _get_next_step(self, current_step: str) -> Optional[str]:
        try:
            idx = STEP_ORDER.index(current_step)
            return STEP_ORDER[idx + 1] if idx + 1 < len(STEP_ORDER) else None
        except ValueError:
            return None

    def _run_write_critique_loop(self) -> Dict[str, Any]:
        """Writer/Critic loop, state-driven, max 2 retries."""
        results = {}
        max_retries = 2
        approved = self._has_event("draft_approved")
        if approved:
            return {"already_approved": True}

        loop_safety = 0
        while not approved and loop_safety < 10:
            loop_safety += 1
            drafts = self._count_events("draft_written")
            rejections = self._count_events("critic_rejection")

            if drafts == 0 or rejections >= drafts:
                if drafts > max_retries:
                    self.logger.warn("max_writer_retries_reached", retries=max_retries)
                    break
                attempt = drafts + 1
                retry_msg = ("Previous draft was rejected. Read critic_rejection event "
                             "and address all issues.") if drafts > 0 else ""
                self.logger.info(f"writer_attempt_{attempt}", attempt=attempt)
                results[f"writer_v{attempt}"] = self.step_write(retry_msg=retry_msg)
                continue

            attempt = rejections + 1
            self.logger.info(f"critic_attempt_{attempt}", attempt=attempt)
            results[f"critic_v{attempt}"] = self.step_critique()
            if self.check_critic_decision():
                self.logger.info(f"critic_approved", attempt=attempt)
                approved = True

        return results

    def _run_verify_loop(self) -> Dict[str, Any]:
        """
        Run Verifier; if it fails, loop back to Writer (which will re-emit a draft;
        Critic re-approves; Verifier re-checks). Bounded by MAX_VERIFICATION_RETRIES.
        """
        results = {}

        if self._has_event("verification_passed"):
            return {"already_verified": True}

        verify_attempts = 0
        while verify_attempts <= MAX_VERIFICATION_RETRIES:
            verify_attempts += 1
            self.logger.info(f"verifier_attempt_{verify_attempts}", attempt=verify_attempts)
            results[f"verifier_v{verify_attempts}"] = self.step_verify()

            if self._has_event("verification_passed"):
                self.logger.info("verification_passed", attempt=verify_attempts)
                return results

            if not self._has_event("verification_failed"):
                # Verifier did not emit anything terminal — treat as a soft fail and stop
                self.logger.warn("verifier_no_terminal_event", attempt=verify_attempts)
                break

            if verify_attempts > MAX_VERIFICATION_RETRIES:
                self.logger.warn("max_verification_retries_reached", attempts=verify_attempts)
                break

            # Verification failed — loop back through Writer/Critic to fix URLs
            self.logger.info(
                "verification_failed_looping_back_to_writer",
                attempt=verify_attempts,
            )
            results.update(self._run_write_critique_loop())

        return results

    # ========================================================================
    # FULL PIPELINE (for local/long-running execution)
    # ========================================================================
    def orchestrate(self):
        """Run full pipeline end-to-end. For local use."""
        self.logger.info("orchestrate_start")
        total_start = time.time()
        results: Dict[str, Any] = {}

        if self._has_event("email_sent"):
            self.logger.info("already_completed")
            print("\n>>> Session already completed (email_sent present). Nothing to do.", flush=True)
            return

        # Step 1: Memory
        if self._has_event("covered_topics"):
            self.logger.info("step_skipped: memory")
        else:
            results["memory"] = self.step_memory()

        # Step 2: Parallel Research
        if self._has_event("launches_researched") and self._has_event("papers_researched"):
            self.logger.info("step_skipped: research")
        else:
            results["launches"], results["papers"] = self.step_research_parallel()

        # Auto-insert placeholders for silent failures
        for required, key in (("launches_researched", "launches"),
                               ("papers_researched", "papers")):
            if not self._has_event(required):
                self.logger.warn(f"auto_insert_placeholder: {required}", event_type=required)
                handle_emit_event(
                    session_id=self.session_id,
                    agent_name="orchestrator",
                    event_type=required,
                    data={
                        key: [],
                        "note": "Auto-inserted by orchestrator.",
                        "auto_inserted": True,
                    },
                )

        # Step 3: Evaluator
        if self._has_event("items_evaluated"):
            self.logger.info("step_skipped: evaluate")
        else:
            results["evaluator"] = self.step_evaluate()

        # Step 4: Writer/Critic loop
        if not self._has_event("draft_approved"):
            results.update(self._run_write_critique_loop())

        if not self._has_event("draft_approved"):
            self.logger.error("did_not_reach_approval")
            print("\n>>> Did not reach approval. Stopping before delivery.", flush=True)
            self._finalize(results, success=False)
            return

        # Step 5: Verify (URL & arXiv hallucination check)
        if self._has_event("verification_passed"):
            self.logger.info("step_skipped: verify")
        else:
            results.update(self._run_verify_loop())

        if not self._has_event("verification_passed"):
            self.logger.error("verification_did_not_pass")
            print("\n>>> Verification did not pass. Stopping before delivery.", flush=True)
            self._finalize(results, success=False)
            return

        # Step 6: Delivery
        if self._has_event("email_sent"):
            self.logger.info("step_skipped: delivery")
        else:
            results["delivery"] = self.step_deliver()

        total = time.time() - total_start
        self.logger.info(f"orchestrate_complete", total_elapsed_sec=round(total, 2))
        print(f"\n>>> COMPLETE. Total: {total:.1f}s", flush=True)
        print(f">>> Session: {self.session_id}", flush=True)

        self._finalize(results, success=True)

    def _finalize(self, results: Dict[str, Any], success: bool):
        """Persist run summary and metadata."""
        self.tracker.persist()
        os.makedirs("briefs", exist_ok=True)
        log_path = f"briefs/{self.session_id}_log.json"
        with open(log_path, "w") as f:
            json.dump({
                "session_id": self.session_id,
                "resumed": self.resumed,
                "success": success,
                "summary": self.tracker.summary(),
                "agents": {n: {k: v for k, v in r.items() if k != "output"}
                          for n, r in results.items() if isinstance(r, dict)},
            }, f, indent=2)
        print(f">>> Log saved: {log_path}", flush=True)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-Agent Newsletter Orchestrator (step-based, decoupled).",
    )
    parser.add_argument(
        "--session-id",
        dest="session_id",
        default=None,
        help="Resume an existing newsletter session. Reads from Memory Stores JSONL.",
    )
    parser.add_argument(
        "--step",
        dest="step",
        default=None,
        choices=STEP_ORDER,
        help="Run only one step (for Vercel function chaining).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    orch = OrchestratorV2(resume_session_id=args.session_id)
    if args.step:
        result = orch.run_step(args.step)
        print(json.dumps(result, indent=2, default=str))
    else:
        orch.orchestrate()
