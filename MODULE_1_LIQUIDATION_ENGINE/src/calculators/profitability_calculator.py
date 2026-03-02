#!/usr/bin/env python3
"""
MODULE 1 — src/calculators/profitability_calculator.py
=======================================================
Thin re-export so that ``src/`` sub-packages can resolve
``from ..calculators.profitability_calculator import ProfitabilityCalculator, ...``
without duplicating any calculation logic.

The canonical implementation lives in
``MODULE_1_LIQUIDATION_ENGINE/stage_2_analysis/profitability_calculator.py``.
"""

from ...stage_2_analysis.profitability_calculator import (  # noqa: F401
    ExitStrategy,
    ExitAnalysis,
    ProfitabilityResult,
    GasPricePredictor,
    ProfitabilityCalculator,
    get_calculator,
)

__all__ = [
    "ExitStrategy",
    "ExitAnalysis",
    "ProfitabilityResult",
    "GasPricePredictor",
    "ProfitabilityCalculator",
    "get_calculator",
]
