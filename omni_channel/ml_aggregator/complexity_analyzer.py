#!/usr/bin/env python3
"""
Complexity Analyzer
Analyze execution complexity for each opportunity

Factors:
- Number of steps/hops
- Cross-chain requirements
- Contract interaction complexity
- Timing requirements
- Capital requirements
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ..data_lake.data_models import OpportunitySignal, SignalType, ChainId


@dataclass
class ComplexityAnalysis:
    """Complexity analysis result"""
    signal_id: str
    complexity_score: int  # 1-10
    step_count: int
    is_cross_chain: bool
    chain_count: int
    capital_required_usd: float
    timing_criticality: float  # 0-1
    technical_complexity: float  # 0-1
    risk_factors: List[str] = field(default_factory=list)
    analyzed_at: int = 0


class ComplexityAnalyzer:
    """
    Analyze execution complexity for opportunities
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Base complexity by signal type
        self.type_complexity: Dict[SignalType, int] = {
            SignalType.LIQUIDATION: 3,  # Moderate complexity
            SignalType.ARBITRAGE: 5,
            SignalType.SANDWICH: 4,
            SignalType.BACKRUN: 3,
            SignalType.FRONT_RUN: 4,
            SignalType.CROSS_CHAIN_ARB: 8,  # High complexity
            SignalType.ORACLE_UPDATE: 2,  # Low complexity
            SignalType.LARGE_SWAP: 2,
            SignalType.NEW_PROTOCOL: 5,
            SignalType.VULNERABILITY: 6,
        }

        # Chain complexity (gas, finality, etc.)
        self.chain_complexity: Dict[int, float] = {
            1: 1.0,      # Ethereum - baseline
            42161: 0.8,  # Arbitrum - easier
            10: 0.8,     # Optimism
            137: 0.7,    # Polygon - easy
            8453: 0.7,   # Base - easy
            43114: 0.9,  # Avalanche
            56: 0.85,    # BSC
            324: 0.9,    # zkSync
        }

        # Statistics
        self.analyses_performed = 0

        print("🔧 Complexity Analyzer initialized")

    async def start(self):
        """Start analyzer"""
        print("\n🔧 Starting Complexity Analyzer...")
        self.is_running = True
        print("   ✅ Complexity Analyzer started")

    async def stop(self):
        """Stop analyzer"""
        self.is_running = False
        print("   🔧 Complexity Analyzer stopped")

    def analyze(self, signal: OpportunitySignal) -> ComplexityAnalysis:
        """Analyze complexity for a signal"""
        signal_id = signal.signal_id if hasattr(signal, 'signal_id') else signal.trigger_tx_hash

        # Base complexity from signal type
        base_complexity = self.type_complexity.get(signal.signal_type, 5)

        # Count steps
        step_count = self._estimate_steps(signal)

        # Check cross-chain
        is_cross_chain = signal.signal_type == SignalType.CROSS_CHAIN_ARB
        chain_count = self._get_chain_count(signal)

        # Calculate capital required
        capital_required = self._estimate_capital(signal)

        # Timing criticality
        timing_criticality = self._calculate_timing_criticality(signal)

        # Technical complexity
        technical_complexity = self._calculate_technical_complexity(signal)

        # Identify risk factors
        risk_factors = self._identify_risks(signal)

        # Calculate final complexity score (1-10)
        complexity_score = self._calculate_final_complexity(
            base_complexity,
            step_count,
            is_cross_chain,
            chain_count,
            timing_criticality,
            technical_complexity
        )

        self.analyses_performed += 1

        return ComplexityAnalysis(
            signal_id=signal_id,
            complexity_score=complexity_score,
            step_count=step_count,
            is_cross_chain=is_cross_chain,
            chain_count=chain_count,
            capital_required_usd=capital_required,
            timing_criticality=round(timing_criticality, 4),
            technical_complexity=round(technical_complexity, 4),
            risk_factors=risk_factors,
            analyzed_at=int(time.time())
        )

    def analyze_batch(self, signals: List[OpportunitySignal]) -> Dict[str, ComplexityAnalysis]:
        """Analyze batch of signals"""
        results = {}

        for signal in signals:
            signal_id = signal.signal_id if hasattr(signal, 'signal_id') else signal.trigger_tx_hash
            results[signal_id] = self.analyze(signal)

        return results

    def _estimate_steps(self, signal: OpportunitySignal) -> int:
        """Estimate number of execution steps"""
        step_estimates = {
            SignalType.LIQUIDATION: 2,  # Check HF -> liquidate
            SignalType.ARBITRAGE: 4,    # Buy -> transfer -> sell -> transfer back
            SignalType.SANDWICH: 3,     # Front-run -> victim -> back-run
            SignalType.BACKRUN: 2,     # Wait -> execute
            SignalType.FRONT_RUN: 2,    # Detect -> front-run
            SignalType.CROSS_CHAIN_ARB: 6,  # Multiple bridge + swap steps
            SignalType.ORACLE_UPDATE: 1,    # Single transaction
            SignalType.LARGE_SWAP: 2,       # Detect -> backrun
            SignalType.NEW_PROTOCOL: 3,     # Analyze -> interact -> exit
            SignalType.VULNERABILITY: 4,    # Complex exploitation
        }

        return step_estimates.get(signal.signal_type, 3)

    def _get_chain_count(self, signal: OpportunitySignal) -> int:
        """Get number of chains involved"""
        if signal.signal_type == SignalType.CROSS_CHAIN_ARB:
            return 2  # Minimum 2 chains

        # Check metadata for chain info
        if hasattr(signal, 'metadata'):
            metadata = signal.metadata or {}
            if 'chains' in metadata:
                return len(metadata['chains'])

        return 1  # Single chain

    def _estimate_capital(self, signal: OpportunitySignal) -> float:
        """Estimate capital required in USD"""
        # Use expected value as baseline
        base_capital = signal.expected_value_usd if signal.expected_value_usd > 0 else 10000

        # Adjust by signal type
        capital_multipliers = {
            SignalType.LIQUIDATION: 2.0,  # Need debt + collateral
            SignalType.ARBITRAGE: 1.5,
            SignalType.SANDWICH: 1.3,
            SignalType.BACKRUN: 1.0,
            SignalType.FRONT_RUN: 1.2,
            SignalType.CROSS_CHAIN_ARB: 3.0,  # Need capital on multiple chains
            SignalType.ORACLE_UPDATE: 0.5,
            SignalType.LARGE_SWAP: 1.0,
            SignalType.NEW_PROTOCOL: 1.5,
            SignalType.VULNERABILITY: 2.0,
        }

        multiplier = capital_multipliers.get(signal.signal_type, 1.0)

        return base_capital * multiplier

    def _calculate_timing_criticality(self, signal: OpportunitySignal) -> float:
        """Calculate timing criticality (0-1)"""
        latency_ms = signal.latency_requirement_ms if hasattr(signal, 'latency_requirement_ms') else 1000

        # Lower latency = higher criticality
        if latency_ms < 100:
            return 1.0  # Extremely critical
        elif latency_ms < 500:
            return 0.8
        elif latency_ms < 1000:
            return 0.6
        elif latency_ms < 5000:
            return 0.4
        else:
            return 0.2

    def _calculate_technical_complexity(self, signal: OpportunitySignal) -> float:
        """Calculate technical complexity (0-1)"""
        base_complexity = self.type_complexity.get(signal.signal_type, 5) / 10.0

        # Adjust for cross-chain
        if signal.signal_type == SignalType.CROSS_CHAIN_ARB:
            base_complexity += 0.2

        # Adjust for gas estimate (higher gas = more complex)
        gas_estimate = signal.gas_estimate if hasattr(signal, 'gas_estimate') else 200000
        if gas_estimate > 500000:
            base_complexity += 0.1

        return min(base_complexity, 1.0)

    def _identify_risks(self, signal: OpportunitySignal) -> List[str]:
        """Identify risk factors"""
        risks = []

        # Cross-chain risk
        if signal.signal_type == SignalType.CROSS_CHAIN_ARB:
            risks.append("bridge_finality_risk")
            risks.append("cross_chain_execution_risk")

        # High capital risk
        if signal.expected_value_usd > 100000:
            risks.append("high_capital_requirement")

        # Timing risk
        latency_ms = signal.latency_requirement_ms if hasattr(signal, 'latency_requirement_ms') else 1000
        if latency_ms < 100:
            risks.append("extreme_timing_pressure")

        # Gas risk
        gas_price = signal.gas_price_gwei if hasattr(signal, 'gas_price_gwei') else 30
        if gas_price > 100:
            risks.append("high_gas_environment")

        # New protocol risk
        if signal.signal_type == SignalType.NEW_PROTOCOL:
            risks.append("unaudited_protocol")
            risks.append("unknown_contract_risk")

        return risks

    def _calculate_final_complexity(self, base: int, steps: int,
                                     is_cross_chain: bool, chain_count: int,
                                     timing: float, technical: float) -> int:
        """Calculate final complexity score (1-10)"""
        # Start with base
        score = base

        # Add step complexity
        score += (steps - 2) * 0.3

        # Add cross-chain complexity
        if is_cross_chain:
            score += chain_count * 0.5

        # Add timing complexity
        score += timing * 1.5

        # Add technical complexity
        score += technical * 1.5

        # Normalize to 1-10
        return max(1, min(10, int(round(score))))

    def get_stats(self) -> Dict:
        """Get analyzer statistics"""
        return {
            'analyses_performed': self.analyses_performed,
            'type_complexity': {k.value: v for k, v in self.type_complexity.items()},
            'chain_complexity': self.chain_complexity,
        }
