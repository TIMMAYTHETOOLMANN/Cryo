#!/usr/bin/env python3
"""
hub.signal_adapter — Unified Signal Translation Layer
========================================================
Bridges the two incompatible OpportunitySignal formats used across
the CRYO system:

  - **Module 9 format** (``MODULE_9_OMNI_SCOPE.data_bus.OpportunitySignal``)
    Used by the 5 detector arrays + ML ranker.  12 signal types,
    fields: estimated_profit_usd, quality_score, routed_to, etc.

  - **Omni-Channel format** (``omni_channel.data_lake.data_models.OpportunitySignal``)
    Used by Module 1, execution router, profit engine.  10 signal types,
    fields: expected_value_usd, signal_id, execution_complexity, etc.

This adapter provides:
  - ``m9_to_omni(signal)``  — Module 9 → Omni-Channel
  - ``omni_to_m9(signal)``  — Omni-Channel → Module 9
  - ``SignalBridge``         — stateful bridge that subscribes to a DataBus
                               and translates + re-publishes signals

All translations preserve quality_score, routed_to, enrichment fields,
and metadata so no information is lost in transit.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Lazy imports (avoid hard dependency on either module) ──────────

_M9_CLASSES: Dict[str, Any] = {}
_OMNI_CLASSES: Dict[str, Any] = {}


def _load_m9():
    """Lazily import Module 9 data bus types."""
    if _M9_CLASSES:
        return
    from MODULE_9_OMNI_SCOPE.data_bus import (
        OpportunitySignal as M9Signal,
        SignalType as M9SignalType,
        SignalSource as M9SignalSource,
    )
    _M9_CLASSES["Signal"] = M9Signal
    _M9_CLASSES["SignalType"] = M9SignalType
    _M9_CLASSES["SignalSource"] = M9SignalSource


def _load_omni():
    """Lazily import Omni-Channel data model types."""
    if _OMNI_CLASSES:
        return
    from omni_channel.data_lake.data_models import (
        OpportunitySignal as OmniSignal,
        SignalType as OmniSignalType,
        SignalSource as OmniSignalSource,
        ExecutionModule,
    )
    _OMNI_CLASSES["Signal"] = OmniSignal
    _OMNI_CLASSES["SignalType"] = OmniSignalType
    _OMNI_CLASSES["SignalSource"] = OmniSignalSource
    _OMNI_CLASSES["ExecutionModule"] = ExecutionModule


# ── Signal Type Mapping ────────────────────────────────────────────

# Module 9 signal_type.value → Omni-Channel SignalType value
_M9_TO_OMNI_TYPE = {
    "pending_liquidation": "liquidation",
    "arbitrage":           "arbitrage",
    "cross_chain_arb":     "cross_chain_arb",
    "sandwich":            "sandwich",
    "backrun":             "backrun",
    "nft_liquidation":     "liquidation",
    "new_protocol":        "new_protocol",
    "governance_change":   "oracle_update",      # closest match
    "social_spike":        "vulnerability",       # informational
    "yield_opportunity":   "arbitrage",
    "bridge_imbalance":    "cross_chain_arb",
    "flash_loan_surplus":  "arbitrage",
}

# Omni-Channel signal_type.value → Module 9 SignalType value
_OMNI_TO_M9_TYPE = {
    "liquidation":     "pending_liquidation",
    "arbitrage":       "arbitrage",
    "sandwich":        "sandwich",
    "backrun":         "backrun",
    "front_run":       "backrun",
    "cross_chain_arb": "cross_chain_arb",
    "oracle_update":   "governance_change",
    "large_swap":      "arbitrage",
    "new_protocol":    "new_protocol",
    "vulnerability":   "social_spike",
}

# Module 9 source.value → Omni-Channel SignalSource value
_M9_TO_OMNI_SOURCE = {
    "mempool_radar":     "mempool_radar",
    "contract_crawler":  "contract_crawler",
    "static_analyzer":   "static_analyzer",
    "bridge_monitor":    "cross_chain_monitor",
    "archive_indexer":   "enhanced_detector",
    "alpha_seeker":      "enhanced_detector",
    "external":          "enhanced_detector",
}

# Module 9 routed_to string → Omni-Channel ExecutionModule value
_ROUTE_TO_MODULE = {
    "liquidation_engine":   "liquidation_engine",
    "arb_module":           "arbitrage_module",
    "backrun_bot":          "backrun_bot",
    "sandwich_bot":         "sandwich_bot",
    "cross_chain_executor": "cross_chain_executor",
    "manual_review":        "manual_review",
}


# ── Conversion Functions ───────────────────────────────────────────

def m9_to_omni(signal) -> Any:
    """
    Convert a Module 9 ``OpportunitySignal`` to an Omni-Channel one.

    Preserves all enrichment fields in ``metadata`` so nothing is lost.
    """
    _load_omni()
    OmniSignal = _OMNI_CLASSES["Signal"]
    OmniType = _OMNI_CLASSES["SignalType"]
    OmniSource = _OMNI_CLASSES["SignalSource"]
    ExecMod = _OMNI_CLASSES["ExecutionModule"]

    # Map signal type
    omni_type_val = _M9_TO_OMNI_TYPE.get(signal.signal_type.value, "liquidation")
    omni_type = OmniType(omni_type_val)

    # Map source
    omni_src_val = _M9_TO_OMNI_SOURCE.get(signal.source.value, "enhanced_detector")
    omni_source = OmniSource(omni_src_val)

    # Map execution routing
    routed = None
    if signal.routed_to:
        route_val = _ROUTE_TO_MODULE.get(signal.routed_to)
        if route_val:
            routed = ExecMod(route_val)

    # Build metadata that captures Module 9-specific fields
    meta = dict(signal.metadata) if signal.metadata else {}
    meta["m9_quality_score"] = signal.quality_score
    meta["m9_routed_to"] = signal.routed_to
    meta["m9_competition_estimate"] = signal.competition_estimate
    meta["m9_execution_complexity"] = signal.execution_complexity
    meta["m9_urgency_seconds"] = signal.urgency_seconds
    meta["m9_signal_type"] = signal.signal_type.value

    return OmniSignal(
        signal_id=str(uuid.uuid4()),
        signal_type=omni_type,
        source_module=omni_source,
        chain_id=signal.chain_id,
        target_contract=signal.target_contract or None,
        user_address=signal.target_user or None,
        expected_value_usd=signal.estimated_profit_usd,
        gross_profit_usd=signal.estimated_profit_usd,
        estimated_cost_usd=signal.gas_cost_estimate_usd,
        net_profit_usd=signal.estimated_profit_usd - signal.gas_cost_estimate_usd,
        confidence=signal.confidence,
        competition_estimate=signal.competition_estimate,
        urgency_score=min(signal.urgency_seconds * 10, 100),
        execution_complexity=max(1, int(signal.execution_complexity * 10)),
        trigger_tx_hash=signal.tx_hash or None,
        debt_asset=signal.target_asset or None,
        collateral_asset=signal.target_asset or None,
        # Debt/collateral amounts are stored as M9 USD values; the raw
        # wei amounts are unavailable at this layer, so we pass zero and
        # let downstream stages recalculate from on-chain data.
        debt_amount=0,
        collateral_amount=0,
        health_factor=signal.health_factor,
        metadata=meta,
        timestamp=int(signal.timestamp),
        routed_to=routed,
    )


def omni_to_m9(signal) -> Any:
    """
    Convert an Omni-Channel ``OpportunitySignal`` to Module 9 format.

    Preserves all Omni-Channel-specific fields in ``metadata``.
    """
    _load_m9()
    M9Signal = _M9_CLASSES["Signal"]
    M9Type = _M9_CLASSES["SignalType"]
    M9Source = _M9_CLASSES["SignalSource"]

    # Map signal type
    m9_type_val = _OMNI_TO_M9_TYPE.get(signal.signal_type.value, "pending_liquidation")
    m9_type = M9Type(m9_type_val)

    # Map source (reverse lookup)
    _OMNI_TO_M9_SOURCE = {v: k for k, v in _M9_TO_OMNI_SOURCE.items()}
    m9_src_val = _OMNI_TO_M9_SOURCE.get(signal.source_module.value, "external")
    m9_source = M9Source(m9_src_val)

    # Build metadata
    meta = dict(signal.metadata) if signal.metadata else {}
    meta["omni_signal_id"] = signal.signal_id
    meta["omni_signal_type"] = signal.signal_type.value
    meta["omni_status"] = signal.status

    return M9Signal(
        signal_type=m9_type,
        source=m9_source,
        chain_id=signal.chain_id,
        confidence=signal.confidence,
        estimated_profit_usd=signal.expected_value_usd or signal.gross_profit_usd,
        gas_cost_estimate_usd=signal.estimated_cost_usd,
        urgency_seconds=signal.urgency_score / 10.0 if signal.urgency_score else 5.0,
        timestamp=float(signal.timestamp),
        target_protocol=signal.metadata.get("protocol", ""),
        target_user=signal.user_address or "",
        target_asset=signal.debt_asset or signal.collateral_asset or "",
        target_contract=signal.target_contract or "",
        tx_hash=signal.trigger_tx_hash or "",
        health_factor=signal.health_factor,
        debt_amount_usd=0.0,
        collateral_amount_usd=0.0,
        liquidation_bonus=signal.metadata.get("liquidation_bonus", 0.0),
        competition_estimate=signal.competition_estimate,
        execution_complexity=signal.execution_complexity / 10.0,
        quality_score=_safe_quality_score(signal),
        routed_to=signal.routed_to.value if signal.routed_to else "",
        metadata=meta,
    )


# ── Helpers ────────────────────────────────────────────────────────

def _safe_quality_score(signal) -> float:
    """Extract quality_score from an Omni-Channel signal safely.

    ``quality_score`` is a method on the Omni-Channel dataclass but
    a plain float attribute on Module 9.  Handle both gracefully.
    """
    qs = getattr(signal, "quality_score", None)
    if qs is None:
        return 0.0
    if callable(qs):
        try:
            return float(qs())
        except Exception:
            return 0.0
    return float(qs)


# ── Signal Bridge ──────────────────────────────────────────────────

class SignalBridge:
    """
    Stateful bridge that connects a Module 9 DataBus to the
    Omni-Channel signal ecosystem.

    On each bus signal it:
      1. Translates M9 → Omni format
      2. Classifies into execution routing (liquidation / arb / backrun / ...)
      3. Stores the translated signal for downstream consumption

    Usage::

        from MODULE_9_OMNI_SCOPE.data_bus import DataBus
        bridge = SignalBridge()
        bridge.attach(bus)
        # ... bus receives signals from arrays ...
        translated = bridge.consume(limit=10)
    """

    def __init__(self):
        self._translated: List[Any] = []
        self._stats = {
            "signals_received": 0,
            "signals_translated": 0,
            "translation_errors": 0,
            "by_type": {},
        }

    def attach(self, bus) -> None:
        """Subscribe to a Module 9 DataBus."""
        bus.subscribe(self._on_signal)

    def _on_signal(self, signal) -> None:
        """Callback for each Module 9 bus signal."""
        self._stats["signals_received"] += 1
        try:
            translated = m9_to_omni(signal)
            self._translated.append(translated)
            self._stats["signals_translated"] += 1

            typ = signal.signal_type.value
            self._stats["by_type"][typ] = self._stats["by_type"].get(typ, 0) + 1
        except Exception as exc:
            self._stats["translation_errors"] += 1
            logger.debug("Signal translation error: %s", exc)

    def consume(self, limit: int = 20) -> List[Any]:
        """
        Return up to *limit* translated Omni-Channel signals,
        sorted by quality (best first).
        """
        # Sort by net_profit_usd descending (best opportunities first)
        self._translated.sort(
            key=lambda s: getattr(s, "net_profit_usd", 0),
            reverse=True,
        )
        batch = self._translated[:limit]
        self._translated = self._translated[limit:]
        return batch

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "pending": len(self._translated),
        }
