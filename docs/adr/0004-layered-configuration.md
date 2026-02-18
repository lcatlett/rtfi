# ADR-0004: Layered Configuration (M6)

**Status:** Accepted
**Date:** 2026-02-16 (v1.0.0, M6)
**Context:** Replacing fragile markdown settings parsing with a proper config system

## Decision

Use a layered configuration system with four priority levels:

1. Environment variables (`RTFI_*`) — highest priority
2. Config file (`~/.rtfi/config.env`) — key=value format
3. Legacy settings file (`.claude/rtfi.local.md`) — backward compatibility
4. Built-in defaults — lowest priority

## Context

The original settings system parsed markdown files for key-value pairs using string splitting on `:`. This was fragile:
- Colons in values broke parsing
- No standard format — users had to match exact prose ("Risk score threshold:")
- No validation of parsed values
- No way to override per-environment without editing files

## Alternatives Considered

1. **TOML/YAML config:** More structured, supports nested config
   - Rejected: adds a dependency (tomli for Python 3.10) or stdlib YAML (doesn't exist)
2. **JSON config:** stdlib-supported, structured
   - Rejected: painful to hand-edit, no comments
3. **`.env` format (selected):** Simple key=value, supports comments with `#`, no dependencies
   - Matches common ops tooling (Docker, systemd, etc.)
4. **Python config file:** Maximum flexibility
   - Rejected: security risk (arbitrary code execution)

## Consequences

- Legacy `.claude/rtfi.local.md` still works (backward compatible)
- Environment variables override everything — standard for CI/CD and containers
- Config file is human-readable and editable with any text editor
- `setup` command generates a commented default config
