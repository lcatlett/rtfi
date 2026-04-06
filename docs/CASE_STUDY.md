# Case Study: Silent Safety Regression via Plugin Cache Drift

## Summary

On 2026-03-28, RTFI v1.2.0 was confirmed to be running v1.1.0 hook logic in
production sessions due to a Claude Code plugin cache bug. The system reported
compliant risk scores using an outdated 4-factor model while the installed
version had shipped a 5-factor model with new displacement scoring. No errors
were raised. Sessions that should have triggered threshold warnings passed
silently.

## Timeline

| Date | Event |
|------|-------|
| 2026-03-23 | RTFI v1.2.0 shipped — full architecture overhaul (PR #1). Consolidated 7+ modules into `rtfi_core.py`, added instruction displacement as 5th risk factor. |
| 2026-03-23 | Plugin installed via local marketplace (`claude plugin install`). Cache directory created at `~/.config/claude/plugins/cache/`. |
| 2026-03-28 | Discovered `$CLAUDE_PLUGIN_ROOT` in agent Bash resolved to cache directory containing **v1.1.0** files — pre-overhaul code, old module paths, 4-factor scoring model. |
| 2026-03-28 | Cache files manually deleted. Switched to `--plugin-dir` for direct source loading. |

## What Went Wrong

### The Bug: anthropics/claude-code#38699

Claude Code v2.1.83 introduced a cache-only session-start loader (`Sc_`) that
skips the `copyToCache` step for string-source plugins (local marketplace
installs). Two consequences:

1. **Cache never populated** — files from the current version are never copied
2. **Cache never refreshed** — old version files persist across plugin updates

The `installPath` parameter is available but only used for object-source plugins
(github/git/npm). String-source plugins take a branch that resolves directly
from the marketplace source directory, ignoring the cache entirely for
hooks/skills — while the agent's Bash environment correctly points to the
(empty or stale) cache.

### The Failure Chain

```
Plugin update to v1.2.0
  → copyToCache skipped (bug)
  → Cache retains v1.1.0 files
  → Hooks execute from cache
  → v1.1.0 hook logic runs
  → 4-factor scoring (missing displacement)
  → Old thresholds, old weights
  → Sessions score lower than they should
  → No threshold warnings triggered
  → Operator sees "compliant" sessions
  → False confidence in safety posture
```

## What Was Running vs. What Should Have Been

| Component | v1.1.0 (cached) | v1.2.0 (installed) |
|-----------|-----------------|-------------------|
| Module structure | 7+ separate Python modules | Single consolidated `rtfi_core.py` |
| Risk factors | 4 (context, fanout, autonomy, velocity) | 5 (+ instruction displacement) |
| Weight distribution | 0.25 / 0.35 / 0.25 / 0.15 | 0.20 / 0.30 / 0.25 / 0.15 / 0.10 |
| Displacement tracking | Not present | Token estimation, skill/instruction ratio |
| Dashboard | 4-factor gauge bars, square radar chart | 5-factor bars, pentagonal radar |

## Why This Matters for Safety Tools

A monitoring tool that silently degrades is worse than no monitoring tool:

- **No tool** — the operator knows they have no coverage and acts accordingly
- **Stale tool** — the operator trusts scores that are computed with the wrong
  model, missing factors, and outdated thresholds

RTFI is specifically designed to detect instruction non-compliance. The
instruction displacement factor was added because skill prompts (177KB+) were
observed displacing CLAUDE.md instructions (10KB) in long sessions. With the
cache running v1.1.0, the very risk factor built to detect this class of
failure was itself absent — a safety tool blind to the problem it was built to
find.

### The Compounding Problem

The cache bug interacts with RTFI's hook-based architecture in a particularly
dangerous way:

1. **Hooks fire without errors** — the old code is valid Python, it just
   computes the wrong scores
2. **Dashboard shows data** — sessions appear in the UI with scores, events,
   and factor breakdowns
3. **Scores look reasonable** — a 4-factor score of 45 doesn't look obviously
   wrong compared to a 5-factor score of 52
4. **No version indicator** — the dashboard doesn't display which version of
   the scoring model produced the results

## Mitigations Applied

### Immediate

- Deleted stale cache files manually
- Switched to `claude --plugin-dir` for direct source loading (bypasses cache)

### Recommended

1. **Always use `--plugin-dir` during development** — never rely on cache for
   local plugins
2. **Add version to hook output** — include the RTFI version in every database
   write so stale scoring is identifiable after the fact
3. **Health check on startup** — `/rtfi:health` should verify the running hook
   code matches the installed version
4. **Consider `"source": "directory"` marketplace entries** — loads in-place
   without cache copy

## Lessons

1. **Cache invalidation is a safety problem, not just a convenience problem.**
   When the cached artifact is a safety monitor, staleness means undetected
   risk.

2. **Silent failures are the most dangerous failures.** The system continued to
   produce output — just the wrong output. An outright crash would have been
   caught immediately.

3. **Safety tools need self-verification.** A risk scoring system should be able
   to answer "am I running the version I think I'm running?" on every session
   start.

4. **The tool designed to detect displacement was itself displaced.** The
   instruction displacement factor — built because skill prompts crowd out
   standing instructions — was missing from scoring because the cache crowded
   out the current code. Same class of problem, different layer of the stack.
