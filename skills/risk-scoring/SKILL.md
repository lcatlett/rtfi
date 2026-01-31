---
name: risk-scoring
description: Use when discussing RTFI risk scores, compliance thresholds, or tuning risk factors. Provides guidance on interpreting scores and optimizing settings.
version: 1.0.0
---

# RTFI Risk Scoring

RTFI calculates a Compliance Risk Score (0-100) based on measurable session factors. Higher scores indicate higher probability of instruction non-compliance.

## Risk Factors

| Factor | Weight | Description | Threshold |
|--------|--------|-------------|-----------|
| **Context Length** | 25% | Tokens in context relative to max | 128,000 |
| **Agent Fanout** | 30% | Concurrent parallel agents | 5 |
| **Autonomy Depth** | 25% | Steps since human confirmation | 10 |
| **Decision Velocity** | 20% | Tool calls per minute | 20 |

Agent fanout has the highest weight because MOSAIC research shows parallel execution is the highest-risk factor for instruction non-compliance.

## Score Interpretation

| Score Range | Risk Level | Recommendation |
|-------------|------------|----------------|
| 0-30 | Low | Normal operation |
| 31-50 | Moderate | Monitor closely |
| 51-70 | Elevated | Consider verification checkpoints |
| 71-85 | High | Strong recommendation to verify |
| 86-100 | Critical | High probability of instruction violation |

## Configuring Thresholds

Create `.claude/rtfi.local.md`:

```markdown
# RTFI Settings

## Threshold
Risk score threshold for alerts (0-100): 70

## Action Mode
What happens when threshold exceeded: alert
Options: alert, block, confirm
```

### Action Modes

- **alert** (default): Show warning, continue execution
- **confirm**: Pause and ask user to confirm before continuing
- **block**: Deny the tool call that triggered threshold

## Tuning Recommendations

### Lower threshold (50-60) when:
- Working with critical instructions that must not be violated
- Using many parallel agents
- Session involves safety-critical operations
- You've experienced instruction non-compliance issues

### Higher threshold (80-90) when:
- Instructions are flexible/advisory
- You want fewer interruptions
- Working on exploratory tasks
- You regularly verify outputs manually

## Reducing Risk Score

To lower your session's risk score:

1. **Add checkpoints** - Ask for confirmation at key decision points
2. **Limit parallel agents** - Spawn fewer concurrent agents
3. **Break up long sessions** - Start fresh when context grows large
4. **Reduce tool velocity** - Slow down rapid-fire tool execution

## Understanding Alerts

When RTFI alerts:

```
RTFI WARNING: Risk score 75.2 exceeds threshold 70.
Factors: context=0.45, agents=0.60, autonomy=0.80, velocity=0.30
High probability of instruction non-compliance.
```

This means:
- Context is at 45% of max (moderate contribution)
- 3 agents active (60% of 5 max, significant contribution)
- 8 steps since confirmation (80% of 10 max, major contribution)
- 6 tool calls/min (30% of 20/min max, minor contribution)

The primary driver here is autonomy depth - consider adding a checkpoint.

## Session Analysis

Use `/rtfi:sessions` and `/rtfi:risky` to review patterns:

- Which workflows trigger high risk?
- What factors dominate your high-risk sessions?
- Are alerts correlating with actual instruction violations?

This data helps calibrate your threshold for optimal balance between safety and productivity.
