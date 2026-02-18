# ADR-0001: Deterministic Risk Scoring (No LLM in Critical Path)

**Status:** Accepted
**Date:** 2026-01-31
**Context:** Choosing how to calculate compliance risk scores

## Decision

Use a deterministic weighted formula based on observable session factors instead of an LLM-based assessment.

## Context

RTFI needs to evaluate whether a Claude Code session is at risk of ignoring user instructions. Two approaches were considered:

1. **LLM-based:** Feed session context to a model and ask "is this session likely to ignore instructions?"
2. **Deterministic formula:** Calculate a score from measurable factors (context length, agent count, autonomy depth, velocity)

## Rationale

- **Recursive compliance problem:** Using an LLM to check LLM compliance means the checker itself might ignore instructions
- **Latency:** Hook handlers must complete in <50ms. LLM calls take seconds.
- **Cost:** Every tool call would require an API call, making the plugin expensive
- **Predictability:** Deterministic scores are reproducible and debuggable
- **Research basis:** MOSAIC benchmark identifies the specific factors that correlate with instruction non-compliance

## Consequences

- Risk scores reflect statistical likelihood, not actual compliance assessment
- Cannot detect semantic instruction violations (e.g., "use TypeScript" but agent writes JavaScript)
- Instruction Registry (Phase 6) will add semantic analysis as a complementary layer
