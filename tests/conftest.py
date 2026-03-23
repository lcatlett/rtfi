"""Pytest configuration - add scripts directory to path."""

import sys
from pathlib import Path

# Add scripts directory to path so tests can import rtfi_core
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))
