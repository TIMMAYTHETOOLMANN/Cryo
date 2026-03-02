#!/usr/bin/env python3
"""
STAGE 7 — Analytics Engine (Script 2 — Module 8)
==================================================
Real-time analytics, A/B testing of strategies, and anomaly detection.

NEW MODULE (Script 2):
  1. Live Profit Dashboard Data — feeds Grafana / built-in dashboard
  2. A/B Testing — compare executor configs, auto-switch to best
  3. Anomaly Detection — profit margin monitoring + alerts

Zero capital required — pure computation over execution history.
"""

import logging
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ExecutionRecord:
    """Single execution result for analytics."""
    timestamp: float
    chain_id: int
    protocol: str
    collateral_asset: str
    debt_amount_usd: float
    profit_usd: float
    gas_cost_usd: float
    flash_loan_fee_usd: float
    flash_loan_provider: str
    exit_strategy: str
    success: bool
    latency_ms: float = 0.0
    config_variant: str = "default"


@dataclass
class ChainMetrics:
    """Aggregated metrics per chain."""
    chain_id: int
    chain_name: str
    executions: int = 0
    successes: int = 0
    total_profit_usd: float = 0.0
    total_gas_usd: float = 0.0
    avg_profit_per_liq: float = 0.0
    avg_gas_per_liq: float = 0.0
    success_rate: float = 0.0
    liquidations_per_hour: float = 0.0


@dataclass
class ProtocolMetrics:
    """Aggregated metrics per protocol."""
    protocol: str
    executions: int = 0
    successes: int = 0
    total_profit_usd: float = 0.0
    avg_profit: float = 0.0
    most_profitable_asset: str = ""


@dataclass
class ABTestResult:
    """Result of comparing two strategy variants."""
    variant_a: str
    variant_b: str
    a_avg_profit: float
    b_avg_profit: float
    a_count: int
    b_count: int
    winner: str
    confidence: float  # 0-1 rough confidence based on sample size


class AnomalyType(Enum):
    PROFIT_DROP = "profit_drop"
    SUCCESS_RATE_DROP = "success_rate_drop"
    GAS_SPIKE = "gas_spike"
    NEW_COMPETITOR = "new_competitor"


@dataclass
class Anomaly:
    """Detected anomaly in execution metrics."""
    anomaly_type: AnomalyType
    severity: str  # "warning", "critical"
    message: str
    timestamp: float
    metric_value: float
    expected_value: float


# ---------------------------------------------------------------------------
# Analytics Engine
# ---------------------------------------------------------------------------

class AnalyticsEngine:
    """
    Real-time analytics, A/B testing, and anomaly detection.

    Consumes ExecutionRecords from the pipeline and provides:
    - Per-chain, per-protocol, per-provider aggregated metrics
    - A/B test comparisons between config variants
    - Anomaly detection on profit margins, success rates, gas
    - Auto-tuning recommendations (e.g., raise MIN_DEBT on expensive chains)
    """

    ANOMALY_WINDOW = 50         # Rolling window for anomaly detection
    PROFIT_DROP_THRESHOLD = 0.5  # Alert if avg profit drops to 50% of baseline
    SUCCESS_RATE_THRESHOLD = 0.6 # Alert if success rate drops below 60%
    GAS_SPIKE_MULTIPLIER = 3.0   # Alert if gas > 3x recent average

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._records: List[ExecutionRecord] = []
        self._start_time = time.time()

        # Rolling windows for anomaly detection
        self._profit_window: deque = deque(maxlen=self.ANOMALY_WINDOW)
        self._gas_window: deque = deque(maxlen=self.ANOMALY_WINDOW)
        self._success_window: deque = deque(maxlen=self.ANOMALY_WINDOW)

        # Baseline (established after first N executions)
        self._baseline_profit: Optional[float] = None
        self._baseline_gas: Optional[float] = None
        self._baseline_success_rate: Optional[float] = None
        self._baseline_samples = 10

        # A/B test tracking
        self._variant_records: Dict[str, List[ExecutionRecord]] = defaultdict(list)

        # Detected anomalies
        self.anomalies: List[Anomaly] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, rec: ExecutionRecord):
        """Record an execution result."""
        self._records.append(rec)
        self._variant_records[rec.config_variant].append(rec)

        # Update rolling windows
        self._profit_window.append(rec.profit_usd)
        self._gas_window.append(rec.gas_cost_usd)
        self._success_window.append(1.0 if rec.success else 0.0)

        # Establish baselines after enough samples
        if len(self._profit_window) >= self._baseline_samples:
            if self._baseline_profit is None:
                self._baseline_profit = sum(self._profit_window) / len(self._profit_window)
                self._baseline_gas = sum(self._gas_window) / len(self._gas_window)
                self._baseline_success_rate = sum(self._success_window) / len(self._success_window)
                logger.info(
                    f"📊 Analytics baseline: profit=${self._baseline_profit:.2f} "
                    f"gas=${self._baseline_gas:.2f} success={self._baseline_success_rate:.0%}"
                )

        # Check for anomalies
        anomaly = self._detect_anomaly(rec)
        if anomaly:
            self.anomalies.append(anomaly)
            if anomaly.severity == "critical":
                logger.warning(f"🚨 ANOMALY: {anomaly.message}")
            else:
                logger.info(f"⚠️  Anomaly: {anomaly.message}")

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def chain_metrics(self) -> Dict[int, ChainMetrics]:
        """Aggregate metrics per chain."""
        chains: Dict[int, ChainMetrics] = {}
        uptime_hours = max((time.time() - self._start_time) / 3600, 0.001)

        for rec in self._records:
            if rec.chain_id not in chains:
                chain_cfg = self.config.get_chain(rec.chain_id)
                name = chain_cfg.name if chain_cfg else str(rec.chain_id)
                chains[rec.chain_id] = ChainMetrics(chain_id=rec.chain_id, chain_name=name)

            m = chains[rec.chain_id]
            m.executions += 1
            if rec.success:
                m.successes += 1
                m.total_profit_usd += rec.profit_usd
            m.total_gas_usd += rec.gas_cost_usd

        for m in chains.values():
            m.success_rate = m.successes / max(m.executions, 1)
            m.avg_profit_per_liq = m.total_profit_usd / max(m.successes, 1)
            m.avg_gas_per_liq = m.total_gas_usd / max(m.executions, 1)
            m.liquidations_per_hour = m.executions / uptime_hours

        return chains

    def protocol_metrics(self) -> Dict[str, ProtocolMetrics]:
        """Aggregate metrics per protocol."""
        protos: Dict[str, ProtocolMetrics] = {}

        for rec in self._records:
            if rec.protocol not in protos:
                protos[rec.protocol] = ProtocolMetrics(protocol=rec.protocol)
            m = protos[rec.protocol]
            m.executions += 1
            if rec.success:
                m.successes += 1
                m.total_profit_usd += rec.profit_usd

        for m in protos.values():
            m.avg_profit = m.total_profit_usd / max(m.successes, 1)

        return protos

    def summary(self) -> Dict:
        """Overall summary for dashboard."""
        uptime = time.time() - self._start_time
        total_exec = len(self._records)
        successes = sum(1 for r in self._records if r.success)
        total_profit = sum(r.profit_usd for r in self._records if r.success)
        total_gas = sum(r.gas_cost_usd for r in self._records)

        return {
            "uptime_hours": uptime / 3600,
            "total_executions": total_exec,
            "total_successes": successes,
            "success_rate": successes / max(total_exec, 1),
            "total_profit_usd": total_profit,
            "total_gas_usd": total_gas,
            "net_profit_usd": total_profit - total_gas,
            "avg_profit_per_liq": total_profit / max(successes, 1),
            "liquidations_per_hour": total_exec / max(uptime / 3600, 0.001),
            "anomalies_detected": len(self.anomalies),
            "active_variants": list(self._variant_records.keys()),
        }

    # ------------------------------------------------------------------
    # A/B Testing
    # ------------------------------------------------------------------

    def ab_test(self, variant_a: str = "default", variant_b: str = "experimental") -> Optional[ABTestResult]:
        """Compare two strategy variants."""
        recs_a = [r for r in self._variant_records.get(variant_a, []) if r.success]
        recs_b = [r for r in self._variant_records.get(variant_b, []) if r.success]

        if not recs_a or not recs_b:
            return None

        avg_a = sum(r.profit_usd for r in recs_a) / len(recs_a)
        avg_b = sum(r.profit_usd for r in recs_b) / len(recs_b)

        # Simple confidence based on sample size (rough approximation)
        n = min(len(recs_a), len(recs_b))
        confidence = min(1.0, n / 30)  # ~30 samples for decent confidence

        winner = variant_a if avg_a >= avg_b else variant_b

        return ABTestResult(
            variant_a=variant_a, variant_b=variant_b,
            a_avg_profit=avg_a, b_avg_profit=avg_b,
            a_count=len(recs_a), b_count=len(recs_b),
            winner=winner, confidence=confidence,
        )

    def get_best_variant(self) -> str:
        """Return the variant with highest average profit."""
        if not self._variant_records:
            return "default"

        best_variant = "default"
        best_avg = -float("inf")

        for variant, records in self._variant_records.items():
            successes = [r for r in records if r.success]
            if successes:
                avg = sum(r.profit_usd for r in successes) / len(successes)
                if avg > best_avg:
                    best_avg = avg
                    best_variant = variant

        return best_variant

    # ------------------------------------------------------------------
    # Anomaly Detection
    # ------------------------------------------------------------------

    def _detect_anomaly(self, rec: ExecutionRecord) -> Optional[Anomaly]:
        """Check if the latest record is anomalous."""
        if self._baseline_profit is None:
            return None  # Not enough data yet

        now = time.time()

        # 1. Profit drop
        if len(self._profit_window) >= 5:
            recent_avg = sum(list(self._profit_window)[-5:]) / 5
            if recent_avg < self._baseline_profit * self.PROFIT_DROP_THRESHOLD:
                return Anomaly(
                    anomaly_type=AnomalyType.PROFIT_DROP,
                    severity="critical",
                    message=(
                        f"Profit dropped to ${recent_avg:.2f}/liq "
                        f"(baseline: ${self._baseline_profit:.2f})"
                    ),
                    timestamp=now,
                    metric_value=recent_avg,
                    expected_value=self._baseline_profit,
                )

        # 2. Success rate drop
        if len(self._success_window) >= 10:
            recent_rate = sum(list(self._success_window)[-10:]) / 10
            if self._baseline_success_rate and recent_rate < self.SUCCESS_RATE_THRESHOLD:
                return Anomaly(
                    anomaly_type=AnomalyType.SUCCESS_RATE_DROP,
                    severity="warning",
                    message=(
                        f"Success rate dropped to {recent_rate:.0%} "
                        f"(baseline: {self._baseline_success_rate:.0%})"
                    ),
                    timestamp=now,
                    metric_value=recent_rate,
                    expected_value=self._baseline_success_rate,
                )

        # 3. Gas spike
        if self._baseline_gas and rec.gas_cost_usd > self._baseline_gas * self.GAS_SPIKE_MULTIPLIER:
            return Anomaly(
                anomaly_type=AnomalyType.GAS_SPIKE,
                severity="warning",
                message=(
                    f"Gas spike: ${rec.gas_cost_usd:.2f} "
                    f"({rec.gas_cost_usd / self._baseline_gas:.1f}x baseline)"
                ),
                timestamp=now,
                metric_value=rec.gas_cost_usd,
                expected_value=self._baseline_gas,
            )

        return None

    # ------------------------------------------------------------------
    # Auto-Tuning Recommendations
    # ------------------------------------------------------------------

    def get_tuning_recommendations(self) -> List[str]:
        """Generate parameter tuning suggestions based on analytics."""
        recs: List[str] = []
        chains = self.chain_metrics()

        for cid, m in chains.items():
            # Suggest raising MIN_DEBT if avg profit is low
            if m.avg_profit_per_liq > 0 and m.avg_profit_per_liq < 20:
                recs.append(
                    f"Chain {m.chain_name}: avg profit ${m.avg_profit_per_liq:.2f} is low — "
                    f"consider raising MIN_DEBT threshold"
                )

            # Suggest disabling chain if success rate is terrible
            if m.executions >= 10 and m.success_rate < 0.3:
                recs.append(
                    f"Chain {m.chain_name}: success rate {m.success_rate:.0%} — "
                    f"consider disabling or adjusting gas cap"
                )

            # Suggest gas cap adjustment
            if m.avg_gas_per_liq > m.avg_profit_per_liq * 0.5:
                recs.append(
                    f"Chain {m.chain_name}: gas ${m.avg_gas_per_liq:.2f} is >50% of "
                    f"profit ${m.avg_profit_per_liq:.2f} — lower gas cap"
                )

        return recs

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def print_summary(self):
        s = self.summary()
        print()
        print("=" * 80)
        print("  STAGE 7 — ANALYTICS ENGINE")
        print("=" * 80)
        print(f"  Uptime:              {s['uptime_hours']:.2f} hours")
        print(f"  Executions:          {s['total_executions']} ({s['total_successes']} successful)")
        print(f"  Success Rate:        {s['success_rate']:.0%}")
        print(f"  Total Profit:        ${s['total_profit_usd']:,.2f}")
        print(f"  Total Gas:           ${s['total_gas_usd']:,.2f}")
        print(f"  Net Profit:          ${s['net_profit_usd']:,.2f}")
        print(f"  Avg Profit/Liq:      ${s['avg_profit_per_liq']:.2f}")
        print(f"  Liqs/Hour:           {s['liquidations_per_hour']:.1f}")
        print(f"  Anomalies:           {s['anomalies_detected']}")

        # A/B test results
        if len(self._variant_records) > 1:
            variants = list(self._variant_records.keys())
            for i in range(len(variants)):
                for j in range(i + 1, len(variants)):
                    result = self.ab_test(variants[i], variants[j])
                    if result:
                        print(f"\n  A/B: {result.variant_a} vs {result.variant_b}")
                        print(f"    {result.variant_a}: ${result.a_avg_profit:.2f}/liq ({result.a_count} samples)")
                        print(f"    {result.variant_b}: ${result.b_avg_profit:.2f}/liq ({result.b_count} samples)")
                        print(f"    Winner: {result.winner} (confidence: {result.confidence:.0%})")

        # Tuning recommendations
        recs = self.get_tuning_recommendations()
        if recs:
            print(f"\n  ─── Auto-Tuning Recommendations ───")
            for r in recs:
                print(f"    💡 {r}")

        print("=" * 80)
