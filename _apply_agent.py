#!/usr/bin/env python3
"""Apply agent_app/agent.yaml to the live Managed Agent via SDK."""
import os
import sys

import anthropic
import yaml

AGENT_ID = "agent_011CaaLRjtDQefQZ9oUTwbGV"


def main() -> int:
    yaml_path = os.path.join(os.path.dirname(__file__), "agent.yaml")
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)

    client = anthropic.Anthropic()
    current = client.beta.agents.retrieve(AGENT_ID)
    print(f"Current agent: name={current.name!r} version={current.version}", file=sys.stderr)

    update_kwargs = {
        "name": cfg["name"],
        "description": cfg.get("description"),
        "model": cfg["model"],
        "system": cfg["system"],
        "tools": cfg.get("tools", []),
        "mcp_servers": cfg.get("mcp_servers", []),
        "skills": cfg.get("skills", []),
        "metadata": cfg.get("metadata", {}),
    }

    updated = client.beta.agents.update(AGENT_ID, version=current.version, **update_kwargs)
    print(f"Updated agent: name={updated.name!r} version={updated.version}", file=sys.stderr)
    print(f"Tools: {[t.get('name') or t.get('type') for t in (updated.tools or [])]}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
