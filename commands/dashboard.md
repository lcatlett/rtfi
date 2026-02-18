---
name: dashboard
description: Launch the RTFI web dashboard for live risk monitoring and session history
---

Launch the RTFI web dashboard:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/rtfi_dashboard.py" --port 7430
```

Open http://localhost:7430 in your browser. The dashboard shows:

- **Live risk gauge** — updates every 2 seconds while a Claude session is active
- **Factor bars** — context length, agent fanout, autonomy depth, decision velocity
- **Session history** — last 25 sessions with peak risk scores, tool call counts, agent spawns
- **Session detail** — click any row for full breakdown and recent events

Stop the dashboard with `Ctrl+C`.
