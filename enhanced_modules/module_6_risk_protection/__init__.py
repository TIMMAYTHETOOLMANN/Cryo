"""
Module 6 — Enhanced Risk & Slippage Protection
TWAP validation, dynamic slippage, multi-tier circuit breaker.
"""
from enhanced_modules.module_6_risk_protection.twap_oracle import TWAPOracle
from enhanced_modules.module_6_risk_protection.dynamic_slippage import DynamicSlippageCalculator
from enhanced_modules.module_6_risk_protection.circuit_breaker_v2 import CircuitBreakerV2

__all__ = ["TWAPOracle", "DynamicSlippageCalculator", "CircuitBreakerV2"]
