# RTFI Solution Architecture

**Real-Time Instruction Compliance Risk Scoring for LLM Sessions**

---

## Core Insight

Shift from "fix AI behavior" (impossible) to "manage AI risk" (tractable). Predict when instruction non-compliance is likely and insert human checkpoints before failures occur.

---

## Architecture Overview

```
┌─────────────────────── Integration Layer ────────────────────────┐
│  Claude Code Hooks │ Cursor Plugin │ API Proxy │ GitHub Actions  │
└────────────────────────────────┬─────────────────────────────────┘
                                 ▼
┌─────────────────────── Event Stream ─────────────────────────────┐
│           tool.pre | tool.post | agent.spawn | session.*         │
└────────────────────────────────┬─────────────────────────────────┘
                                 ▼
┌─────────────────────── Risk Scoring Engine ──────────────────────┐
│  Context Meter │ Agent Monitor │ Autonomy Tracker │ Aggregator   │
│                              → Risk Score (0-100)                │
└────────────────────────────────┬─────────────────────────────────┘
                                 ▼
┌─────────────────────── Action Dispatcher ────────────────────────┐
│        Alert │ Checkpoint (confirm) │ Block (pause) │ Log        │
└──────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Deterministic risk scoring** | No LLM in critical path - avoids recursive compliance problem, sub-10ms latency |
| **Hooks-first integration** | Zero adoption friction for Claude Code users, immediate value |
| **Event-driven architecture** | Decouples scoring from execution, enables async analysis |
| **Local-first storage** | Privacy-preserving, no cloud dependency for MVP |

---

## Risk Score Formula

Based on [MOSAIC benchmark](https://arxiv.org/html/2601.18554) research on factors affecting instruction compliance:

```python
def calculate_risk_score(session_state):
    weights = {
        'context_length': 0.25,    # Tokens in context
        'agent_fanout': 0.30,      # Concurrent agents (highest weight)
        'autonomy_depth': 0.25,    # Steps since human confirmation
        'decision_velocity': 0.20  # Tool calls per minute
    }

    factors = {
        'context_length': min(1.0, tokens / 128000),
        'agent_fanout': min(1.0, active_agents / 5),
        'autonomy_depth': min(1.0, steps_since_confirm / 10),
        'decision_velocity': min(1.0, tools_per_minute / 20)
    }

    return sum(factors[k] * weights[k] for k in weights) * 100
```

**Agent fanout weighted highest** - parallel agent spawning is the riskiest factor for instruction non-compliance per MOSAIC research.

---

## Full Architecture (Post-MVP)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RTFI Platform                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────── Integration Layer ──────────────────────────┐  │
│  │                                                                        │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │  │
│  │  │  Claude  │ │ Cursor   │ │ API      │ │ GitHub   │ │ Custom   │    │  │
│  │  │  Code    │ │ Plugin   │ │ Proxy    │ │ Actions  │ │ SDK      │    │  │
│  │  │  Hooks   │ │          │ │          │ │          │ │          │    │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘    │  │
│  │       └────────────┴────────────┴────────────┴────────────┘          │  │
│  └────────────────────────────────────┬─────────────────────────────────┘  │
│                                       ▼                                     │
│  ┌────────────────────────── Event Bus (NATS/Redis) ────────────────────┐  │
│  │  Topics: tool.pre | tool.post | agent.spawn | session.* | alert.*    │  │
│  └────────────────────────────────────┬─────────────────────────────────┘  │
│                                       │                                     │
│          ┌────────────────────────────┼─────────────────────────┐          │
│          ▼                            ▼                         ▼          │
│  ┌───────────────┐          ┌────────────────┐         ┌───────────────┐  │
│  │ Risk Scoring  │          │ Instruction    │         │ Behavior      │  │
│  │ Engine        │          │ Registry       │         │ Logger        │  │
│  │               │          │                │         │               │  │
│  │ • Context     │          │ • CLAUDE.md    │         │ • Full trace  │  │
│  │ • Fanout      │          │   parser       │         │ • Context     │  │
│  │ • Autonomy    │          │ • Sys prompt   │         │   snapshots   │  │
│  │ • Velocity    │          │   extractor    │         │ • Tool I/O    │  │
│  │               │          │ • Criticality  │         │               │  │
│  └───────┬───────┘          │   classifier   │         └───────┬───────┘  │
│          │                  └────────────────┘                 │          │
│          │                                                     │          │
│          └─────────────────────────┬───────────────────────────┘          │
│                                    ▼                                       │
│                          ┌────────────────────┐                            │
│                          │   PostgreSQL +     │                            │
│                          │   TimescaleDB      │                            │
│                          │   (events, scores, │                            │
│                          │    instructions)   │                            │
│                          └─────────┬──────────┘                            │
│                                    │                                       │
│                                    ▼                                       │
│  ┌────────────────────────── Analytics Layer ───────────────────────────┐  │
│  │                                                                        │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │  │
│  │  │  Dashboard   │  │  Compliance  │  │  Risk Model  │                │  │
│  │  │  (Grafana)   │  │  Reports     │  │  Trainer     │                │  │
│  │  │              │  │  (scheduled) │  │  (feedback)  │                │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘                │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## MVP Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│                     Claude Code Session                       │
│                                                               │
│  User Prompt → [PreToolUse Hook] → Tool Execution             │
│                      │                    │                   │
│                      ▼                    ▼                   │
│              ┌───────────────┐    [PostToolUse Hook]          │
│              │ RTFI Tracker  │           │                    │
│              │  (sidecar)    │◀──────────┘                    │
│              └───────┬───────┘                                │
│                      │                                        │
│                      ▼                                        │
│              ┌───────────────┐                                │
│              │ Risk Score    │ → if > threshold → [Stop Hook] │
│              │ Calculator    │                    (checkpoint)│
│              └───────────────┘                                │
└──────────────────────────────────────────────────────────────┘
```

---

## Data Model

### Sessions Table
```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    instruction_source TEXT,
    instruction_hash TEXT,
    final_risk_score REAL,
    peak_risk_score REAL DEFAULT 0,
    total_tool_calls INTEGER DEFAULT 0,
    total_agent_spawns INTEGER DEFAULT 0,
    outcome TEXT DEFAULT 'in_progress'
);
```

### Risk Events Table
```sql
CREATE TABLE risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    context_tokens INTEGER DEFAULT 0,
    risk_score_total REAL,
    risk_score_factors TEXT,
    threshold_exceeded BOOLEAN DEFAULT FALSE,
    metadata JSON
);
```

### Instructions Table (Phase 2)
```sql
CREATE TABLE instructions (
    id INTEGER PRIMARY KEY,
    source_file TEXT,
    instruction_text TEXT,
    instruction_type TEXT,    -- 'behavioral', 'procedural', 'safety'
    criticality TEXT,         -- 'must', 'should', 'may'
    created_at TIMESTAMP
);
```

### Compliance Events Table (Phase 2)
```sql
CREATE TABLE compliance_events (
    id INTEGER PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    instruction_id INTEGER REFERENCES instructions(id),
    assessment TEXT,          -- 'followed', 'violated', 'unclear'
    evidence TEXT,
    assessed_at TIMESTAMP
);
```

---

## Instruction Registry Options

### Option A: Schema-based (requires format change)
- **Pros:** Deterministic parsing, no LLM needed
- **Cons:** Requires user adoption, breaking change

### Option B: LLM-based extraction (recursive problem)
- **Pros:** Works with natural language
- **Cons:** Introduces compliance uncertainty in the compliance tool

### Option C: Hybrid (Recommended)
- Parse markdown headers, numbered lists, imperative verbs
- Fall back to small classifier model for ambiguous cases
- Use confidence thresholds to decide when human review needed

---

## Technical Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Score correlation unvalidated** | High | A/B testing with post-hoc compliance analysis, continuous model refinement |
| **False positives annoy users** | Medium | Configurable thresholds, learning mode that observes without blocking |
| **Hook overhead slows Claude Code** | Medium | Async event emission, batch processing, ~10ms budget per hook |
| **Instruction parsing fails on ambiguous language** | Medium | Start with structured hints, flag low-confidence cases for human review |
| **Anthropic changes hook API** | Low | Abstract integration layer, version compatibility checks |
| **Users disable for convenience** | Medium | Audit log shows when disabled, compliance reports highlight gaps |

---

## Phased Rollout

| Phase | Scope | Deliverables |
|-------|-------|--------------|
| **0 - Prototype** | CLI tool + SQLite, manual threshold | Risk scoring engine, basic CLI |
| **1 - MVP** | Claude Code hooks, configurable thresholds, local logging | Hook integration, session tracking |
| **2 - Analytics** | Dashboard, session history, pattern detection | Grafana dashboard, compliance reports |
| **3 - Multi-platform** | Cursor, API proxy, enterprise features | SDK, API proxy, Instruction Registry |

---

## Applied to the Original Failure Case

For the research failure described in PRODUCT-BRIEF.md (11 agents spawned without confirmation, 42 unusable documents):

| Factor | Value | Risk Contribution |
|--------|-------|-------------------|
| Agent fan-out | 11 parallel agents | **High** (0.30 × 1.0 = 30) |
| Context load | Large (PRD + research) | **High** (0.25 × ~0.8 = 20) |
| Autonomy depth | Multiple steps without confirmation | **High** (0.25 × 1.0 = 25) |
| Decision velocity | Many tool calls before verification | **Medium** (0.20 × ~0.5 = 10) |

**Calculated Risk Score: ~85/100** (threshold: 70)

**Result:** Risk threshold exceeded before agents spawned. System would have paused and required: "Confirm methodology is correct before spawning 11 agents."

---

## Key Differentiation

| Existing Tools | RTFI Approach |
|----------------|---------------|
| Monitor outputs after the fact | Predict risk before failure |
| Detect hallucinations in content | Detect conditions that cause non-compliance |
| Retroactive quality assessment | Proactive intervention points |
| "Was the output good?" | "Is the session at risk of ignoring instructions?" |

---

*Generated from thinkdeep analysis of PRODUCT-BRIEF.md, 2026-01-31*
