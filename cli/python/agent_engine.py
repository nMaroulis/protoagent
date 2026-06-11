"""Compatibility shim for older CLI imports.

The real Python implementation lives in ../../core/protoagent_core.
"""

from __future__ import annotations

import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[2] / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from protoagent_core.agent_engine import *  # noqa: F401,F403
