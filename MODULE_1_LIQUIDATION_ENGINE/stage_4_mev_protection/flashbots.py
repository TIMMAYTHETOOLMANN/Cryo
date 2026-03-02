#!/usr/bin/env python3
"""
STAGE 4 — MEV Protection (Consolidated)
=========================================
Flashbots bundle submission, bloXroute private routing,
and oracle backrunning strategy.
"""

try:
    from ..src.mev_protection.flashbots import (
        MEVProtection,
        MEVRoute,
        FlashbotsBundle,
        BundleResult,
        OracleBackrunSignal,
    )
except ImportError:
    import logging
    logging.getLogger(__name__).warning(
        "Could not import MEVProtection from src/ — stub mode"
    )

    class MEVProtection:
        pass

__all__ = ["MEVProtection"]
