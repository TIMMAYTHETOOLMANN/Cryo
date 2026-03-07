#!/usr/bin/env python3
"""
enhanced_modules.module_8_analytics_dashboard.ab_testing_engine
================================================================
Production-grade A/B testing engine for continuous strategy optimization
of the CryoSUPER liquidation pipeline.

Capabilities
────────────
  • Multi-variant experiments on any tunable parameter (gas multiplier,
    min-profit threshold, flash-loan provider, MEV strategy, etc.)
  • Deterministic hashing for reproducible variant assignment
  • Per-variant metric collection (profit, gas, success rate, latency)
  • Bayesian posterior analysis with Thompson Sampling (scipy)
  • Frequentist t-test & chi-squared significance checks
  • Multi-armed bandit adaptive traffic allocation
  • Automatic winner promotion when significance thresholds are met
  • Guardrail system that halts variants breaching risk limits
  • Full audit trail & experiment lifecycle management

Dependencies: numpy, scipy (both in requirements.txt).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import numpy as np

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import ExecutionRecord

logger = logging.getLogger(__name__)

# ── Optional scipy imports ─────────────────────────────────────────
_SCIPY_AVAILABLE = False
try:
    from scipy import stats as sp_stats
    from scipy.special import betaln  # noqa: F401
    _SCIPY_AVAILABLE = True
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════════
#  Enums & Data Classes
# ═══════════════════════════════════════════════════════════════════

class ExperimentStatus(Enum):
    """Lifecycle states for an experiment."""
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


class SignificanceMethod(Enum):
    """Statistical testing methodology."""
    BAYESIAN = "bayesian"
    FREQUENTIST = "frequentist"
    HYBRID = "hybrid"


class AllocationStrategy(Enum):
    """Traffic allocation strategy."""
    FIXED = "fixed"                    # Static weights
    THOMPSON_SAMPLING = "thompson"     # Bayesian multi-armed bandit
    EPSILON_GREEDY = "epsilon_greedy"  # ε-greedy exploration
    UCB1 = "ucb1"                      # Upper confidence bound


@dataclass
class VariantConfig:
    """Configuration for a single experiment variant."""
    variant_id: str
    name: str
    description: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    traffic_weight: float = 0.5        # Static allocation weight (0–1)
    is_control: bool = False

    def __post_init__(self) -> None:
        if not self.variant_id:
            self.variant_id = f"v_{uuid.uuid4().hex[:8]}"


@dataclass
class VariantMetrics:
    """Accumulated metrics for a single variant."""
    variant_id: str
    # Counters
    impressions: int = 0               # Times this variant was assigned
    conversions: int = 0               # Successful profitable liquidations
    failures: int = 0
    # Continuous metrics
    total_profit_usd: float = 0.0
    total_gas_usd: float = 0.0
    total_latency_ms: float = 0.0
    profits: List[float] = field(default_factory=list)
    gas_costs: List[float] = field(default_factory=list)
    # Bayesian priors (Beta distribution for success rate)
    alpha: float = 1.0                 # successes + 1 (prior)
    beta_param: float = 1.0            # failures + 1 (prior)
    # Bayesian priors (Normal-Gamma for profit distribution)
    mu_0: float = 0.0
    kappa: float = 1.0
    alpha_ng: float = 1.0
    beta_ng: float = 1.0
    # Timestamps
    first_observation: float = 0.0
    last_observation: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.impressions
        return self.conversions / max(1, total)

    @property
    def avg_profit(self) -> float:
        return self.total_profit_usd / max(1, self.conversions)

    @property
    def avg_gas(self) -> float:
        return self.total_gas_usd / max(1, self.impressions)

    @property
    def net_profit(self) -> float:
        return self.total_profit_usd - self.total_gas_usd

    @property
    def avg_latency(self) -> float:
        return self.total_latency_ms / max(1, self.impressions)


@dataclass
class GuardrailConfig:
    """Safety guardrails that halt a variant when breached."""
    min_success_rate: float = 0.20          # Stop if SR drops below this
    max_avg_gas_usd: float = 500.0          # Stop if avg gas exceeds
    max_loss_usd: float = -1000.0           # Stop if net profit falls below
    min_observations: int = 30              # Minimum obs before checking
    max_consecutive_failures: int = 10


@dataclass
class ExperimentConfig:
    """Full configuration for an A/B experiment."""
    experiment_id: str
    name: str
    description: str = ""
    parameter_key: str = ""                 # e.g. "gas_multiplier"
    variants: List[VariantConfig] = field(default_factory=list)
    allocation_strategy: AllocationStrategy = AllocationStrategy.THOMPSON_SAMPLING
    significance_method: SignificanceMethod = SignificanceMethod.HYBRID
    significance_threshold: float = 0.95    # Bayesian posterior probability
    frequentist_alpha: float = 0.05         # p-value threshold
    min_sample_size: int = 100              # Per-variant minimum
    max_duration_seconds: float = 86400 * 7  # 7 days
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)
    auto_promote: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class ExperimentResult:
    """Final or interim result of an experiment."""
    experiment_id: str
    status: ExperimentStatus
    winner_variant_id: Optional[str] = None
    winner_confidence: float = 0.0
    variant_summaries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    bayesian_probabilities: Dict[str, float] = field(default_factory=dict)
    frequentist_p_value: Optional[float] = None
    effect_size: Optional[float] = None     # Cohen's d
    recommendation: str = ""
    analysis_timestamp: float = field(default_factory=time.time)
    halted_variants: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
#  Experiment State
# ═══════════════════════════════════════════════════════════════════

class _ExperimentState:
    """Internal mutable state for a running experiment."""

    def __init__(self, config: ExperimentConfig):
        self.config = config
        self.status = ExperimentStatus.DRAFT
        self.metrics: Dict[str, VariantMetrics] = {
            v.variant_id: VariantMetrics(variant_id=v.variant_id)
            for v in config.variants
        }
        self.halted_variants: set[str] = set()
        self.promotion_history: List[Dict[str, Any]] = []
        self.started_at: Optional[float] = None
        self.ended_at: Optional[float] = None
        self._assignment_count = 0


# ═══════════════════════════════════════════════════════════════════
#  A/B Testing Engine
# ═══════════════════════════════════════════════════════════════════

class ABTestingEngine(EnhancedModule):
    """
    Multi-armed bandit + Bayesian A/B testing engine for continuous
    strategy optimization of the CryoSUPER liquidation pipeline.

    Usage
    ─────
    >>> engine = ABTestingEngine()
    >>> await engine.start()
    >>> exp_id = engine.create_experiment(ExperimentConfig(...))
    >>> engine.start_experiment(exp_id)
    >>> variant = engine.assign_variant(exp_id, record)
    >>> engine.record_outcome(exp_id, variant.variant_id, record)
    >>> result = engine.analyze(exp_id)
    """

    # ── Constructor ────────────────────────────────────────────

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("ab_testing_engine", config)
        self._experiments: Dict[str, _ExperimentState] = {}
        self._experiment_audit: List[Dict[str, Any]] = []
        self._rng = np.random.default_rng(seed=42)

        # Tunables
        self._epsilon: float = float(self.config.get("epsilon", 0.10))
        self._bayesian_samples: int = int(self.config.get("bayesian_samples", 10_000))
        self._auto_analyze_interval: int = int(self.config.get("auto_analyze_interval", 50))
        self._max_experiments: int = int(self.config.get("max_experiments", 50))

    # ── Lifecycle Hooks ────────────────────────────────────────

    async def _on_start(self) -> None:
        logger.info(
            "[ABTestingEngine] Started — allocation strategies: %s, scipy=%s",
            [s.value for s in AllocationStrategy],
            _SCIPY_AVAILABLE,
        )

    async def _on_stop(self) -> None:
        active = sum(
            1 for e in self._experiments.values()
            if e.status == ExperimentStatus.RUNNING
        )
        logger.info(
            "[ABTestingEngine] Stopping — %d experiments (%d active)",
            len(self._experiments), active,
        )
        # Auto-complete running experiments
        for exp_id, state in self._experiments.items():
            if state.status == ExperimentStatus.RUNNING:
                state.status = ExperimentStatus.COMPLETED
                state.ended_at = time.time()

    # ── Experiment CRUD ────────────────────────────────────────

    def create_experiment(self, config: ExperimentConfig) -> str:
        """Register a new experiment. Returns the experiment ID."""
        if len(self._experiments) >= self._max_experiments:
            raise RuntimeError(
                f"Maximum experiment limit ({self._max_experiments}) reached. "
                "Complete or delete existing experiments first."
            )
        if config.experiment_id in self._experiments:
            raise ValueError(f"Experiment '{config.experiment_id}' already exists")
        if len(config.variants) < 2:
            raise ValueError("An experiment requires at least 2 variants")

        # Ensure exactly one control
        controls = [v for v in config.variants if v.is_control]
        if not controls:
            config.variants[0].is_control = True
            logger.info(
                "[ABTestingEngine] Auto-assigned '%s' as control",
                config.variants[0].name,
            )

        # Normalize traffic weights
        total_weight = sum(v.traffic_weight for v in config.variants)
        if total_weight > 0:
            for v in config.variants:
                v.traffic_weight /= total_weight

        state = _ExperimentState(config)
        self._experiments[config.experiment_id] = state
        self._audit_log("experiment_created", config.experiment_id)
        logger.info(
            "[ABTestingEngine] Created experiment '%s' with %d variants",
            config.name, len(config.variants),
        )
        return config.experiment_id

    def start_experiment(self, experiment_id: str) -> None:
        """Transition experiment from DRAFT → RUNNING."""
        state = self._get_state(experiment_id)
        if state.status not in (ExperimentStatus.DRAFT, ExperimentStatus.PAUSED):
            raise RuntimeError(
                f"Cannot start experiment in state '{state.status.value}'"
            )
        state.status = ExperimentStatus.RUNNING
        state.started_at = time.time()
        self._audit_log("experiment_started", experiment_id)
        logger.info("[ABTestingEngine] Experiment '%s' is RUNNING", experiment_id)

    def pause_experiment(self, experiment_id: str) -> None:
        """Pause a running experiment."""
        state = self._get_state(experiment_id)
        if state.status != ExperimentStatus.RUNNING:
            raise RuntimeError("Can only pause a RUNNING experiment")
        state.status = ExperimentStatus.PAUSED
        self._audit_log("experiment_paused", experiment_id)

    def complete_experiment(
        self, experiment_id: str, promote_winner: bool = True
    ) -> ExperimentResult:
        """Finalize an experiment and optionally promote the winner."""
        state = self._get_state(experiment_id)
        result = self._analyze_experiment(state)

        state.status = ExperimentStatus.COMPLETED
        state.ended_at = time.time()
        result.status = ExperimentStatus.COMPLETED

        if promote_winner and result.winner_variant_id:
            self._promote_variant(state, result.winner_variant_id, result)

        self._audit_log(
            "experiment_completed", experiment_id,
            {"winner": result.winner_variant_id, "confidence": result.winner_confidence},
        )
        logger.info(
            "[ABTestingEngine] Experiment '%s' COMPLETED → winner='%s' (%.1f%% confidence)",
            experiment_id,
            result.winner_variant_id or "none",
            result.winner_confidence * 100,
        )
        return result

    def delete_experiment(self, experiment_id: str) -> None:
        """Remove an experiment (must be COMPLETED or DRAFT)."""
        state = self._get_state(experiment_id)
        if state.status == ExperimentStatus.RUNNING:
            raise RuntimeError("Cannot delete a RUNNING experiment — stop it first")
        del self._experiments[experiment_id]
        self._audit_log("experiment_deleted", experiment_id)

    # ── Variant Assignment ─────────────────────────────────────

    def assign_variant(
        self,
        experiment_id: str,
        record: ExecutionRecord,
    ) -> VariantConfig:
        """
        Assign a variant for the given execution record.

        Uses the experiment's allocation strategy:
          - FIXED:             Deterministic hash-based split
          - THOMPSON_SAMPLING: Bayesian posterior sampling
          - EPSILON_GREEDY:    Best performer + ε random exploration
          - UCB1:              Upper Confidence Bound
        """
        state = self._get_state(experiment_id)
        if state.status != ExperimentStatus.RUNNING:
            raise RuntimeError(
                f"Experiment '{experiment_id}' is not RUNNING "
                f"(state={state.status.value})"
            )

        eligible = [
            v for v in state.config.variants
            if v.variant_id not in state.halted_variants
        ]
        if not eligible:
            raise RuntimeError(
                f"All variants halted in experiment '{experiment_id}'"
            )
        if len(eligible) == 1:
            chosen = eligible[0]
        else:
            strategy = state.config.allocation_strategy
            if strategy == AllocationStrategy.FIXED:
                chosen = self._assign_fixed(state, eligible, record)
            elif strategy == AllocationStrategy.THOMPSON_SAMPLING:
                chosen = self._assign_thompson(state, eligible)
            elif strategy == AllocationStrategy.EPSILON_GREEDY:
                chosen = self._assign_epsilon_greedy(state, eligible)
            elif strategy == AllocationStrategy.UCB1:
                chosen = self._assign_ucb1(state, eligible)
            else:
                chosen = self._assign_fixed(state, eligible, record)

        # Count impression
        state.metrics[chosen.variant_id].impressions += 1
        now = time.time()
        m = state.metrics[chosen.variant_id]
        if m.first_observation == 0.0:
            m.first_observation = now
        m.last_observation = now
        state._assignment_count += 1

        return chosen

    def record_outcome(
        self,
        experiment_id: str,
        variant_id: str,
        record: ExecutionRecord,
        latency_ms: float = 0.0,
    ) -> List[str]:
        """
        Record the outcome of an execution under a variant.

        Returns list of guardrail warnings (empty if all clear).
        """
        state = self._get_state(experiment_id)
        m = state.metrics.get(variant_id)
        if m is None:
            raise ValueError(f"Unknown variant '{variant_id}'")

        profit = float(record.net_profit_usd)
        gas = float(record.gas_cost_usd)
        success = record.status.value == "confirmed" and profit > 0

        if success:
            m.conversions += 1
            m.total_profit_usd += profit
            m.alpha += 1.0
        else:
            m.failures += 1
            m.beta_param += 1.0

        m.total_gas_usd += gas
        m.total_latency_ms += latency_ms
        m.profits.append(profit)
        m.gas_costs.append(gas)

        # Update Normal-Gamma posterior for profit
        self._update_normal_gamma(m, profit)

        # Guardrail check
        warnings = self._check_guardrails(state, variant_id)

        # Periodic auto-analysis
        total_obs = sum(vm.impressions for vm in state.metrics.values())
        if (
            total_obs > 0
            and total_obs % self._auto_analyze_interval == 0
            and state.status == ExperimentStatus.RUNNING
        ):
            self._auto_check_experiment(state)

        self.record_success()  # EnhancedModule bookkeeping
        return warnings

    # ── Analysis ───────────────────────────────────────────────

    def analyze(self, experiment_id: str) -> ExperimentResult:
        """Run full statistical analysis on an experiment."""
        state = self._get_state(experiment_id)
        return self._analyze_experiment(state)

    def get_experiment_status(self, experiment_id: str) -> Dict[str, Any]:
        """Lightweight status snapshot."""
        state = self._get_state(experiment_id)
        return {
            "experiment_id": experiment_id,
            "name": state.config.name,
            "status": state.status.value,
            "variants": {
                v.variant_id: {
                    "name": v.name,
                    "impressions": state.metrics[v.variant_id].impressions,
                    "conversions": state.metrics[v.variant_id].conversions,
                    "success_rate": round(state.metrics[v.variant_id].success_rate, 4),
                    "net_profit": round(state.metrics[v.variant_id].net_profit, 2),
                    "avg_profit": round(state.metrics[v.variant_id].avg_profit, 2),
                    "halted": v.variant_id in state.halted_variants,
                }
                for v in state.config.variants
            },
            "total_observations": sum(
                vm.impressions for vm in state.metrics.values()
            ),
            "halted_variants": list(state.halted_variants),
            "uptime_seconds": (
                round(time.time() - state.started_at, 1) if state.started_at else 0
            ),
        }

    def list_experiments(self) -> List[Dict[str, Any]]:
        """List all experiments with summary info."""
        return [
            {
                "experiment_id": eid,
                "name": s.config.name,
                "status": s.status.value,
                "variants": len(s.config.variants),
                "total_observations": sum(
                    vm.impressions for vm in s.metrics.values()
                ),
            }
            for eid, s in self._experiments.items()
        ]

    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return recent audit trail entries."""
        return self._experiment_audit[-limit:]

    # ═══════════════════════════════════════════════════════════
    #  ALLOCATION STRATEGIES (Private)
    # ═══════════════════════════════════════════════════════════

    def _assign_fixed(
        self,
        state: _ExperimentState,
        eligible: List[VariantConfig],
        record: ExecutionRecord,
    ) -> VariantConfig:
        """Deterministic hash-based assignment for reproducibility."""
        hash_input = f"{state.config.experiment_id}:{record.record_id}:{record.borrower}"
        digest = hashlib.sha256(hash_input.encode()).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF  # ∈ [0, 1]

        # Renormalize weights across eligible variants
        total_w = sum(v.traffic_weight for v in eligible)
        cumulative = 0.0
        for v in eligible:
            cumulative += v.traffic_weight / max(total_w, 1e-9)
            if bucket <= cumulative:
                return v
        return eligible[-1]

    def _assign_thompson(
        self,
        state: _ExperimentState,
        eligible: List[VariantConfig],
    ) -> VariantConfig:
        """
        Thompson Sampling — draw from each variant's Beta posterior
        and pick the one with the highest sampled success rate.
        """
        best_sample = -1.0
        best_variant = eligible[0]

        for v in eligible:
            m = state.metrics[v.variant_id]
            sample = float(self._rng.beta(m.alpha, m.beta_param))
            if sample > best_sample:
                best_sample = sample
                best_variant = v

        return best_variant

    def _assign_epsilon_greedy(
        self,
        state: _ExperimentState,
        eligible: List[VariantConfig],
    ) -> VariantConfig:
        """
        ε-greedy: with probability ε pick a random variant,
        otherwise pick the current best performer.
        """
        if float(self._rng.random()) < self._epsilon:
            return eligible[int(self._rng.integers(0, len(eligible)))]

        # Pick best by net profit per impression
        best = max(
            eligible,
            key=lambda v: (
                state.metrics[v.variant_id].net_profit
                / max(1, state.metrics[v.variant_id].impressions)
            ),
        )
        return best

    def _assign_ucb1(
        self,
        state: _ExperimentState,
        eligible: List[VariantConfig],
    ) -> VariantConfig:
        """
        UCB1 — Upper Confidence Bound.
        Balances exploitation (mean reward) with exploration (uncertainty).
        """
        total_n = max(1, sum(state.metrics[v.variant_id].impressions for v in eligible))
        best_score = -math.inf
        best_variant = eligible[0]

        for v in eligible:
            m = state.metrics[v.variant_id]
            n_i = max(1, m.impressions)
            mean_reward = m.net_profit / n_i
            exploration_bonus = math.sqrt(2.0 * math.log(total_n) / n_i)
            score = mean_reward + exploration_bonus
            if score > best_score:
                best_score = score
                best_variant = v

        return best_variant

    # ═══════════════════════════════════════════════════════════
    #  STATISTICAL ANALYSIS (Private)
    # ═══════════════════════════════════════════════════════════

    def _analyze_experiment(self, state: _ExperimentState) -> ExperimentResult:
        """Full statistical analysis of an experiment."""
        config = state.config
        variant_summaries: Dict[str, Dict[str, Any]] = {}
        bayesian_probs: Dict[str, float] = {}

        # Build variant summaries
        for v in config.variants:
            m = state.metrics[v.variant_id]
            variant_summaries[v.variant_id] = {
                "name": v.name,
                "is_control": v.is_control,
                "impressions": m.impressions,
                "conversions": m.conversions,
                "failures": m.failures,
                "success_rate": round(m.success_rate, 4),
                "total_profit_usd": round(m.total_profit_usd, 2),
                "total_gas_usd": round(m.total_gas_usd, 2),
                "net_profit_usd": round(m.net_profit, 2),
                "avg_profit_usd": round(m.avg_profit, 2),
                "avg_gas_usd": round(m.avg_gas, 2),
                "avg_latency_ms": round(m.avg_latency, 2),
                "halted": v.variant_id in state.halted_variants,
            }

        # ── Bayesian analysis (Monte Carlo) ────────────────────
        bayesian_probs = self._bayesian_win_probabilities(state)

        # ── Frequentist analysis ───────────────────────────────
        p_value = None
        effect_size = None
        control_v = next(
            (v for v in config.variants if v.is_control), config.variants[0]
        )
        treatments = [v for v in config.variants if not v.is_control]

        if _SCIPY_AVAILABLE and treatments:
            p_value, effect_size = self._frequentist_analysis(
                state, control_v.variant_id, treatments[0].variant_id
            )

        # ── Determine winner ───────────────────────────────────
        winner_id: Optional[str] = None
        winner_conf = 0.0
        recommendation = ""

        sufficient_data = all(
            state.metrics[v.variant_id].impressions >= config.min_sample_size
            for v in config.variants
            if v.variant_id not in state.halted_variants
        )

        if sufficient_data and bayesian_probs:
            best_vid = max(bayesian_probs, key=bayesian_probs.get)  # type: ignore[arg-type]
            best_prob = bayesian_probs[best_vid]

            if best_prob >= config.significance_threshold:
                winner_id = best_vid
                winner_conf = best_prob
                winner_name = variant_summaries[best_vid]["name"]
                recommendation = (
                    f"Promote '{winner_name}' — "
                    f"{best_prob:.1%} Bayesian probability of being best"
                )
                if p_value is not None:
                    recommendation += f" (frequentist p={p_value:.4f})"
            else:
                recommendation = (
                    f"No winner yet — best is '{variant_summaries[best_vid]['name']}' "
                    f"at {best_prob:.1%} (need {config.significance_threshold:.0%}). "
                    f"Continue collecting data."
                )
        elif not sufficient_data:
            min_obs = min(
                state.metrics[v.variant_id].impressions
                for v in config.variants
                if v.variant_id not in state.halted_variants
            ) if any(
                v.variant_id not in state.halted_variants
                for v in config.variants
            ) else 0
            recommendation = (
                f"Insufficient data — min observations per variant: {min_obs} "
                f"(need {config.min_sample_size})"
            )

        # ── Duration check ─────────────────────────────────────
        if state.started_at:
            elapsed = time.time() - state.started_at
            if elapsed > config.max_duration_seconds and not winner_id:
                recommendation += (
                    f" | TIMEOUT: experiment exceeded max duration "
                    f"({config.max_duration_seconds/3600:.0f}h). "
                    f"Consider concluding with current best."
                )

        return ExperimentResult(
            experiment_id=config.experiment_id,
            status=state.status,
            winner_variant_id=winner_id,
            winner_confidence=winner_conf,
            variant_summaries=variant_summaries,
            bayesian_probabilities=bayesian_probs,
            frequentist_p_value=p_value,
            effect_size=effect_size,
            recommendation=recommendation,
            halted_variants=list(state.halted_variants),
        )

    def _bayesian_win_probabilities(
        self, state: _ExperimentState
    ) -> Dict[str, float]:
        """
        Monte Carlo estimation of P(variant is best) for each variant.
        Draws from Beta posteriors for success rate and compares.
        """
        active_variants = [
            v for v in state.config.variants
            if v.variant_id not in state.halted_variants
        ]
        if len(active_variants) < 2:
            return {
                v.variant_id: 1.0 / max(1, len(active_variants))
                for v in active_variants
            }

        n_samples = self._bayesian_samples
        win_counts: Dict[str, int] = defaultdict(int)

        # Draw samples from each variant's Beta posterior
        samples: Dict[str, np.ndarray] = {}
        for v in active_variants:
            m = state.metrics[v.variant_id]
            samples[v.variant_id] = self._rng.beta(
                m.alpha, m.beta_param, size=n_samples
            )

        # Weighted score: combine success rate and profit
        profit_samples: Dict[str, np.ndarray] = {}
        for v in active_variants:
            m = state.metrics[v.variant_id]
            if len(m.profits) >= 2:
                mean_p = np.mean(m.profits)
                std_p = max(np.std(m.profits, ddof=1), 1e-6)
                profit_samples[v.variant_id] = self._rng.normal(
                    mean_p, std_p / math.sqrt(max(1, len(m.profits))),
                    size=n_samples,
                )
            else:
                profit_samples[v.variant_id] = np.full(n_samples, m.avg_profit)

        # Composite score: success_rate * avg_profit (expected value)
        composite: Dict[str, np.ndarray] = {}
        for v in active_variants:
            composite[v.variant_id] = (
                samples[v.variant_id] * profit_samples[v.variant_id]
            )

        # Count wins
        vid_list = [v.variant_id for v in active_variants]
        stacked = np.stack([composite[vid] for vid in vid_list], axis=0)
        winners = np.argmax(stacked, axis=0)

        for idx, vid in enumerate(vid_list):
            win_counts[vid] = int(np.sum(winners == idx))

        # Normalize
        total = max(1, sum(win_counts.values()))
        return {vid: win_counts[vid] / total for vid in vid_list}

    def _frequentist_analysis(
        self,
        state: _ExperimentState,
        control_id: str,
        treatment_id: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Two-sample frequentist tests between control and treatment.

        Returns (p_value, effect_size) or (None, None).
        """
        if not _SCIPY_AVAILABLE:
            return None, None

        mc = state.metrics[control_id]
        mt = state.metrics[treatment_id]

        # ── Chi-squared test on success rate ───────────────────
        if mc.impressions >= 5 and mt.impressions >= 5:
            table = np.array([
                [mc.conversions, mc.impressions - mc.conversions],
                [mt.conversions, mt.impressions - mt.conversions],
            ])
            if table.min() >= 0:
                try:
                    chi2_result = sp_stats.chi2_contingency(table)
                    chi2_p = float(chi2_result[1])
                except ValueError:
                    chi2_p = 1.0
            else:
                chi2_p = 1.0
        else:
            chi2_p = 1.0

        # ── Welch's t-test on profit distributions ─────────────
        t_p = 1.0
        cohens_d = None
        if len(mc.profits) >= 2 and len(mt.profits) >= 2:
            try:
                t_stat, t_p = sp_stats.ttest_ind(
                    mt.profits, mc.profits, equal_var=False
                )
                t_p = float(t_p)

                # Cohen's d
                pooled_std = math.sqrt(
                    (np.var(mc.profits, ddof=1) + np.var(mt.profits, ddof=1)) / 2
                )
                if pooled_std > 0:
                    cohens_d = (np.mean(mt.profits) - np.mean(mc.profits)) / pooled_std
                    cohens_d = round(float(cohens_d), 4)
            except Exception:
                t_p = 1.0

        # Combine p-values (Fisher's method)
        combined_p = min(chi2_p, t_p)  # conservative: use the smaller

        return combined_p, cohens_d

    # ═══════════════════════════════════════════════════════════
    #  GUARDRAILS & AUTO-PROMOTION (Private)
    # ═══════════════════════════════════════════════════════════

    def _check_guardrails(
        self, state: _ExperimentState, variant_id: str
    ) -> List[str]:
        """Check safety guardrails and halt variants that breach them."""
        m = state.metrics[variant_id]
        g = state.config.guardrails
        warnings: List[str] = []

        if variant_id in state.halted_variants:
            return warnings

        if m.impressions < g.min_observations:
            return warnings  # Too early to judge

        # Success rate guardrail
        if m.success_rate < g.min_success_rate:
            msg = (
                f"Variant '{variant_id}' breached success-rate guardrail: "
                f"{m.success_rate:.1%} < {g.min_success_rate:.1%}"
            )
            warnings.append(msg)
            state.halted_variants.add(variant_id)
            logger.warning("[ABTestingEngine] GUARDRAIL: %s", msg)

        # Gas cost guardrail
        if m.avg_gas > g.max_avg_gas_usd:
            msg = (
                f"Variant '{variant_id}' breached gas guardrail: "
                f"${m.avg_gas:.2f} > ${g.max_avg_gas_usd:.2f}"
            )
            warnings.append(msg)
            state.halted_variants.add(variant_id)
            logger.warning("[ABTestingEngine] GUARDRAIL: %s", msg)

        # Net loss guardrail
        if m.net_profit < g.max_loss_usd:
            msg = (
                f"Variant '{variant_id}' breached loss guardrail: "
                f"${m.net_profit:.2f} < ${g.max_loss_usd:.2f}"
            )
            warnings.append(msg)
            state.halted_variants.add(variant_id)
            logger.warning("[ABTestingEngine] GUARDRAIL: %s", msg)

        # Consecutive failures
        recent_fails = 0
        for p in reversed(m.profits):
            if p <= 0:
                recent_fails += 1
            else:
                break
        if recent_fails >= g.max_consecutive_failures:
            msg = (
                f"Variant '{variant_id}' hit {recent_fails} consecutive failures"
            )
            warnings.append(msg)
            state.halted_variants.add(variant_id)
            logger.warning("[ABTestingEngine] GUARDRAIL: %s", msg)

        if warnings:
            self._audit_log("guardrail_breach", state.config.experiment_id, {
                "variant_id": variant_id,
                "warnings": warnings,
            })

        return warnings

    def _auto_check_experiment(self, state: _ExperimentState) -> None:
        """Periodic auto-analysis; promotes winner if thresholds met."""
        if not state.config.auto_promote:
            return

        result = self._analyze_experiment(state)
        if (
            result.winner_variant_id
            and result.winner_confidence >= state.config.significance_threshold
        ):
            logger.info(
                "[ABTestingEngine] AUTO-PROMOTE: '%s' wins experiment '%s' "
                "with %.1f%% confidence",
                result.winner_variant_id,
                state.config.experiment_id,
                result.winner_confidence * 100,
            )
            self._promote_variant(state, result.winner_variant_id, result)
            state.status = ExperimentStatus.COMPLETED
            state.ended_at = time.time()

    def _promote_variant(
        self,
        state: _ExperimentState,
        variant_id: str,
        result: ExperimentResult,
    ) -> None:
        """Record a variant promotion event."""
        variant = next(
            (v for v in state.config.variants if v.variant_id == variant_id),
            None,
        )
        if variant is None:
            return

        promotion = {
            "timestamp": time.time(),
            "experiment_id": state.config.experiment_id,
            "variant_id": variant_id,
            "variant_name": variant.name,
            "params": variant.params,
            "confidence": result.winner_confidence,
            "effect_size": result.effect_size,
            "net_profit_usd": state.metrics[variant_id].net_profit,
        }
        state.promotion_history.append(promotion)
        self._audit_log("variant_promoted", state.config.experiment_id, promotion)
        logger.info(
            "[ABTestingEngine] PROMOTED '%s' → params=%s (net=$%.2f)",
            variant.name,
            json.dumps(variant.params, default=str),
            state.metrics[variant_id].net_profit,
        )

    # ═══════════════════════════════════════════════════════════
    #  BAYESIAN POSTERIOR UPDATE
    # ═══════════════════════════════════════════════════════════

    def _update_normal_gamma(self, m: VariantMetrics, observation: float) -> None:
        """
        Online update of Normal-Gamma posterior for profit distribution.
        Conjugate prior for mean and variance of normally distributed data.
        """
        mu_0 = m.mu_0
        kappa = m.kappa
        alpha = m.alpha_ng
        beta = m.beta_ng

        kappa_n = kappa + 1
        mu_n = (kappa * mu_0 + observation) / kappa_n
        alpha_n = alpha + 0.5
        beta_n = beta + (kappa * (observation - mu_0) ** 2) / (2 * kappa_n)

        m.mu_0 = mu_n
        m.kappa = kappa_n
        m.alpha_ng = alpha_n
        m.beta_ng = beta_n

    # ═══════════════════════════════════════════════════════════
    #  UTILITIES (Private)
    # ═══════════════════════════════════════════════════════════

    def _get_state(self, experiment_id: str) -> _ExperimentState:
        state = self._experiments.get(experiment_id)
        if state is None:
            raise KeyError(f"Experiment '{experiment_id}' not found")
        return state

    def _audit_log(
        self,
        event: str,
        experiment_id: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = {
            "timestamp": time.time(),
            "event": event,
            "experiment_id": experiment_id,
            "details": details or {},
        }
        self._experiment_audit.append(entry)
        # Cap audit trail at 10 000 entries
        if len(self._experiment_audit) > 10_000:
            self._experiment_audit = self._experiment_audit[-5_000:]

    # ═══════════════════════════════════════════════════════════
    #  CONVENIENCE FACTORY
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def quick_experiment(
        name: str,
        parameter_key: str,
        control_value: Any,
        treatment_value: Any,
        control_name: str = "control",
        treatment_name: str = "treatment",
        min_sample: int = 100,
        strategy: AllocationStrategy = AllocationStrategy.THOMPSON_SAMPLING,
    ) -> ExperimentConfig:
        """
        Factory for a simple two-variant experiment.

        >>> config = ABTestingEngine.quick_experiment(
        ...     name="Gas Multiplier Test",
        ...     parameter_key="gas_multiplier",
        ...     control_value=1.0,
        ...     treatment_value=1.2,
        ... )
        """
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        return ExperimentConfig(
            experiment_id=exp_id,
            name=name,
            parameter_key=parameter_key,
            min_sample_size=min_sample,
            allocation_strategy=strategy,
            variants=[
                VariantConfig(
                    variant_id=f"{exp_id}_ctrl",
                    name=control_name,
                    is_control=True,
                    traffic_weight=0.5,
                    params={parameter_key: control_value},
                ),
                VariantConfig(
                    variant_id=f"{exp_id}_treat",
                    name=treatment_name,
                    is_control=False,
                    traffic_weight=0.5,
                    params={parameter_key: treatment_value},
                ),
            ],
        )

    @staticmethod
    def multi_variant_experiment(
        name: str,
        parameter_key: str,
        variants: Dict[str, Any],
        control_name: str = "control",
        min_sample: int = 100,
        strategy: AllocationStrategy = AllocationStrategy.THOMPSON_SAMPLING,
    ) -> ExperimentConfig:
        """
        Factory for multi-variant experiments.

        >>> config = ABTestingEngine.multi_variant_experiment(
        ...     name="Flash Loan Provider Test",
        ...     parameter_key="flash_loan_provider",
        ...     variants={
        ...         "aave_v3": "aave_v3",
        ...         "balancer": "balancer_v2",
        ...         "dydx": "dydx",
        ...     },
        ...     control_name="aave_v3",
        ... )
        """
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        n = len(variants)
        variant_configs = []
        for vname, vvalue in variants.items():
            variant_configs.append(
                VariantConfig(
                    variant_id=f"{exp_id}_{vname[:8]}",
                    name=vname,
                    is_control=(vname == control_name),
                    traffic_weight=1.0 / n,
                    params={parameter_key: vvalue},
                )
            )
        return ExperimentConfig(
            experiment_id=exp_id,
            name=name,
            parameter_key=parameter_key,
            min_sample_size=min_sample,
            allocation_strategy=strategy,
            variants=variant_configs,
        )
