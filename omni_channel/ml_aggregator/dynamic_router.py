#!/usr/bin/env python3
"""
Dynamic Router
Route scored opportunities to appropriate execution modules

Routing logic:
- High-value → Liquidation Engine
- Medium-value → Arbitrage Module
- Low-latency → Backrun Bot
- Cross-chain → Cross-Chain Executor
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum

from ..data_lake.data_models import OpportunitySignal, SignalType, ExecutionModule
from .quality_scorer import ScoredSignal, ScoreTier


class RoutingStrategy(Enum):
    """Routing strategies"""
    SCORE_BASED = "score_based"  # Route by quality score
    TYPE_BASED = "type_based"  # Route by signal type
    VALUE_BASED = "value_based"  # Route by expected value
    COMPLEXITY_BASED = "complexity_based"  # Route by complexity


@dataclass
class RoutingDecision:
    """Routing decision for a signal"""
    signal_id: str
    routed_to: ExecutionModule
    routing_reason: str
    priority: int  # 1-10, higher = more urgent
    confidence: float
    alternative_modules: List[ExecutionModule] = field(default_factory=list)
    routed_at: int = 0


class DynamicRouter:
    """
    Route opportunities to execution modules
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Routing strategy
        self.strategy = RoutingStrategy(self.config.get('strategy', 'score_based'))

        # Module capabilities
        self.module_capabilities: Dict[ExecutionModule, List[SignalType]] = {
            ExecutionModule.LIQUIDATION_ENGINE: [
                SignalType.LIQUIDATION,
                SignalType.ORACLE_UPDATE,
                SignalType.VULNERABILITY,
            ],
            ExecutionModule.ARBITRAGE_MODULE: [
                SignalType.ARBITRAGE,
                SignalType.CROSS_CHAIN_ARB,
            ],
            ExecutionModule.BACKRUN_BOT: [
                SignalType.BACK_RUN,
                SignalType.LARGE_SWAP,
            ],
            ExecutionModule.SANDWICH_BOT: [
                SignalType.SANDWICH,
                SignalType.FRONT_RUN,
            ],
            ExecutionModule.CROSS_CHAIN_EXECUTOR: [
                SignalType.CROSS_CHAIN_ARB,
                SignalType.ARBITRAGE,
            ],
            ExecutionModule.MANUAL_REVIEW: [
                SignalType.NEW_PROTOCOL,
                SignalType.VULNERABILITY,
            ],
        }

        # Module load tracking
        self._module_load: Dict[ExecutionModule, int] = {m: 0 for m in ExecutionModule}
        self._module_capacity: Dict[ExecutionModule, int] = {
            ExecutionModule.LIQUIDATION_ENGINE: 100,
            ExecutionModule.ARBITRAGE_MODULE: 50,
            ExecutionModule.BACKRUN_BOT: 200,
            ExecutionModule.SANDWICH_BOT: 100,
            ExecutionModule.CROSS_CHAIN_EXECUTOR: 20,
            ExecutionModule.MANUAL_REVIEW: 10,
        }

        # Routing rules
        self._custom_rules: List[Dict] = []

        # Callbacks
        self._route_callbacks: List[Callable[[RoutingDecision], Awaitable[None]]] = []

        # Statistics
        self.signals_routed = 0
        self.routing_by_module: Dict[str, int] = {m.value: 0 for m in ExecutionModule}

        print("🔄 Dynamic Router initialized")

    async def start(self):
        """Start router"""
        print("\n🔄 Starting Dynamic Router...")
        self.is_running = True
        print("   ✅ Dynamic Router started")

    async def stop(self):
        """Stop router"""
        self.is_running = False
        print("   🔄 Dynamic Router stopped")

    def route(self, scored_signal: ScoredSignal) -> RoutingDecision:
        """Route a scored signal to execution module"""
        signal = scored_signal.signal

        # Apply routing strategy
        if self.strategy == RoutingStrategy.SCORE_BASED:
            decision = self._route_by_score(scored_signal)
        elif self.strategy == RoutingStrategy.TYPE_BASED:
            decision = self._route_by_type(signal)
        elif self.strategy == RoutingStrategy.VALUE_BASED:
            decision = self._route_by_value(signal)
        else:
            decision = self._route_by_score(scored_signal)

        # Apply custom rules
        for rule in self._custom_rules:
            if self._matches_rule(signal, rule):
                decision = self._apply_rule(signal, rule)
                break

        # Check module capacity
        if self._module_load[decision.routed_to] >= self._module_capacity[decision.routed_to]:
            # Module at capacity, find alternative
            decision = self._find_alternative(decision)

        # Update load
        self._module_load[decision.routed_to] += 1
        self.signals_routed += 1
        self.routing_by_module[decision.routed_to.value] += 1

        decision.routed_at = int(time.time())

        return decision

    def route_batch(self, scored_signals: List[ScoredSignal]) -> List[RoutingDecision]:
        """Route batch of scored signals"""
        decisions = []

        for scored in scored_signals:
            decision = self.route(scored)
            decisions.append(decision)

        return decisions

    def _route_by_score(self, scored: ScoredSignal) -> RoutingDecision:
        """Route based on quality score"""
        signal = scored.signal

        if scored.tier == ScoreTier.EXCELLENT:
            # High priority → Liquidation Engine
            module = ExecutionModule.LIQUIDATION_ENGINE
            reason = f"Excellent score ({scored.quality_score:.2f})"
            priority = 10
        elif scored.tier == ScoreTier.GOOD:
            # Medium priority → Arbitrage or Backrun
            if signal.signal_type in [SignalType.ARBITRAGE, SignalType.CROSS_CHAIN_ARB]:
                module = ExecutionModule.ARBITRAGE_MODULE
                reason = f"Good score, arbitrage type"
                priority = 7
            else:
                module = ExecutionModule.BACKRUN_BOT
                reason = f"Good score, standard execution"
                priority = 6
        elif scored.tier == ScoreTier.FAIR:
            # Lower priority → Backrun bot
            module = ExecutionModule.BACKRUN_BOT
            reason = f"Fair score, low-risk execution"
            priority = 4
        else:
            # Poor score → Manual review or skip
            module = ExecutionModule.MANUAL_REVIEW
            reason = f"Low score ({scored.quality_score:.2f}), manual review"
            priority = 1

        return RoutingDecision(
            signal_id=scored.signal.signal_id if hasattr(scored.signal, 'signal_id') else scored.signal.trigger_tx_hash,
            routed_to=module,
            routing_reason=reason,
            priority=priority,
            confidence=scored.quality_score,
            alternative_modules=self._get_alternatives(module)
        )

    def _route_by_type(self, signal: OpportunitySignal) -> RoutingDecision:
        """Route based on signal type"""
        type_routing: Dict[SignalType, ExecutionModule] = {
            SignalType.LIQUIDATION: ExecutionModule.LIQUIDATION_ENGINE,
            SignalType.ARBITRAGE: ExecutionModule.ARBITRAGE_MODULE,
            SignalType.SANDWICH: ExecutionModule.SANDWICH_BOT,
            SignalType.BACK_RUN: ExecutionModule.BACKRUN_BOT,
            SignalType.FRONT_RUN: ExecutionModule.SANDWICH_BOT,
            SignalType.CROSS_CHAIN_ARB: ExecutionModule.CROSS_CHAIN_EXECUTOR,
            SignalType.ORACLE_UPDATE: ExecutionModule.LIQUIDATION_ENGINE,
            SignalType.LARGE_SWAP: ExecutionModule.BACKRUN_BOT,
            SignalType.NEW_PROTOCOL: ExecutionModule.MANUAL_REVIEW,
            SignalType.VULNERABILITY: ExecutionModule.MANUAL_REVIEW,
        }

        module = type_routing.get(signal.signal_type, ExecutionModule.MANUAL_REVIEW)

        return RoutingDecision(
            signal_id=signal.signal_id if hasattr(signal, 'signal_id') else signal.trigger_tx_hash,
            routed_to=module,
            routing_reason=f"Type-based routing for {signal.signal_type.value}",
            priority=5,
            confidence=0.8,
            alternative_modules=self._get_alternatives(module)
        )

    def _route_by_value(self, signal: OpportunitySignal) -> RoutingDecision:
        """Route based on expected value"""
        ev = signal.expected_value_usd

        if ev > 10000:
            module = ExecutionModule.LIQUIDATION_ENGINE
            reason = f"High value (${ev:,.0f})"
            priority = 9
        elif ev > 1000:
            module = ExecutionModule.ARBITRAGE_MODULE
            reason = f"Medium value (${ev:,.0f})"
            priority = 6
        elif ev > 100:
            module = ExecutionModule.BACKRUN_BOT
            reason = f"Low value (${ev:,.0f})"
            priority = 4
        else:
            module = ExecutionModule.MANUAL_REVIEW
            reason = f"Very low value (${ev:,.0f})"
            priority = 1

        return RoutingDecision(
            signal_id=signal.signal_id if hasattr(signal, 'signal_id') else signal.trigger_tx_hash,
            routed_to=module,
            routing_reason=reason,
            priority=priority,
            confidence=0.7,
            alternative_modules=self._get_alternatives(module)
        )

    def _get_alternatives(self, primary: ExecutionModule) -> List[ExecutionModule]:
        """Get alternative modules for a primary module"""
        alternatives = {
            ExecutionModule.LIQUIDATION_ENGINE: [ExecutionModule.ARBITRAGE_MODULE, ExecutionModule.MANUAL_REVIEW],
            ExecutionModule.ARBITRAGE_MODULE: [ExecutionModule.CROSS_CHAIN_EXECUTOR, ExecutionModule.BACKRUN_BOT],
            ExecutionModule.BACKRUN_BOT: [ExecutionModule.SANDWICH_BOT, ExecutionModule.MANUAL_REVIEW],
            ExecutionModule.SANDWICH_BOT: [ExecutionModule.BACKRUN_BOT, ExecutionModule.MANUAL_REVIEW],
            ExecutionModule.CROSS_CHAIN_EXECUTOR: [ExecutionModule.ARBITRAGE_MODULE],
            ExecutionModule.MANUAL_REVIEW: [],
        }
        return alternatives.get(primary, [])

    def _find_alternative(self, decision: RoutingDecision) -> RoutingDecision:
        """Find alternative module when primary is at capacity"""
        for alt in decision.alternative_modules:
            if self._module_load[alt] < self._module_capacity[alt]:
                decision.routed_to = alt
                decision.routing_reason += f" (fallback to {alt.value})"
                return decision

        # All modules at capacity, queue for manual review
        decision.routed_to = ExecutionModule.MANUAL_REVIEW
        decision.routing_reason += " (all modules at capacity)"
        return decision

    def add_routing_rule(self, condition: Dict, target_module: ExecutionModule):
        """Add custom routing rule"""
        rule = {
            'condition': condition,
            'target': target_module
        }
        self._custom_rules.append(rule)

    def _matches_rule(self, signal: OpportunitySignal, rule: Dict) -> bool:
        """Check if signal matches rule condition"""
        condition = rule.get('condition', {})

        # Check signal type
        if 'signal_type' in condition:
            if signal.signal_type != condition['signal_type']:
                return False

        # Check minimum value
        if 'min_value' in condition:
            if signal.expected_value_usd < condition['min_value']:
                return False

        # Check chain
        if 'chain_id' in condition:
            if signal.chain_id != condition['chain_id']:
                return False

        return True

    def _apply_rule(self, signal: OpportunitySignal, rule: Dict) -> RoutingDecision:
        """Apply routing rule"""
        target = rule.get('target', ExecutionModule.MANUAL_REVIEW)

        return RoutingDecision(
            signal_id=signal.signal_id if hasattr(signal, 'signal_id') else signal.trigger_tx_hash,
            routed_to=target,
            routing_reason=f"Custom rule match",
            priority=8,
            confidence=0.9,
            alternative_modules=self._get_alternatives(target)
        )

    def get_module_load(self, module: ExecutionModule) -> float:
        """Get module load percentage"""
        capacity = self._module_capacity.get(module, 1)
        load = self._module_load.get(module, 0)
        return load / capacity

    def on_route(self, callback: Callable[[RoutingDecision], Awaitable[None]]):
        """Register routing callback"""
        self._route_callbacks.append(callback)

    async def emit_route(self, decision: RoutingDecision):
        """Emit routing decision to callbacks"""
        for callback in self._route_callbacks:
            try:
                await callback(decision)
            except Exception as e:
                print(f"   ⚠️  Route callback error: {e}")

    def get_stats(self) -> Dict:
        """Get router statistics"""
        return {
            'signals_routed': self.signals_routed,
            'routing_by_module': self.routing_by_module,
            'module_load': {
                m.value: self.get_module_load(m)
                for m in ExecutionModule
            },
            'strategy': self.strategy.value,
        }
