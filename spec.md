 Root cause: Claude Code only injects $CLAUDE_PLUGIN_ROOT into the env when executing hooks. Slash commands render their markdown as instructions to Claude, then Claude calls the Bash tool — Bash doesn't inherit
  plugin context, so the var is empty. The rtfi status.md at line 17 assumes a hook-style env that doesn't exist for commands.

  Options, in order of preference:

  1. File upstream — the proper fix. The command should resolve its own path, e.g.:
  SCRIPT="$(dirname "$0")/../scripts/rtfi_cli.py"
  1. or ship a wrapper script invoked via a hook. Per your code-and-config.md rule, don't edit ~/.claude/plugins/cache/ directly.
  2. Set it in your shell rc (workaround, brittle — version path changes on upgrade):
  export CLAUDE_PLUGIN_ROOT="$HOME/.claude/plugins/cache/rtfi/rtfi/1.2.0"
  2. Breaks on next rtfi upgrade (1.2.0 → 1.3.0).
  3. Use the skills directly — /rtfi:sessions, /rtfi:risky, /rtfi:show may have the same flaw; check before relying on them.

  Recommend option 1. Want me to draft an upstream issue for the rtfi repo?
