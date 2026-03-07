#!/usr/bin/env python3
"""
omni_channel — REDIRECT STUB
==============================
This package has been consolidated into MODULE_1_LIQUIDATION_ENGINE.detectors.

All imports from ``omni_channel.*`` are transparently redirected to
``MODULE_1_LIQUIDATION_ENGINE.detectors.*`` so existing code continues to work.

CANONICAL LOCATION:  MODULE_1_LIQUIDATION_ENGINE/detectors/
"""
import warnings

warnings.warn(
    "omni_channel has been consolidated into MODULE_1_LIQUIDATION_ENGINE.detectors. "
    "Update imports to use MODULE_1_LIQUIDATION_ENGINE.detectors.*",
    DeprecationWarning,
    stacklevel=2,
)

# ── Redirect: repoint this package's __path__ to the canonical location ──
# This makes `from omni_channel.X import Y` resolve to
# `MODULE_1_LIQUIDATION_ENGINE/detectors/X/` transparently.
import MODULE_1_LIQUIDATION_ENGINE.detectors as _canonical
__path__ = _canonical.__path__

# Re-export top-level symbols
from MODULE_1_LIQUIDATION_ENGINE.detectors import *  # noqa: F401,F403
