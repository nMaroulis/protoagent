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


class ClusterMonitor:
    def __init__(self, m, n):
        self.m, self.n = m, n
        self.grid = {}
        self.curr_clusters = 0
        self.dirs = ((1, 0), (-1, 0), (0, -1), (0, 1))

    def find(self, x):

        if x not in self.grid:
            self.grid[x] = x
        if self.grid[x] != x:
            self.grid[x] = self.find(self.grid[x])

        return self.grid[x]

    def union(self, x, y):

        self.grid[self.find(y)] = self.find(x)

    def turn_on(self, row, col):

        idx = row * self.n + col

        for dr, dc in self.dirs:
            d_idx = (idx + row) * self.n + +col + dc
            if 0 <= d_idx < self.n * self.m:
                self.union(idx, d_idx)
