# ADR-0002: Fresh Process Per Hook with DB State Hydration

**Status:** Accepted
**Date:** 2026-02-05 (C1 fix)
**Context:** Managing session state across hook invocations

## Decision

Hydrate session state from SQLite on each hook invocation and persist it back after mutation. Accept the constraint that each hook runs as a fresh Python process.

## Context

Claude Code spawns a new process for each hook invocation. This means:
- No in-memory state survives between calls
- Session state (tool timestamps, agent spawn times, token counts) must be externalized
- The original implementation assumed in-memory state persistence, causing risk scores to reset to zero on every call (C1 bug)

## Alternatives Considered

1. **Long-running daemon:** A persistent process that hooks connect to via IPC
   - Rejected: adds deployment complexity, crash recovery concerns, port conflicts
2. **File-based state:** JSON files per session
   - Rejected: no ACID guarantees, race conditions with concurrent hooks
3. **SQLite `session_state` column:** Serialize `SessionState` to JSON in the sessions table
   - **Selected:** atomic writes, already have SQLite, simple to implement

## Consequences

- Every hook invocation pays ~5ms for DB read + write (acceptable within 50ms budget)
- `SessionState.to_dict()` / `from_dict()` must handle backward compatibility when fields change
- State is durable across process crashes — no data loss
