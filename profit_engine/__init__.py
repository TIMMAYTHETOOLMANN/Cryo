#!/usr/bin/env python3
"""
profit_engine — REDIRECT STUB
================================
This package has been consolidated into MODULE_1_LIQUIDATION_ENGINE.profit_core.

All imports from ``profit_engine.*`` are transparently redirected so existing
code (unified_pipeline.py, run_until_profit.py, tests) continues to work.

Deleted duplicates (canonical replacements):
  - rpc_gateway.py            → MODULE_10_RPC_GATEWAY
  - jit_liquidation_engine.py → MODULE_11_TIMING_ENGINE
  - triangulated_profit_engine.py → MODULE_1_LIQUIDATION_ENGINE.pipeline + master_orchestrator

CANONICAL LOCATION:  MODULE_1_LIQUIDATION_ENGINE/profit_core/
"""
import warnings

warnings.warn(
    "profit_engine has been consolidated into MODULE_1_LIQUIDATION_ENGINE.profit_core. "
    "Update imports to use MODULE_1_LIQUIDATION_ENGINE.profit_core.*",
    DeprecationWarning,
    stacklevel=2,
)

# ── Redirect: repoint this package's __path__ to the canonical location ──
# This makes `from profit_engine.X import Y` resolve to
# `MODULE_1_LIQUIDATION_ENGINE/profit_core/X.py` transparently.
import MODULE_1_LIQUIDATION_ENGINE.profit_core as _canonical

__path__ = _canonical.__path__

# Re-export top-level symbols
from MODULE_1_LIQUIDATION_ENGINE.profit_core import *  # noqa: F401,F403
