#!/usr/bin/env python3
"""
enhanced_modules.module_8_analytics_dashboard.anomaly_detector
================================================================
ML-based anomaly detection for profit margins, gas costs, and
competitor behavior.

Uses Isolation Forest (scikit-learn) trained on ExecutionRecord
history to detect:
  - Sudden profit drops (new competitor entering)
  - Gas price spikes (network congestion)
  - Front-running patterns (repeated execution failures)
  - Unusual success rate changes

Triggers alerts and can auto-adjust bidding strategies.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

import numpy as np

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import ExecutionRecord

logger = logging.getLogger(__name__)

try:
    from sklearn.ensemble import IsolationForest
    _SKLEARN_AVAILABLE = True
except ImportError:
    _SKLEARN_AVAILABLE = False


class AnomalyType(Enum):
    PROFIT_DROP = "profit_drop"
    GAS_SPIKE = "gas_spike"
    FRONTRUN_PATTERN = "frontrun_pattern"
    SUCCESS_RATE_CHANGE = "success_rate_change"
    VOLUME_ANOMALY = "volume_anomaly"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AnomalyAlert:
    """An anomaly detection alert."""
    alert_id: str
    anomaly_type: AnomalyType
    severity: AlertSeverity
    message: str
    current_value: float
    expected_range: tuple  # (min, max)
    recommended_action: str
    timestamp: float = field(default_factory=time.time)
    auto_mitigated: bool = False


@dataclass
class AnomalyFeatureVector:
    """Feature vector for anomaly detection."""
    avg_profit_usd: float
    avg_gas_usd: float
    success_rate: float
    exec_count: int
    avg_latency_ms: float
    frontrun_count: int
    unique_chains: int
    timestamp: float = field(default_factory=time.time)

    def to_array(self) -> np.ndarray:
        return np.array([
            self.avg_profit_usd,
            self.avg_gas_usd,
            self.success_rate,
            self.exec_count,
            self.avg_latency_ms,
            self.frontrun_count,
            self.unique_chains,
        ], dtype=np.float64)


class AnomalyDetector(EnhancedModule):
    """
    Isolation Forest-based anomaly detection for the liquidation engine.
    """

    # Detection parameters
    WINDOW_SIZE = 50        # Observations per feature vector
    CONTAMINATION = 0.05    # Expected anomaly rate
    MIN_TRAINING_SAMPLES = 100
    RE_TRAIN_INTERVAL = 500  # Re-train every N observations

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("anomaly_detector", config)
        self._model: Optional[IsolationForest] = None
        self._history: Deque[ExecutionRecord] = deque(maxlen=10000)
        self._feature_vectors: List[AnomalyFeatureVector] = []
        self._alerts: List[AnomalyAlert] = []
        self._observations_since_train = 0
        self._alert_count = 0

        # Thresholds for rule-based detection (fallback)
        self._profit_ma: Deque[float] = deque(maxlen=100)
        self._gas_ma: Deque[float] = deque(maxlen=100)
        self._success_window: Deque[bool] = deque(maxlen=50)

    async def _on_start(self) -> None:
        if not _SKLEARN_AVAILABLE:
            logger.warning("[AnomalyDetector] scikit-learn unavailable — using rule-based detection")
        logger.info("[AnomalyDetector] Initialized (contamination=%.2f)", self.CONTAMINATION)

    async def _on_stop(self) -> None:
        logger.info(
            "[AnomalyDetector] Total alerts: %d, History: %d observations",
            self._alert_count, len(self._history),
        )

    # ── Observation Ingestion ──────────────────────────────────

    def ingest(self, record: ExecutionRecord) -> List[AnomalyAlert]:
        """
        Ingest an execution record and check for anomalies.
        Returns list of new alerts (may be empty).
        """
        self._history.append(record)
        self._observations_since_train += 1

        # Update moving averages
        self._profit_ma.append(float(record.net_profit_usd))
        self._gas_ma.append(float(record.gas_cost_usd))
        self._success_window.append(record.status.value == "confirmed")

        alerts = []

        # Rule-based checks (always active)
        alerts.extend(self._check_profit_drop(record))
        alerts.extend(self._check_gas_spike(record))
        alerts.extend(self._check_success_rate())
        alerts.extend(self._check_frontrun_pattern(record))

        # ML-based check (when model is ready)
        if self._model and _SKLEARN_AVAILABLE:
            ml_alerts = self._check_ml_anomaly()
            alerts.extend(ml_alerts)

        # Retrain periodically
        if (
            self._observations_since_train >= self.RE_TRAIN_INTERVAL
            and len(self._history) >= self.MIN_TRAINING_SAMPLES
            and _SKLEARN_AVAILABLE
        ):
            self._train_model()
            self._observations_since_train = 0

        for alert in alerts:
            self._alerts.append(alert)
            self._alert_count += 1
            logger.warning(
                "[AnomalyDetector] ALERT [%s] %s: %s",
                alert.severity.value.upper(),
                alert.anomaly_type.value,
                alert.message,
            )

        return alerts

    # ── Rule-Based Checks ──────────────────────────────────────

    def _check_profit_drop(self, record: ExecutionRecord) -> List[AnomalyAlert]:
        if len(self._profit_ma) < 20:
            return []

        recent = list(self._profit_ma)
        avg_recent = np.mean(recent[-10:])
        avg_historical = np.mean(recent[:-10])

        if avg_historical > 0 and avg_recent < avg_historical * 0.5:
            return [AnomalyAlert(
                alert_id=f"profit_drop_{self._alert_count}",
                anomaly_type=AnomalyType.PROFIT_DROP,
                severity=AlertSeverity.WARNING,
                message=f"Profit dropped {((avg_historical - avg_recent)/avg_historical*100):.0f}%: "
                        f"${avg_recent:.2f} vs ${avg_historical:.2f} avg",
                current_value=avg_recent,
                expected_range=(avg_historical * 0.7, avg_historical * 1.3),
                recommended_action="Review competitor activity; consider adjusting min_profit threshold",
            )]
        return []

    def _check_gas_spike(self, record: ExecutionRecord) -> List[AnomalyAlert]:
        if len(self._gas_ma) < 10:
            return []

        recent_gas = list(self._gas_ma)
        avg_gas = np.mean(recent_gas[:-1]) if len(recent_gas) > 1 else recent_gas[0]
        current_gas = float(record.gas_cost_usd)

        if avg_gas > 0 and current_gas > avg_gas * 3:
            return [AnomalyAlert(
                alert_id=f"gas_spike_{self._alert_count}",
                anomaly_type=AnomalyType.GAS_SPIKE,
                severity=AlertSeverity.WARNING,
                message=f"Gas cost ${current_gas:.2f} is {current_gas/avg_gas:.1f}x average (${avg_gas:.2f})",
                current_value=current_gas,
                expected_range=(avg_gas * 0.5, avg_gas * 2),
                recommended_action="Increase gas price threshold or pause low-margin executions",
            )]
        return []

    def _check_success_rate(self) -> List[AnomalyAlert]:
        if len(self._success_window) < 20:
            return []

        success_rate = sum(1 for s in self._success_window if s) / len(self._success_window)

        if success_rate < 0.5:
            return [AnomalyAlert(
                alert_id=f"sr_drop_{self._alert_count}",
                anomaly_type=AnomalyType.SUCCESS_RATE_CHANGE,
                severity=AlertSeverity.CRITICAL if success_rate < 0.3 else AlertSeverity.WARNING,
                message=f"Success rate dropped to {success_rate*100:.0f}%",
                current_value=success_rate,
                expected_range=(0.7, 1.0),
                recommended_action="Check for front-running; verify protocol contracts; review gas settings",
            )]
        return []

    def _check_frontrun_pattern(self, record: ExecutionRecord) -> List[AnomalyAlert]:
        """Detect front-running patterns (repeated failures on profitable opportunities)."""
        if record.status.value != "reverted":
            return []

        # Check for pattern: profitable opportunity + revert = likely front-run
        if record.gross_profit_usd > 50 and record.error and "revert" in record.error.lower():
            recent_reverts = sum(
                1 for r in list(self._history)[-10:]
                if r.status.value == "reverted"
            )
            if recent_reverts >= 3:
                return [AnomalyAlert(
                    alert_id=f"frontrun_{self._alert_count}",
                    anomaly_type=AnomalyType.FRONTRUN_PATTERN,
                    severity=AlertSeverity.CRITICAL,
                    message=f"{recent_reverts}/10 recent reverts on profitable opportunities",
                    current_value=recent_reverts,
                    expected_range=(0, 2),
                    recommended_action="Switch to private mempool; increase priority fee; add bundle protection",
                )]
        return []

    # ── ML-Based Detection ─────────────────────────────────────

    def _train_model(self) -> None:
        """Train the Isolation Forest model on historical feature vectors."""
        if not _SKLEARN_AVAILABLE:
            return

        # Build feature vectors from history windows
        records = list(self._history)
        vectors = []

        for i in range(self.WINDOW_SIZE, len(records), self.WINDOW_SIZE // 2):
            window = records[i - self.WINDOW_SIZE:i]
            fv = self._build_feature_vector(window)
            vectors.append(fv.to_array())
            self._feature_vectors.append(fv)

        if len(vectors) < self.MIN_TRAINING_SAMPLES // self.WINDOW_SIZE:
            return

        X = np.array(vectors)
        self._model = IsolationForest(
            contamination=self.CONTAMINATION,
            n_estimators=100,
            max_samples="auto",
            random_state=42,
        )
        self._model.fit(X)
        logger.info("[AnomalyDetector] Trained IsolationForest on %d vectors", len(X))

    def _check_ml_anomaly(self) -> List[AnomalyAlert]:
        """Check if the latest window is anomalous."""
        if len(self._history) < self.WINDOW_SIZE:
            return []

        recent = list(self._history)[-self.WINDOW_SIZE:]
        fv = self._build_feature_vector(recent)
        X = fv.to_array().reshape(1, -1)

        prediction = self._model.predict(X)[0]
        score = self._model.decision_function(X)[0]

        if prediction == -1:  # Anomaly
            return [AnomalyAlert(
                alert_id=f"ml_anomaly_{self._alert_count}",
                anomaly_type=AnomalyType.VOLUME_ANOMALY,
                severity=AlertSeverity.WARNING,
                message=f"ML detected anomaly (score={score:.3f}): "
                        f"profit=${fv.avg_profit_usd:.2f}, sr={fv.success_rate:.0%}",
                current_value=score,
                expected_range=(-0.5, 0.5),
                recommended_action="Investigate recent execution patterns",
            )]
        return []

    def _build_feature_vector(self, records: List[ExecutionRecord]) -> AnomalyFeatureVector:
        """Build a feature vector from a window of execution records."""
        profits = [float(r.net_profit_usd) for r in records]
        gas_costs = [float(r.gas_cost_usd) for r in records]
        successes = sum(1 for r in records if r.status.value == "confirmed")
        chains = len(set(r.chain_id for r in records))
        frontrun = sum(1 for r in records if r.error and "revert" in (r.error or "").lower())

        return AnomalyFeatureVector(
            avg_profit_usd=float(np.mean(profits)) if profits else 0.0,
            avg_gas_usd=float(np.mean(gas_costs)) if gas_costs else 0.0,
            success_rate=successes / max(1, len(records)),
            exec_count=len(records),
            avg_latency_ms=0.0,
            frontrun_count=frontrun,
            unique_chains=chains,
        )

    # ── Query ──────────────────────────────────────────────────

    def get_recent_alerts(self, count: int = 20) -> List[Dict[str, Any]]:
        return [
            {
                "id": a.alert_id,
                "type": a.anomaly_type.value,
                "severity": a.severity.value,
                "message": a.message,
                "action": a.recommended_action,
                "timestamp": a.timestamp,
            }
            for a in self._alerts[-count:]
        ]

    def get_detection_stats(self) -> Dict[str, Any]:
        return {
            "total_alerts": self._alert_count,
            "total_observations": len(self._history),
            "model_trained": self._model is not None,
            "alerts_by_type": {
                t.value: sum(1 for a in self._alerts if a.anomaly_type == t)
                for t in AnomalyType
            },
        }
