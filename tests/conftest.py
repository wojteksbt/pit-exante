"""Shared test configuration."""

import sys
from pathlib import Path

# Ensure src is on the path for all test modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
