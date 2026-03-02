#!/usr/bin/env python3
"""
MODULE 1 — src/config_manager.py
==================================
Thin re-export so that ``src/`` sub-packages can resolve
``from ..config_manager import ConfigManager, get_config``
without duplicating any configuration logic.

The canonical config lives in ``MODULE_1_LIQUIDATION_ENGINE/config/settings.py``.
All consumers should import from *this* module to stay within the ``src/``
package boundary.
"""

from ..config.settings import (  # noqa: F401
    ConfigManager,
    ChainConfig,
    ProtocolConfig,
    FlashLoanProviderConfig,
    DatabaseConfig,
    ExecutionConfig,
    get_config,
)

__all__ = [
    "ConfigManager",
    "ChainConfig",
    "ProtocolConfig",
    "FlashLoanProviderConfig",
    "DatabaseConfig",
    "ExecutionConfig",
    "get_config",
]
