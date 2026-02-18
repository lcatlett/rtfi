# ADR-0005: HMAC-SHA256 Audit Trail (M5)

**Status:** Accepted
**Date:** 2026-02-16 (v1.0.0, M5)
**Context:** Enterprise compliance requires tamper-evident audit logging

## Decision

Sign every audit log entry with HMAC-SHA256 using a machine-specific key stored at `~/.rtfi/.audit_key` (mode 0600). Append `[sig:<hex>]` to each log line.

## Context

Enterprise environments require audit trails that can prove log entries haven't been modified after the fact. A simple append-only log file offers no integrity guarantees — any user with file access can edit entries.

## Alternatives Considered

1. **No signatures:** Simple append-only log
   - Rejected: no tamper detection for compliance requirements
2. **Hash chain:** Each entry includes hash of previous entry (blockchain-style)
   - Rejected: any single corrupted entry breaks verification of all subsequent entries
3. **Per-entry HMAC (selected):** Each entry independently verifiable
   - Corruption of one entry doesn't affect others
   - Simple `verify_audit_log()` function checks all entries
4. **External signing service:** Send entries to a timestamping authority
   - Rejected: adds network dependency, defeats local-first principle

## Consequences

- Key is auto-generated on first use (32 bytes from `os.urandom`)
- Key loss means existing signatures can't be verified (but entries remain readable)
- `verify_audit_log()` returns per-line results — partial corruption is detectable
- Key is machine-specific — logs moved between machines will fail verification
