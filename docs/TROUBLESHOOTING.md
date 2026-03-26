# RTFI Troubleshooting Guide

## Common Issues

### ModuleNotFoundError: No module named 'pydantic'

**Symptom:** Hook errors showing `ModuleNotFoundError: No module named 'pydantic'`

**Solution:**

Install the missing dependency:

1. **Run the setup script:**
   ```bash
   bash scripts/setup.sh
   ```

2. **Or install manually with uv (recommended):**
   ```bash
   uv pip install pydantic>=2.0.0
   ```

3. **Or install with pip:**
   ```bash
   pip3 install --user pydantic>=2.0.0
   ```

3. **Verify installation:**
   ```bash
   python3 -c "import pydantic; print(pydantic.__version__)"
   ```

### Hook Handler Errors on Startup

**Symptom:** Errors in Claude Code when starting a session

**Solution:**

1. **Check health:**
   ```bash
   python3 scripts/rtfi_cli.py health
   ```

2. **Check logs:**
   ```bash
   tail -f ~/.rtfi/rtfi.log
   ```

3. **Verify Python version:**
   ```bash
   python3 --version  # Should be >= 3.10
   ```

### Database Errors

**Symptom:** SQLite errors or database corruption

**Solution:**

1. **Check database location:**
   ```bash
   ls -lh ~/.rtfi/rtfi.db
   ```

2. **Reset database (WARNING: deletes all session data):**
   ```bash
   rm ~/.rtfi/rtfi.db
   # Database will be recreated on next session
   ```

### High Risk Scores Not Triggering Alerts

**Symptom:** Risk scores exceed threshold but no alerts appear

**Solution:**

1. **Check threshold setting:**
   ```bash
   python3 scripts/rtfi_cli.py status
   ```

2. **Verify action mode:**
   - Check `~/.rtfi/config.env` (primary config file)
   - Or environment variables: `RTFI_THRESHOLD`, `RTFI_ACTION_MODE`
   - Ensure action mode is set to `alert`, `block`, or `confirm`

3. **Check environment variables:**
   ```bash
   echo $RTFI_THRESHOLD
   echo $RTFI_ACTION_MODE
   cat ~/.rtfi/config.env
   ```

### Commands Not Working

**Symptom:** `/rtfi:*` commands not recognized

**Solution:**

1. **Verify plugin is loaded:**
   - Check Claude Code plugin directory
   - Ensure `hooks/hooks.json` exists

2. **Check command files:**
   ```bash
   ls -l commands/*.md
   ```

3. **Restart Claude Code**

## Getting Help

If you continue to experience issues:

1. **Run health check:**
   ```bash
   python3 scripts/rtfi_cli.py health
   ```

2. **Check logs:**
   ```bash
   cat ~/.rtfi/rtfi.log
   cat ~/.rtfi/audit.log
   ```

3. **Report issue:**
   - Include output from health check
   - Include relevant log entries
   - Include Python version and OS
   - Open issue at: https://github.com/lcatlett/rtfi/issues

## Debug Mode

To enable verbose logging:

```bash
# Set log level to DEBUG
export RTFI_LOG_LEVEL=DEBUG

# Check logs
tail -f ~/.rtfi/rtfi.log
```

## Performance Issues

If hooks are slowing down Claude Code:

1. **Increase hook timeout** in `hooks/hooks.json` (default: 5000ms)
2. **Reduce retention days** to clean up old sessions
3. **Check database size:**
   ```bash
   du -h ~/.rtfi/rtfi.db
   ```

