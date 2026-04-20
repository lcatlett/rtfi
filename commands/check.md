---
name: check
description: Validate whether a session followed its declared constraints — produces a per-constraint PASS/FAIL report with exact violation locations
allowed-tools:
  - Bash
argument-hint: "<session-id> | --latest [--constraints <file>] [--json]"
---

# RTFI Compliance Check

Replays a session's event sequence and checks each event against declared constraints (max agents, confirm interval, context guard, risk threshold). Produces a concrete report showing which instruction was violated at which step, the risk score decomposition, and the exact warning RTFI sent to Claude.

## Instructions

Run the compliance checker:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/demo_compliance_check.py" $ARGUMENTS
```

Read the output and summarize:
- **Verdict**: COMPLIANT or NON-COMPLIANT
- **Which constraints failed** and at which step
- **Primary risk driver** from the score decomposition (whichever factor contributed the most points)
- **What RTFI told Claude** at the threshold breach moment (the systemMessage)
- **Artifact compliance** (PASS / FAIL / N/A): lists the expected-vs-missing artifacts
  that RTFI's Stop hook checked for. Configure via `RTFI_EXPECTED_ARTIFACTS`
  (colon-separated paths, relative to project dir). Default: disabled (N/A).

If the user wants to adjust the constraints being checked, they can create a JSON file like:

```json
[
  {"id": "C1", "name": "Max 2 parallel agents", "max_agents": 2},
  {"id": "C2", "name": "Confirm every 5 steps", "max_steps_without_confirm": 5},
  {"id": "C3", "name": "Context guard", "max_tokens": 80000},
  {"id": "C4", "name": "Risk threshold", "max_risk_score": 70.0}
]
```

## Usage Examples

- `/rtfi:check --latest` — check the most recent session
- `/rtfi:check b00b08a9` — check a specific session by ID prefix
- `/rtfi:check --latest --json` — machine-readable output
- `/rtfi:check --latest --constraints my_constraints.json` — custom constraint set
