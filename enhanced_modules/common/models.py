#!/usr/bin/env python3
"""
enhanced_modules.common.models — Shared Data Models
====================================================
Canonical dataclasses used by all 8 enhanced modules.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Chain Configuration ────────────────────────────────────────────

@dataclass
class ChainConfig:
    """Per-chain configuration for multi-chain operations."""
    chain_id: int
    name: str
    rpc_url: str
    ws_url: Optional[str] = None
    explorer_api: Optional[str] = None
    avg_block_time_ms: int = 12000
    gas_token: str = "ETH"
    flash_loan_providers: List[str] = field(default_factory=list)
    min_profit_usd: Decimal = Decimal("10")
    max_gas_price_gwei: float = 100.0
    enabled: bool = True


# ── Flash Loan Provider Profile ────────────────────────────────────

class FlashLoanProviderType(Enum):
    AAVE_V3 = "aave_v3"
    BALANCER_V2 = "balancer_v2"
    UNISWAP_V3 = "uniswap_v3"
    MAKER_DSS = "maker_dss"
    DYDX = "dydx"
    DODO = "dodo"
    EULER = "euler"


@dataclass
class ProviderProfile:
    """Describes a flash loan provider's characteristics."""
    provider_type: FlashLoanProviderType
    chain_id: int
    address: str
    fee_bps: int = 0  # basis points (0 = free)
    max_loan_usd: Decimal = Decimal("0")
    supported_assets: List[str] = field(default_factory=list)
    avg_success_rate: float = 1.0
    avg_gas_overhead: int = 0
    is_active: bool = True
    last_updated: float = field(default_factory=time.time)

    @property
    def fee_pct(self) -> float:
        return self.fee_bps / 10000.0


# ── Execution Record ──────────────────────────────────────────────

class ExecutionStatus(Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    REVERTED = "reverted"
    FAILED = "failed"


@dataclass
class ExecutionRecord:
    """Tracks a single liquidation execution lifecycle."""
    record_id: str
    chain_id: int
    protocol: str
    borrower: str
    debt_asset: str
    debt_amount: Decimal
    collateral_asset: str
    collateral_amount: Decimal
    flash_loan_provider: str
    flash_loan_fee: Decimal = Decimal("0")
    gas_used: int = 0
    gas_price_gwei: float = 0.0
    gas_cost_usd: Decimal = Decimal("0")
    gross_profit_usd: Decimal = Decimal("0")
    net_profit_usd: Decimal = Decimal("0")
    exit_strategy: str = "immediate_sell"
    status: ExecutionStatus = ExecutionStatus.PENDING
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── Price Feed ─────────────────────────────────────────────────────

@dataclass
class PriceFeed:
    """A price observation from an oracle or DEX."""
    asset: str
    price_usd: Decimal
    source: str  # "chainlink", "uniswap_twap", "dex_spot"
    chain_id: int = 1
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    block_number: Optional[int] = None


# ── Position Data ──────────────────────────────────────────────────

@dataclass
class EnrichedPosition:
    """A borrower position enriched with predictive features."""
    borrower: str
    protocol: str
    chain_id: int
    health_factor: float
    debt_usd: Decimal
    collateral_usd: Decimal
    debt_asset: str
    collateral_asset: str
    # Predictive features
    liquidation_probability: float = 0.0
    volatility_index: float = 0.0
    oracle_update_frequency: float = 0.0
    cross_protocol_exposure: Decimal = Decimal("0")
    estimated_bonus_usd: Decimal = Decimal("0")
    # Timing
    blocks_until_liquidatable: Optional[int] = None
    last_updated: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── MEV Bundle ─────────────────────────────────────────────────────

@dataclass
class MEVBundle:
    """Represents a bundle of transactions for MEV extraction."""
    bundle_id: str
    chain_id: int
    target_block: int
    transactions: List[Dict[str, Any]] = field(default_factory=list)
    relay: str = "flashbots"
    max_block_range: int = 1
    priority_fee_gwei: float = 0.0
    expected_profit_usd: Decimal = Decimal("0")
    submitted: bool = False
    included: bool = False
