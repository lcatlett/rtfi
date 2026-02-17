#!/bin/bash
# RTFI Plugin Setup Script
# Ensures all dependencies are installed

set -e

echo "RTFI Plugin Setup"
echo "================="
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $PYTHON_VERSION"

# Install pydantic — prefer uv, fall back to pip
echo ""
echo "Installing dependencies..."
if command -v uv &> /dev/null; then
    uv pip install pydantic>=2.0.0
else
    python3 -m pip install --user -q pydantic>=2.0.0
fi

# Verify installation
if python3 -c "import pydantic" 2>/dev/null; then
    PYDANTIC_VERSION=$(python3 -c "import pydantic; print(pydantic.__version__)")
    echo "✓ pydantic $PYDANTIC_VERSION installed"
else
    echo "✗ Failed to install pydantic"
    echo "  Try: uv pip install pydantic>=2.0.0"
    echo "   or: pip3 install pydantic>=2.0.0"
    exit 1
fi

# Create RTFI directory with restricted permissions
RTFI_DIR="$HOME/.rtfi"
if [ ! -d "$RTFI_DIR" ]; then
    mkdir -p "$RTFI_DIR"
    chmod 700 "$RTFI_DIR"
    echo "✓ Created $RTFI_DIR"
else
    echo "✓ Directory $RTFI_DIR exists"
fi

# Run the setup wizard for config and database initialization
echo ""
python3 "$(dirname "$0")/rtfi_cli.py" setup

echo ""
echo "Available commands:"
echo "  /rtfi:sessions  - List recent sessions"
echo "  /rtfi:risky     - Show high-risk sessions"
echo "  /rtfi:status    - Show RTFI status"
echo "  /rtfi:health    - Run health check"
echo "  /rtfi:setup     - Re-run setup wizard"
