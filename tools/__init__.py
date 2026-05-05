"""
Tools (Hands) for AI Weekly orchestrator.

Each tool is an independent "hand" that can fail/be replaced independently.
Aligns with Anthropic's "Scaling Managed Agents" decoupling pattern:
- Each tool exposes the same execute(input) -> string interface
- No coupling between tools
- Each can be tested, retried, or replaced independently

Tools:
- memory_store: emit_event, get_events (Memory Stores access)
- email: send_email_smtp (SMTP delivery)
"""

from tools.memory_store import handle_emit_event, handle_get_events
from tools.email import handle_send_email_smtp
from tools.subscribers import get_subscribers

__all__ = [
    "handle_emit_event",
    "handle_get_events",
    "handle_send_email_smtp",
    "get_subscribers",
]
