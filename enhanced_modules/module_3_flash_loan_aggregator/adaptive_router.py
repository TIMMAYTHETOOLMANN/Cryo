#!/usr/bin/env python3
"""
enhanced_modules.module_3_flash_loan_aggregator.adaptive_router
================================================================
Extends the FlashLoanRouter with a **success-rate-weighted scoring
system** that dynamically re-ranks providers based on observed
latency, revert rate, and liquidity depth.

Every 100 executions, provider scores are recalculated and the
routing order is adjusted to favor the most reliable + cheapest.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import FlashLoanProviderType, ProviderProfile

logger = logging.getLogger(__name__)


@dataclass
class ProviderScore:
    """Composite score for a flash loan provider."""
    provider_type: FlashLoanProviderType
    chain_id: int
    # Raw metrics
    total_attempts: int = 0
    total_successes: int = 0
    total_reverts: int = 0
    total_gas_used: int = 0
    total_latency_ms: float = 0.0
    total_fees_usd: Decimal = Decimal("0")
    # Computed scores
    success_rate: float = 1.0
    avg_latency_ms: float = 0.0
    avg_gas_per_call: float = 0.0
    composite_score: float = 1.0
    last_updated: float = field(default_factory=time.time)

    def record_attempt(
        self, success: bool, gas_used: int, latency_ms: float, fee_usd: Decimal
    ) -> None:
        self.total_attempts += 1
        if success:
            self.total_successes += 1
        else:
            self.total_reverts += 1
        self.total_gas_used += gas_used
        self.total_latency_ms += latency_ms
        self.total_fees_usd += fee_usd
        self._recompute()

    def _recompute(self) -> None:
        if self.total_attempts > 0:
            self.success_rate = self.total_successes / self.total_attempts
            self.avg_latency_ms = self.total_latency_ms / self.total_attempts
            self.avg_gas_per_call = self.total_gas_used / self.total_attempts
        self.composite_score = self._compute_composite()
        self.last_updated = time.time()

    def _compute_composite(self) -> float:
        """
        Composite score ∈ [0, 1]. Higher is better.
        Weights: success_rate=0.4, cost=0.3, latency=0.2, experience=0.1
        """
        # Success component
        sr = self.success_rate * 0.4

        # Cost component (lower fee = higher score)
        avg_fee = float(self.total_fees_usd) / max(1, self.total_attempts)
        cost_score = max(0, 1.0 - avg_fee / 100) * 0.3  # $100 fee = 0 score

        # Latency component (lower = better)
        latency_score = max(0, 1.0 - self.avg_latency_ms / 5000) * 0.2

        # Experience component (more attempts = more confidence)
        exp_score = min(1.0, self.total_attempts / 50) * 0.1

        return sr + cost_score + latency_score + exp_score


@dataclass
class RoutingDecision:
    """The output of the adaptive router."""
    primary_provider: FlashLoanProviderType
    fallback_chain: List[FlashLoanProviderType]
    primary_score: float
    reasoning: str
    estimated_cost_usd: Decimal


class AdaptiveFlashLoanRouter(EnhancedModule):
    """
    Adaptive router that learns from execution history to rank
    flash loan providers optimally for each chain/asset pair.
    """

    RE_RANK_INTERVAL = 100  # Re-rank every N executions

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("adaptive_flash_loan_router", config)
        # chain_id -> provider_type -> ProviderScore
        self._scores: Dict[int, Dict[FlashLoanProviderType, ProviderScore]] = defaultdict(dict)
        # chain_id -> sorted list of (score, provider_type)
        self._rankings: Dict[int, List[Tuple[float, FlashLoanProviderType]]] = {}
        self._total_executions = 0

    async def _on_start(self) -> None:
        # Initialize scores for all known providers
        for chain_id in [1, 10, 42161, 8453, 137]:
            for pt in FlashLoanProviderType:
                self._scores[chain_id][pt] = ProviderScore(
                    provider_type=pt, chain_id=chain_id
                )
            self._rerank(chain_id)
        logger.info("[AdaptiveRouter] Initialized for %d chains", len(self._scores))

    async def _on_stop(self) -> None:
        for chain_id, scores in self._scores.items():
            for pt, score in scores.items():
                if score.total_attempts > 0:
                    logger.info(
                        "  [%d] %s: %d attempts, %.1f%% success, score=%.3f",
                        chain_id, pt.value, score.total_attempts,
                        score.success_rate * 100, score.composite_score,
                    )

    # ── Routing ────────────────────────────────────────────────

    def route(
        self,
        chain_id: int,
        asset: str,
        amount_usd: Decimal,
    ) -> RoutingDecision:
        """
        Determine the optimal provider ordering for a flash loan.
        """
        rankings = self._rankings.get(chain_id, [])
        if not rankings:
            # Fallback default ordering
            rankings = [
                (1.0, FlashLoanProviderType.BALANCER_V2),
                (0.9, FlashLoanProviderType.DODO),
                (0.8, FlashLoanProviderType.MAKER_DSS),
                (0.7, FlashLoanProviderType.AAVE_V3),
                (0.6, FlashLoanProviderType.DYDX),
                (0.5, FlashLoanProviderType.UNISWAP_V3),
            ]

        primary_score, primary = rankings[0]
        fallbacks = [pt for _, pt in rankings[1:]]

        return RoutingDecision(
            primary_provider=primary,
            fallback_chain=fallbacks,
            primary_score=primary_score,
            reasoning=f"Selected {primary.value} (score={primary_score:.3f}) based on "
                      f"{self._total_executions} historical executions",
            estimated_cost_usd=Decimal("0"),  # Filled by fee minimizer
        )

    # ── Feedback Loop ──────────────────────────────────────────

    def record_execution(
        self,
        chain_id: int,
        provider_type: FlashLoanProviderType,
        success: bool,
        gas_used: int = 0,
        latency_ms: float = 0.0,
        fee_usd: Decimal = Decimal("0"),
    ) -> None:
        """Record an execution outcome for adaptive learning."""
        score = self._scores[chain_id].get(provider_type)
        if not score:
            score = ProviderScore(provider_type=provider_type, chain_id=chain_id)
            self._scores[chain_id][provider_type] = score

        score.record_attempt(success, gas_used, latency_ms, fee_usd)
        self._total_executions += 1

        # Periodic re-ranking
        if self._total_executions % self.RE_RANK_INTERVAL == 0:
            self._rerank(chain_id)
            logger.info(
                "[AdaptiveRouter] Re-ranked chain %d after %d executions",
                chain_id, self._total_executions,
            )

    # ── Re-ranking ─────────────────────────────────────────────

    def _rerank(self, chain_id: int) -> None:
        """Recompute provider rankings for a chain."""
        scores = self._scores.get(chain_id, {})
        ranked = sorted(
            [(s.composite_score, pt) for pt, s in scores.items()],
            key=lambda x: (-x[0], x[1].value),
        )
        self._rankings[chain_id] = ranked

    def get_rankings(self, chain_id: int) -> List[Dict[str, Any]]:
        """Get current rankings for monitoring."""
        rankings = self._rankings.get(chain_id, [])
        return [
            {
                "rank": i + 1,
                "provider": pt.value,
                "score": round(score, 4),
                "attempts": self._scores[chain_id][pt].total_attempts,
                "success_rate": round(self._scores[chain_id][pt].success_rate, 4),
            }
            for i, (score, pt) in enumerate(rankings)
        ]
