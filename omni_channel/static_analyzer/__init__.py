"""
Static Analysis Engine
Pre-Deployment & Pre-Interaction MEV Discovery

This module performs static analysis on contracts to discover MEV opportunities:
- Symbolic execution with Manticore
- Bytecode decompilation with Panoramix
- MEV pattern matching
- Liquidation formula extraction
- Vulnerability scanning
"""

from .manticore_engine import ManticoreEngine, AnalysisResult
from .panoramix_decompiler import PanoramixDecompiler, DecompiledContract
from .mev_patterns import MEVPatternMatcher, MEVPattern, MEVType
from .liquidation_formulas import LiquidationFormulaExtractor, HealthFactorConfig
from .vulnerability_scanner import VulnerabilityScanner, Vulnerability

__all__ = [
    # Main classes
    'ManticoreEngine',
    'AnalysisResult',
    'PanoramixDecompiler',
    'DecompiledContract',
    'MEVPatternMatcher',
    'MEVPattern',
    'MEVType',
    'LiquidationFormulaExtractor',
    'HealthFactorConfig',
    'VulnerabilityScanner',
    'Vulnerability',
]
