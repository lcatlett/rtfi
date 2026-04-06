#!/bin/bash
# RTFI Plugin Setup Script
# Validates environment and initializes config + database.
# No third-party dependencies required — RTFI uses Python stdlib only.

set -e

echo "RTFI Plugin Setup"
echo "================="
echo ""

# Ensure mise-managed Python is active if available
if command -v mise &> /dev/null; then
    eval "$(mise activate bash 2>/dev/null)" || true
fi

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    echo "✗ Python >= 3.10 required (found $PYTHON_VERSION)"
    echo "  Install via mise: mise use python@3.14"
    exit 1
fi
echo "✓ Python version: $PYTHON_VERSION"

# Run the setup wizard for config and database initialization
echo ""
python3 "$(dirname "$0")/rtfi_cli.py" setup
