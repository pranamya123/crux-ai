# AI Weekly: Multi-Agent Newsletter on Managed Agents

## Project Goal

Apply Anthropic's Managed Agents to build a production-grade multi-agent newsletter system, learning agentic engineering patterns and forming opinions on when Managed Agents matter.

## TL;DR

Built a 7-agent newsletter system using Managed Agents + shared session pattern (Supabase as event log).

**Key learning:** Managed Agents shines when you have multiple specialized brains coordinating through durable shared state. Single-agent systems don't need it.

---

## Architecture Evolution

### V0: Single Agent (Starting Point)

```
Trigger -> Orchestrator -> Single Agent (does everything) -> Email
```

**Problems:**
- One agent does research + writing + delivery (no specialization)
- Quality issues require restarting whole flow
- Hard to extend or reuse
- Doesn't leverage Managed Agents benefits beyond hosting

### V1: Multi-Agent, Orchestrator-Mediated

```
Orchestrator
  +-- Memory Agent (own session)
  +-- Research Launches (own session)
  +-- Research Papers (own session)
  +-- Evaluator (own session)
  +-- Writer (own session)
  +-- Critic (own session)
  +-- Delivery (own session)

Coordination: orchestrator passes data via initial messages
```

**Improvements:**
- Specialized agents (one job each)
- Parallel research execution
- Critic feedback loop
- Cost optimized (Haiku for simple, Opus for complex)

**Limitation:** Each agent has its own session. No shared event log. Orchestrator mediates all data passing.

### V2: Shared Session Pattern (Final)

```
                +------------------+
                |  Supabase        |
                |  session_events  |
                |  (shared log)    |
                +--------+---------+
                         |
        All 7 agents read/write via custom tools
                         |
        +-------+--------+--------+--------+
        |       |        |        |        |
     Memory  Research  Evaluator  Writer  Critic  Delivery
              (parallel)
```

**Key change:** Custom tools `emit_event` and `get_events` give all agents access to a shared session log in Supabase.

---

## Components

### Agents (7 total)

| Agent | Model | Role |
|-------|-------|------|
| 1. Memory | Haiku 4.5 | Read past briefs, emit covered_topics |
| 2. Research Launches | Opus 4.7 | Find AI launches, emit launches_researched |
| 3. Research Papers | Opus 4.7 | Find AI papers, emit papers_researched |
| 4. Evaluator | Haiku 4.5 | Score & rank, emit items_evaluated |
| 5. Writer | Opus 4.7 | Write brief, emit draft_written |
| 6. Critic | Opus 4.7 | Review, emit draft_approved or critic_rejection |
| 7. Delivery | Haiku 4.5 | Send email, emit email_sent |

### Custom Tools (handled by orchestrator)

- `emit_event(session_id, event_type, data)` - Write to session log
- `get_events(session_id, agent_name?, event_type?)` - Read prior events
- `send_email_smtp(subject, body_markdown)` - SMTP delivery

### Shared Session Log (Supabase)

```sql
CREATE TABLE session_events (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL,
  agent_name TEXT NOT NULL,
  event_type TEXT NOT NULL,
  data JSONB NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);
```

Every agent's output is an event row. Read the entire newsletter generation flow with one SQL query.

---

## Workflow

```
1. Generate session_id (UUID)
2. Memory Agent reads past briefs, emits covered_topics
3. Research Launches + Research Papers run in parallel
   (both pass session_id, both emit events)
4. Evaluator gets all prior events, scores items, emits items_evaluated
5. Writer reads evaluator's selections, writes draft, emits draft_written
6. Critic reads draft, decides APPROVE or REJECT (loop max 2 retries)
7. Delivery reads approved draft, sends email, emits email_sent
```

---

## Costs (per weekly run)

| Agent | Model | Est. Cost |
|-------|-------|-----------|
| Memory | Haiku | $0.20 |
| Research Launches | Opus | $2.00 |
| Research Papers | Opus | $2.00 |
| Evaluator | Haiku | $0.30 |
| Writer | Opus | $2.50 |
| Critic | Opus | $1.50 |
| Delivery | Haiku | $0.20 |
| **Total** | | **~$8.70/run** |

Monthly (4 runs): ~$35.

**Comparison:**
- Single agent v0: ~$6/run = $24/month
- Multi-agent v2: ~$8.70/run = $35/month
- Cost increase: 45% for significantly better architecture

---

## Opinions on Managed Agents

After building this, here's what I learned about when Managed Agents matter:

### When Managed Agents shines

1. **Multi-agent systems with specialization.** Each agent has one job. Reusable, debuggable, swappable.

2. **Long-horizon tasks needing durable state.** Session log persists across failures. Resume from last successful event instead of restarting.

3. **When you need parallel cognitive work.** Research agents running concurrently is significantly faster and cheaper than sequential.

4. **Critic and feedback loops.** Quality gates between agents catch errors before they propagate.

5. **Hot-swappable components.** Want a better evaluator? Just update Agent 4. Other agents don't change.

### When Managed Agents is overkill

1. **Single-shot tasks.** A summarizer needs one agent, not a hosted runtime.

2. **Workflows you fully control.** If your logic is deterministic (not agent-driven), simpler frameworks work.

3. **Low-stakes outputs.** No need for critic loops or memory if output quality doesn't matter.

### The session-as-state insight

The most powerful concept is treating the session as **durable shared state**, not just a conversation log. This is similar to event sourcing in distributed systems.

In my implementation, I used Supabase as the shared event store because the SDK doesn't yet support multi-agent sessions natively. This works well, but it's a workaround. The pure vision: ONE Managed Agents session that all agents read/write to via getEvents/emitEvent.

### What's missing in my implementation

1. **Human-in-the-loop approval.** Production should have a manual approval gate before delivery.

2. **Observability.** Tracing, metrics on agent performance over time.

3. **A/B testing.** Run two writer versions, compare quality.

4. **Memory across runs.** Currently memory is per-session. Long-term memory store (RAG over past briefs) would be better.

5. **Cost per agent monitoring.** Know which agent burns the most tokens.

---

## Files

- `orchestrator_v2.py` - Main multi-agent orchestrator (shared session)
- `multi_agent_orchestrator.py` - V1 orchestrator (separate sessions)
- `app.py` - Flask app for subscribers (Vercel-deployed)
- `agent.yaml` - Single-agent config (V0, archived)
- `briefs/` - Run logs and generated briefs

## How to run

```bash
cd ~/Desktop/agent_app
python3 orchestrator_v2.py
```

Then check the events:
```sql
SELECT * FROM session_events WHERE session_id = 'newsletter_...' ORDER BY created_at;
```

## Conclusion

The hardest part wasn't building agents. It was understanding when each architectural pattern matters. Managed Agents is a tool for systems that need **specialization + coordination + durable state**. If you don't need all three, you don't need Managed Agents.

For the AI Weekly newsletter specifically, multi-agent is overkill (1 subscriber doesn't justify $35/month). But as a learning exercise, it's the right project: enough complexity to exercise every Managed Agents pattern without overwhelming the architecture.

