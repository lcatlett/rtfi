# ADR-0007: Checkpoint Detection for Autonomy Depth Reset

**Status:** Accepted
**Date:** 2026-03-22
**Context:** The autonomy depth factor tracks how many tool calls occur without user confirmation. In production, this counter grew unbounded — there was no mechanism to detect when the user actually confirmed an action, so the risk score would climb monotonically even when the user was actively engaged.

## Decision

Implement a two-mechanism checkpoint detection system:

1. **Auto-detection (primary):** During `PostToolUse` hook processing, detect tool names that represent user confirmation — specifically `AskUserQuestion` and tools containing `user_prompt` or `confirm`. When detected, reset `steps_since_confirm` to zero.
2. **Manual fallback:** Provide `/rtfi:checkpoint` slash command that users or other skills can invoke to explicitly reset the autonomy depth counter.

## Rationale

- **AskUserQuestion is the natural signal:** Claude Code's `AskUserQuestion` tool is the canonical way an agent pauses for user input. If this tool was used, the user confirmed something — autonomy depth should reset.
- **No new hook events needed:** Detection works within the existing `PostToolUse` hook, requiring no changes to Claude Code's hook contract.
- **Manual escape hatch:** Some confirmation patterns don't use `AskUserQuestion` (e.g., a user typing a response to a direct question). The `/rtfi:checkpoint` command covers these cases without over-engineering auto-detection.
- **Conservative matching:** Only well-known tool names reset the counter. Unknown tools default to incrementing depth, erring on the side of higher risk scores.

## Consequences

- Risk scores now stabilize during interactive sessions instead of climbing indefinitely
- The compliance checker (`/rtfi:check`) can verify whether checkpoints occurred at appropriate intervals
- Sessions with genuine autonomous runs (no user interaction) still show escalating autonomy depth as intended

## Alternatives Considered

- **Hook into user input directly:** Would require a new hook event type from Claude Code (not available).
- **Time-based decay:** Reset depth after N seconds of inactivity. Rejected because inactivity doesn't imply user confirmation — the agent might just be slow.
- **Prompt-based detection:** Parse the conversation for user messages. Rejected as fragile and not available in hook data.
