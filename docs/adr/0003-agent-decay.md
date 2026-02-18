# ADR-0003: Agent Decay (5-Minute Window)

**Status:** Accepted
**Date:** 2026-02-16 (v0.3.0, H4)
**Context:** Preventing stale agent counts from inflating risk scores

## Decision

Agent spawns decay from the active agent count after 300 seconds (5 minutes). The `active_agents` property counts only spawns within the decay window.

## Context

The original implementation incremented `active_agents` on each `Task` tool call and never decremented it. Over a long session, the agent fanout factor would climb to 1.0 and stay there, even if agents had long since completed.

This caused false positives: sessions that spawned a few agents early would remain at elevated risk for the entire session duration.

## Alternatives Considered

1. **Track agent completion:** Decrement count when an agent finishes
   - Rejected: Claude Code hooks don't fire a dedicated "agent completed" event
2. **Fixed decay window (selected):** Use timestamp-based sliding window
   - 5 minutes chosen as reasonable upper bound for agent task duration
3. **Configurable decay:** Let users set the window
   - Deferred: added complexity for minimal user benefit. Can revisit if requested.

## Consequences

- Agent fanout factor naturally decreases over time, reducing false positives
- Short burst of agent spawns (the actual risk pattern) still scores high
- `tool_calls_timestamps` and `agent_spawn_timestamps` lists grow unbounded in theory, but `prune_old_timestamps()` trims entries older than the window
