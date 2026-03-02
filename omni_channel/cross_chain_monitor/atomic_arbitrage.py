#!/usr/bin/env python3
"""
Atomic Arbitrage Detector
Detect cross-chain arbitrage opportunities that can be executed atomically

Features:
- Atomic bridge detection
- Finality time analysis
- Execution feasibility check
- Risk assessment
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

from .multi_chain_graph import MultiChainGraph, Edge
from .n_hop_pathfinder import NHopPathfinder, ArbitragePath


class AtomicityType(Enum):
    """Types of atomic execution"""
    FULLY_ATOMIC = "fully_atomic"  # All steps in single tx
    BRIDGE_ATOMIC = "bridge_atomic"  # Bridge handles atomicity
    OPTIMISTIC = "optimistic"  # Optimistic finality
    NOT_ATOMIC = "not_atomic"  # Requires multiple txs


@dataclass
class CrossChainArb:
    """Cross-chain arbitrage opportunity"""
    arb_id: str
    opportunity: ArbitragePath
    atomicity: AtomicityType
    required_capital_usd: float
    expected_profit_usd: float
    expected_profit_percent: float
    execution_steps: List[Dict] = field(default_factory=list)
    bridge_transactions: List[str] = field(default_factory=list)
    finality_time_seconds: int = 0
    slippage_risk: float = 0
    bridge_risk: float = 0
    total_risk: float = 0
    can_execute: bool = False
    rejection_reason: str = ""


@dataclass
class BridgeCapability:
    """Bridge atomic execution capability"""
    bridge_name: str
    supports_atomic: bool
    atomic_type: AtomicityType
    max_amount_usd: float
    min_finality_seconds: int
    supported_chains: List[int]
    supported_tokens: List[str]


class AtomicArbitrageDetector:
    """
    Detect atomic cross-chain arbitrage opportunities
    """

    def __init__(self, graph: MultiChainGraph, pathfinder: NHopPathfinder,
                 config: Dict[str, Any] = None):
        self.graph = graph
        self.pathfinder = pathfinder
        self.config = config or {}

        # Bridge capabilities
        self._bridge_capabilities: Dict[str, BridgeCapability] = {}

        # Atomic execution requirements
        self.min_profit_usd = self.config.get('min_profit_usd', 50)
        self.max_finality_seconds = self.config.get('max_finality_seconds', 600)
        self.max_slippage = self.config.get('max_slippage', 0.05)  # 5%

        # Statistics
        self.opportunities_detected = 0
        self.atomic_opportunities = 0

        print("⚛️  Atomic Arbitrage Detector initialized")

    def initialize_bridge_capabilities(self):
        """Initialize bridge capability data"""
        # Stargate - supports atomic bridging via LayerZero
        self._bridge_capabilities["stargate"] = BridgeCapability(
            bridge_name="Stargate Finance",
            supports_atomic=True,
            atomic_type=AtomicityType.BRIDGE_ATOMIC,
            max_amount_usd=1000000,
            min_finality_seconds=60,
            supported_chains=[1, 42161, 10, 137, 8453, 43114, 56],
            supported_tokens=["USDC", "USDT", "ETH"]
        )

        # Hop - optimistic verification
        self._bridge_capabilities["hop"] = BridgeCapability(
            bridge_name="Hop Protocol",
            supports_atomic=False,
            atomic_type=AtomicityType.OPTIMISTIC,
            max_amount_usd=500000,
            min_finality_seconds=120,
            supported_chains=[1, 42161, 10, 137, 8453],
            supported_tokens=["ETH", "USDC", "USDT", "DAI"]
        )

        # Across - optimistic oracle
        self._bridge_capabilities["across"] = BridgeCapability(
            bridge_name="Across Protocol",
            supports_atomic=False,
            atomic_type=AtomicityType.OPTIMISTIC,
            max_amount_usd=750000,
            min_finality_seconds=30,
            supported_chains=[1, 42161, 10, 137, 8453],
            supported_tokens=["ETH", "USDC", "WBTC", "DAI"]
        )

        print(f"   ⚛️  Initialized {len(self._bridge_capabilities)} bridge capabilities")

    def detect_atomic_opportunities(self, amount_usd: float = 10000) -> List[CrossChainArb]:
        """Detect atomic arbitrage opportunities"""
        atomic_arbs = []

        # Find all profitable paths
        result = self.pathfinder.find_all_arbitrage_paths(amount_usd=amount_usd)

        for path in result.profitable_paths:
            # Check if path can be executed atomically
            atomic_arb = self._evaluate_atomicity(path, amount_usd)

            if atomic_arb and atomic_arb.can_execute:
                atomic_arbs.append(atomic_arb)
                self.atomic_opportunities += 1

            self.opportunities_detected += 1

        return atomic_arbs

    def _evaluate_atomicity(self, path: ArbitragePath, amount_usd: float) -> Optional[CrossChainArb]:
        """Evaluate if path can be executed atomically"""
        # Identify bridges used
        bridges_in_path = set(path.bridges_used)

        if not bridges_in_path:
            # No bridges = single chain, potentially fully atomic
            return self._create_single_chain_arb(path, amount_usd)

        # Check bridge capabilities
        atomic_bridges = []
        total_finality = 0

        for bridge_name in bridges_in_path:
            capability = self._bridge_capabilities.get(bridge_name)

            if not capability:
                return self._create_non_atomic_arb(path, amount_usd, f"Unknown bridge: {bridge_name}")

            if not capability.supports_atomic:
                return self._create_non_atomic_arb(path, amount_usd, f"Bridge not atomic: {bridge_name}")

            atomic_bridges.append(bridge_name)
            total_finality = max(total_finality, capability.min_finality_seconds)

            # Check amount limits
            if amount_usd > capability.max_amount_usd:
                return self._create_non_atomic_arb(
                    path, amount_usd,
                    f"Amount exceeds {bridge_name} limit"
                )

        # Check finality time
        if total_finality > self.max_finality_seconds:
            return self._create_non_atomic_arb(
                path, amount_usd,
                f"Finality time {total_finality}s exceeds max"
            )

        # Create atomic arb
        arb_id = f"atomic_{path.path_id}_{int(time.time())}"

        # Calculate risks
        slippage_risk = self._calculate_slippage_risk(path)
        bridge_risk = self._calculate_bridge_risk(atomic_bridges)

        # Execution steps
        execution_steps = self._build_execution_steps(path)

        atomic_arb = CrossChainArb(
            arb_id=arb_id,
            opportunity=path,
            atomicity=AtomicityType.BRIDGE_ATOMIC,
            required_capital_usd=amount_usd,
            expected_profit_usd=path.profit_amount,
            expected_profit_percent=path.profit_percent,
            execution_steps=execution_steps,
            bridge_transactions=[],  # Would populate with actual txs
            finality_time_seconds=total_finality,
            slippage_risk=slippage_risk,
            bridge_risk=bridge_risk,
            total_risk=slippage_risk + bridge_risk,
            can_execute=slippage_risk < self.max_slippage,
            rejection_reason="" if slippage_risk < self.max_slippage else "High slippage risk"
        )

        return atomic_arb

    def _create_single_chain_arb(self, path: ArbitragePath, amount_usd: float) -> CrossChainArb:
        """Create arb for single-chain opportunity"""
        arb_id = f"single_chain_{path.path_id}"

        return CrossChainArb(
            arb_id=arb_id,
            opportunity=path,
            atomicity=AtomicityType.FULLY_ATOMIC,
            required_capital_usd=amount_usd,
            expected_profit_usd=path.profit_amount,
            expected_profit_percent=path.profit_percent,
            execution_steps=self._build_execution_steps(path),
            finality_time_seconds=12,  # Single block
            slippage_risk=self._calculate_slippage_risk(path),
            bridge_risk=0,
            total_risk=self._calculate_slippage_risk(path),
            can_execute=True,
            rejection_reason=""
        )

    def _create_non_atomic_arb(self, path: ArbitragePath, amount_usd: float,
                                reason: str) -> CrossChainArb:
        """Create non-atomic arb (for tracking)"""
        arb_id = f"non_atomic_{path.path_id}"

        return CrossChainArb(
            arb_id=arb_id,
            opportunity=path,
            atomicity=AtomicityType.NOT_ATOMIC,
            required_capital_usd=amount_usd,
            expected_profit_usd=path.profit_amount,
            expected_profit_percent=path.profit_percent,
            execution_steps=[],
            finality_time_seconds=0,
            slippage_risk=self._calculate_slippage_risk(path),
            bridge_risk=1.0,  # High risk for non-atomic
            total_risk=1.0,
            can_execute=False,
            rejection_reason=reason
        )

    def _calculate_slippage_risk(self, path: ArbitragePath) -> float:
        """Calculate slippage risk for path"""
        # Base risk from path characteristics
        risk = 0.0

        # More hops = more slippage risk
        risk += min(path.hops * 0.01, 0.03)

        # Low liquidity increases risk
        min_liquidity = min(e.liquidity for e in path.edges) if path.edges else float('inf')

        if min_liquidity < 10000:
            risk += 0.05
        elif min_liquidity < 50000:
            risk += 0.02
        elif min_liquidity < 100000:
            risk += 0.01

        # Large amounts relative to liquidity
        if path.input_amount > min_liquidity * 0.1:
            risk += 0.02

        return min(risk, 0.15)

    def _calculate_bridge_risk(self, bridges: List[str]) -> float:
        """Calculate bridge-related risk"""
        risk = 0.0

        for bridge_name in bridges:
            capability = self._bridge_capabilities.get(bridge_name)

            if not capability:
                risk += 0.2
                continue

            # Risk based on atomicity type
            if capability.atomic_type == AtomicityType.FULLY_ATOMIC:
                risk += 0.01
            elif capability.atomic_type == AtomicityType.BRIDGE_ATOMIC:
                risk += 0.02
            elif capability.atomic_type == AtomicityType.OPTIMISTIC:
                risk += 0.05
            else:
                risk += 0.1

        return min(risk, 0.2)

    def _build_execution_steps(self, path: ArbitragePath) -> List[Dict]:
        """Build execution step list"""
        steps = []

        for i, edge in enumerate(path.edges):
            step = {
                'step': i + 1,
                'type': edge.edge_type,
                'from': edge.source,
                'to': edge.destination,
                'rate': edge.rate,
                'fee': edge.fee,
            }

            if edge.edge_type == "swap":
                step['protocol'] = edge.metadata.get('protocol', 'unknown')
                step['pool'] = edge.metadata.get('pool', 'unknown')
            elif edge.edge_type == "bridge":
                step['bridge'] = edge.metadata.get('bridge', 'unknown')
                step['finality_ms'] = edge.latency_ms

            steps.append(step)

        return steps

    def get_atomic_bridges(self) -> List[str]:
        """Get list of bridges that support atomic execution"""
        return [
            name for name, cap in self._bridge_capabilities.items()
            if cap.supports_atomic
        ]

    def get_stats(self) -> Dict:
        """Get detector statistics"""
        return {
            'opportunities_detected': self.opportunities_detected,
            'atomic_opportunities': self.atomic_opportunities,
            'bridge_capabilities': len(self._bridge_capabilities),
            'atomic_bridges': len(self.get_atomic_bridges()),
        }
