---
description: Reset autonomy depth for the current session (manual checkpoint)
---

Run the following command to create a manual checkpoint, resetting the autonomy depth risk factor:

```bash
python3 $CLAUDE_PLUGIN_ROOT/scripts/rtfi_cli.py checkpoint
```

Report the result to the user. If successful, the autonomy depth factor has been reset to 0, which may lower the overall risk score.
