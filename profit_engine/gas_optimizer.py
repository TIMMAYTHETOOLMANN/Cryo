#!/usr/bin/env python3
"""
GAS OPTIMIZER — Dynamic Gas Prediction & Margin Compression Prevention
========================================================================
Prevents gas spikes from destroying margins by:
  - Tracking real-time gas across all chains
  - Predicting next-block gas via EIP-1559 base-fee model
  - Computing maximum acceptable gas for a given profit target
  - Auto-skipping trades where gas compresses profit below threshold

Used by every executor before transaction submission.
"""

import asyncio
import time
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from collections import deque


@dataclass
class GasEstimate:
    """Gas estimate for a specific trade"""
    chain_id: int
    base_fee_gwei: float
    priority_fee_gwei: float
    total_fee_gwei: float
    estimated_gas_units: int
    gas_cost_eth: float
    gas_cost_usd: float
    max_acceptable_gas_gwei: float   # Above this, trade is unprofitable
    is_profitable_at_current: bool
    margin_remaining_usd: float      # Profit after gas
    margin_percent: float            # margin / gross_profit


@dataclass
class ChainGasState:
    """Real-time gas state for a chain"""
    chain_id: int
    current_base_fee: float       # gwei
    current_priority_fee: float   # gwei
    predicted_next_base: float    # gwei (EIP-1559 prediction)
    eth_price_usd: float
    last_updated: float
    base_fee_history: deque = field(default_factory=lambda: deque(maxlen=100))
    congestion_level: str = "normal"  # low, normal, high, extreme


class GasOptimizer:
    """
    Dynamic gas management across all supported chains.
    """

    # Competition-aware bidding thresholds (Enhancement 8)
    COMPETITION_LOW_THRESHOLD = 0.3      # Below this: low competition
    COMPETITION_HIGH_THRESHOLD = 0.7     # Above this: high competition
    BID_PCT_LOW_COMPETITION = 0.05       # Max 5% of profit as priority fee
    BID_PCT_MED_COMPETITION = 0.15       # Max 15%
    BID_PCT_HIGH_COMPETITION = 0.40      # Max 40% (must-win)
    CONGESTION_PREMIUM_HIGH = 1.2        # 20% premium during high congestion
    CONGESTION_PREMIUM_EXTREME = 1.5     # 50% premium during extreme congestion

    # Gas unit estimates per operation type
    GAS_UNITS = {
        'liquidation': 350_000,
        'flash_loan_liquidation': 500_000,
        'arbitrage_2hop': 300_000,
        'arbitrage_3hop': 450_000,
        'cross_chain_bridge': 200_000,
        'cross_chain_execute': 150_000,
        'backrun': 200_000,
        'reserve_protocol_arb': 600_000,
        'nft_liquidation': 400_000,
    }

    # Chain-specific gas cost multipliers (relative to Ethereum L1)
    CHAIN_MULTIPLIERS = {
        1: 1.0,         # Ethereum
        42161: 0.01,    # Arbitrum (~100x cheaper)
        10: 0.001,      # Optimism (~1000x cheaper)
        137: 0.005,     # Polygon (~200x cheaper)
        8453: 0.001,    # Base (~1000x cheaper)
        43114: 0.05,    # Avalanche (~20x cheaper)
        56: 0.02,       # BSC (~50x cheaper)
        324: 0.008,     # zkSync (~125x cheaper)
    }

    # Default ETH prices per chain (updated at runtime from oracles)
    DEFAULT_ETH_PRICES = {
        1: 2500.0,
        42161: 2500.0,
        10: 2500.0,
        137: 0.50,      # MATIC
        8453: 2500.0,
        43114: 35.0,    # AVAX
        56: 600.0,      # BNB
        324: 2500.0,
    }

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.chain_states: Dict[int, ChainGasState] = {}
        self.min_margin_percent = self.config.get('min_margin_percent', 0.01)  # 1% min margin (max aggression)
        self.min_margin_usd = self.config.get('min_margin_usd', 0.01)  # $0.01 min — fire on anything net-positive

        # Initialize states for all supported chains
        for chain_id in self.CHAIN_MULTIPLIERS:
            # Use realistic current-era gas: ~1 gwei on mainnet, proportionally lower on L2s
            initial_base = 1.0 * self.CHAIN_MULTIPLIERS[chain_id]
            initial_priority = 0.1 * self.CHAIN_MULTIPLIERS[chain_id]
            self.chain_states[chain_id] = ChainGasState(
                chain_id=chain_id,
                current_base_fee=initial_base,
                current_priority_fee=initial_priority,
                predicted_next_base=initial_base,
                eth_price_usd=self.DEFAULT_ETH_PRICES.get(chain_id, 2500.0),
                last_updated=time.time(),
            )

        print("⛽ Gas Optimizer initialized")
        print(f"   Chains: {len(self.chain_states)}")
        print(f"   Min margin: {self.min_margin_percent*100:.0f}% / ${self.min_margin_usd}")

    # ──────────────────────────────────────────────
    # GAS UPDATE (called by scanner per-block)
    # ──────────────────────────────────────────────

    async def update_gas(self, chain_id: int, w3) -> Optional[ChainGasState]:
        """Fetch latest gas data from chain and update state."""
        try:
            state = self.chain_states.get(chain_id)
            if not state:
                return None

            # Get latest block for base fee
            latest = w3.eth.get_block('latest')
            base_fee = latest.get('baseFeePerGas', 30 * 10**9) / 10**9  # gwei

            # Priority fee estimate
            try:
                priority = w3.eth.max_priority_fee / 10**9
            except Exception:
                priority = 2.0 * self.CHAIN_MULTIPLIERS.get(chain_id, 1.0)

            # Store history
            state.base_fee_history.append(base_fee)

            # EIP-1559 next-block prediction
            # If parent block was >50% full, base fee increases up to 12.5%
            gas_used_ratio = latest.get('gasUsedRatio', latest.get('gasUsed', 0) / max(latest.get('gasLimit', 1), 1))
            if gas_used_ratio > 0.5:
                predicted = base_fee * (1 + 0.125 * (gas_used_ratio - 0.5) / 0.5)
            else:
                predicted = base_fee * (1 - 0.125 * (0.5 - gas_used_ratio) / 0.5)
            predicted = max(predicted, 0.01)

            # Congestion classification
            if len(state.base_fee_history) >= 10:
                avg_fee = sum(state.base_fee_history) / len(state.base_fee_history)
                ratio = base_fee / max(avg_fee, 0.01)
                if ratio > 2.0:
                    congestion = "extreme"
                elif ratio > 1.5:
                    congestion = "high"
                elif ratio < 0.5:
                    congestion = "low"
                else:
                    congestion = "normal"
            else:
                congestion = "normal"

            state.current_base_fee = base_fee
            state.current_priority_fee = priority
            state.predicted_next_base = predicted
            state.congestion_level = congestion
            state.last_updated = time.time()

            return state

        except Exception as e:
            return self.chain_states.get(chain_id)

    # ──────────────────────────────────────────────
    # ESTIMATE
    # ──────────────────────────────────────────────

    def estimate(
        self,
        chain_id: int,
        operation_type: str,
        gross_profit_usd: float,
        gas_units_override: Optional[int] = None,
    ) -> GasEstimate:
        """
        Compute gas estimate for a proposed trade.
        Returns whether the trade remains profitable after gas costs.
        """
        state = self.chain_states.get(chain_id)
        if not state:
            # Unknown chain — conservative refusal
            return GasEstimate(
                chain_id=chain_id, base_fee_gwei=999, priority_fee_gwei=0,
                total_fee_gwei=999, estimated_gas_units=0, gas_cost_eth=0,
                gas_cost_usd=999999, max_acceptable_gas_gwei=0,
                is_profitable_at_current=False, margin_remaining_usd=0,
                margin_percent=0,
            )

        gas_units = gas_units_override or self.GAS_UNITS.get(operation_type, 350_000)
        total_fee_gwei = state.predicted_next_base + state.current_priority_fee
        gas_cost_eth = (gas_units * total_fee_gwei) / 1e9
        gas_cost_usd = gas_cost_eth * state.eth_price_usd

        margin_remaining = gross_profit_usd - gas_cost_usd
        margin_pct = margin_remaining / max(gross_profit_usd, 0.01)

        # Max acceptable gas: solve for gas_gwei where margin = min_margin_usd
        # gas_cost = (gas_units * gas_gwei) / 1e9 * eth_price
        # gross - gas_cost = min_margin_usd
        # gas_gwei = (gross - min_margin_usd) * 1e9 / (gas_units * eth_price)
        if gas_units > 0 and state.eth_price_usd > 0:
            max_gas_gwei = ((gross_profit_usd - self.min_margin_usd) * 1e9) / (
                gas_units * state.eth_price_usd
            )
        else:
            max_gas_gwei = 0

        is_profitable = (
            margin_remaining >= self.min_margin_usd
            and margin_pct >= self.min_margin_percent
        )

        return GasEstimate(
            chain_id=chain_id,
            base_fee_gwei=round(state.predicted_next_base, 4),
            priority_fee_gwei=round(state.current_priority_fee, 4),
            total_fee_gwei=round(total_fee_gwei, 4),
            estimated_gas_units=gas_units,
            gas_cost_eth=round(gas_cost_eth, 8),
            gas_cost_usd=round(gas_cost_usd, 2),
            max_acceptable_gas_gwei=round(max(max_gas_gwei, 0), 4),
            is_profitable_at_current=is_profitable,
            margin_remaining_usd=round(margin_remaining, 2),
            margin_percent=round(margin_pct, 4),
        )

    # ──────────────────────────────────────────────
    # PRIORITY FEE BIDDING (for multiplier phase)
    # ──────────────────────────────────────────────

    def compute_priority_bid(
        self,
        chain_id: int,
        expected_profit_usd: float,
        competition_level: float,   # 0-1
    ) -> float:
        """
        Enhancement 8: Compute optimal priority fee bid based on expected profit,
        competition level, and congestion.  Higher competition → bid more of the
        profit as priority fee to win the block inclusion race.

        Strategy:
          - Low competition (<0.3): use base priority fee — save margin
          - Medium competition (0.3-0.7): bid up to 15% of profit
          - High competition (>0.7): bid up to 40% of profit
          - Extreme congestion: add congestion premium
        Returns priority fee in gwei.
        """
        state = self.chain_states.get(chain_id)
        if not state:
            return 2.0

        # Base bid = current network priority
        base_bid = state.current_priority_fee

        # Tiered willingness to pay based on competition intensity
        if competition_level < self.COMPETITION_LOW_THRESHOLD:
            max_pct = self.BID_PCT_LOW_COMPETITION
        elif competition_level < self.COMPETITION_HIGH_THRESHOLD:
            max_pct = self.BID_PCT_MED_COMPETITION
        else:
            max_pct = self.BID_PCT_HIGH_COMPETITION

        # Congestion premium: boost bid during high/extreme congestion
        congestion_premium = 1.0
        if state.congestion_level == 'high':
            congestion_premium = self.CONGESTION_PREMIUM_HIGH
        elif state.congestion_level == 'extreme':
            congestion_premium = self.CONGESTION_PREMIUM_EXTREME

        max_bid_usd = expected_profit_usd * max_pct * competition_level * congestion_premium
        gas_units = 350_000  # Average
        if state.eth_price_usd > 0 and gas_units > 0:
            max_bid_gwei = (max_bid_usd * 1e9) / (gas_units * state.eth_price_usd)
        else:
            max_bid_gwei = base_bid

        return round(max(base_bid, min(max_bid_gwei, base_bid * 10)), 4)

    # ──────────────────────────────────────────────
    # STATUS
    # ──────────────────────────────────────────────

    def chain_status(self) -> Dict[int, Dict]:
        result = {}
        for cid, state in self.chain_states.items():
            result[cid] = {
                'base_fee_gwei': round(state.current_base_fee, 4),
                'priority_gwei': round(state.current_priority_fee, 4),
                'predicted_next': round(state.predicted_next_base, 4),
                'congestion': state.congestion_level,
                'eth_price': state.eth_price_usd,
            }
        return result
