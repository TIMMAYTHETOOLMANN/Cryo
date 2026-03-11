#!/usr/bin/env python3
"""
STAGE 1C — RToken Arbitrage Detector
======================================
Continuously monitors Reserve Protocol RTokens for price discrepancies
between their market price and basket (collateral) value.

Detection rule:
  Opportunity exists when:
    RToken Market Price < Value of 1 Basket of Collateral Tokens
        → Mint arbitrage (buy cheap RToken, redeem for collateral)
    RToken Market Price > Value of 1 Basket of Collateral Tokens
        → Mint-and-sell arbitrage (mint RToken from collateral, sell at premium)

Supports multi-chain RToken deployments (Ethereum, Arbitrum, Base).
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware as poa_middleware
except ImportError:
    try:
        from web3.middleware import geth_poa_middleware as poa_middleware
    except ImportError:
        poa_middleware = None

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# ABI fragments for Reserve Protocol RToken interactions
# ────────────────────────────────────────────────────────────────────────

RTOKEN_ABI = json.loads('''[
    {"inputs":[],"name":"basketsNeeded","outputs":[
        {"name":"","type":"uint192"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalSupply","outputs":[
        {"name":"","type":"uint256"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"basketHandler","outputs":[
        {"name":"","type":"address"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"main","outputs":[
        {"name":"","type":"address"}
    ],"stateMutability":"view","type":"function"}
]''')

BASKET_HANDLER_ABI = json.loads('''[
    {"inputs":[],"name":"quote","outputs":[
        {"name":"erc20s","type":"address[]"},
        {"name":"quantities","type":"uint256[]"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"status","outputs":[
        {"name":"","type":"uint8"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"nonce","outputs":[
        {"name":"","type":"uint48"}
    ],"stateMutability":"view","type":"function"}
]''')

ERC20_DECIMALS_ABI = json.loads('''[
    {"inputs":[],"name":"decimals","outputs":[
        {"name":"","type":"uint8"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"symbol","outputs":[
        {"name":"","type":"string"}
    ],"stateMutability":"view","type":"function"}
]''')


# ────────────────────────────────────────────────────────────────────────
# Known RToken deployments (auto-discovered in production)
# ────────────────────────────────────────────────────────────────────────

KNOWN_RTOKENS: Dict[int, Dict[str, str]] = {
    1: {  # Ethereum
        "eUSD":     "0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F",
        "ETH+":     "0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8",
        "hyUSD":    "0xaCdf0DBA4B9839b96221a8487e9ca660a48212be",
        "USDC+":    "0xFc0B1EEf20e4c68B3DCF36c4f4DD0e02Ee2dCE69",
        "dgnETH":   "0x005F893EcD7bE22C11a4109a0EF45C0eC0cd44Ff",
    },
    42161: {  # Arbitrum
        "eUSD":     "0x12275DCB9048680c4Be40942eA4D92c74C63b844",
        "ETH+":     "0x18c14c2D707b2212e17d1579789Fc06010cfca23",
    },
    8453: {  # Base
        "hyUSD":    "0xCc7FF230365bD730eE4B352cC2492CEdAC49383e",
        "bsdETH":   "0xCb327b99fF831bF8223cCEd12B1338FF3aA322Ff",
    },
}


# ────────────────────────────────────────────────────────────────────────
# Data models
# ────────────────────────────────────────────────────────────────────────

@dataclass
class RTokenState:
    """Snapshot of an RToken's on-chain & market state."""
    name: str
    address: str
    chain_id: int
    total_supply: int = 0
    baskets_needed: int = 0
    basket_tokens: List[str] = field(default_factory=list)
    basket_quantities: List[int] = field(default_factory=list)
    basket_value_usd: float = 0.0
    market_price_usd: float = 0.0
    collateral_ratio: float = 0.0   # basket_value / market_price
    spread_pct: float = 0.0         # (basket_value - market_price) / market_price * 100
    last_updated: float = 0.0


@dataclass
class RTokenArbOpportunity:
    """A detected RToken arbitrage opportunity."""
    rtoken_name: str
    rtoken_address: str
    chain_id: int
    direction: str               # "redeem" or "mint_and_sell"
    basket_value_usd: float
    market_price_usd: float
    spread_pct: float            # absolute spread percentage
    estimated_profit_usd: float  # per-unit profit * trade size
    gas_cost_usd: float
    net_profit_usd: float
    timestamp: int
    collateral_tokens: List[str]


# ────────────────────────────────────────────────────────────────────────
# RToken Arbitrage Detector
# ────────────────────────────────────────────────────────────────────────

class RTokenArbitrageDetector:
    """
    Continuously monitors Reserve Protocol RTokens on every supported
    chain for minting/redemption arbitrage opportunities.

    Core detection:
        spread = basketValue - marketPrice
        If spread > threshold → opportunity exists

    Supports dynamic RToken discovery and multi-chain deployment.
    """

    def __init__(
        self,
        config: Optional[ConfigManager] = None,
        *,
        min_spread_pct: float = 0.5,
        trade_size_usd: float = 10_000.0,
    ):
        self.config = config or get_config()
        self.min_spread_pct = min_spread_pct
        self.trade_size_usd = trade_size_usd
        self.w3_providers: Dict[int, Web3] = {}
        self._rtoken_states: Dict[str, RTokenState] = {}  # "chain:addr" → state
        self._known_rtokens: Dict[int, Dict[str, str]] = {
            k: dict(v) for k, v in KNOWN_RTOKENS.items()
        }

        self.stats = {
            "scans": 0,
            "rtokens_tracked": 0,
            "opportunities_found": 0,
            "total_spread_captured_usd": 0.0,
            "start_time": time.time(),
        }

    # ── Lifecycle ────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Connect to chains and prepare RToken contracts."""
        for chain_id, chain_cfg in self.config.get_all_chains().items():
            if not chain_cfg.rpc_url or chain_id not in self._known_rtokens:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(
                    chain_cfg.rpc_url, request_kwargs={"timeout": 10}
                ))
                if poa_middleware:
                    w3.middleware_onion.inject(poa_middleware, layer=0)
                self.w3_providers[chain_id] = w3
            except Exception as e:
                logger.debug("RTokenDetector: chain %d init error: %s", chain_id, e)

        # Build initial state map
        for chain_id, rtokens in self._known_rtokens.items():
            for name, addr in rtokens.items():
                key = f"{chain_id}:{addr.lower()}"
                self._rtoken_states[key] = RTokenState(
                    name=name, address=addr, chain_id=chain_id,
                )
        self.stats["rtokens_tracked"] = len(self._rtoken_states)
        logger.info(
            "RToken Arbitrage Detector ready — %d tokens across %d chains",
            len(self._rtoken_states), len(self.w3_providers),
        )

    def register_rtoken(self, chain_id: int, name: str, address: str) -> None:
        """Dynamically register a new RToken for monitoring."""
        self._known_rtokens.setdefault(chain_id, {})[name] = address
        key = f"{chain_id}:{address.lower()}"
        if key not in self._rtoken_states:
            self._rtoken_states[key] = RTokenState(
                name=name, address=address, chain_id=chain_id,
            )
            self.stats["rtokens_tracked"] = len(self._rtoken_states)
            logger.info("Registered new RToken: %s (%s) on chain %d", name, address, chain_id)

    # ── Core Detection ───────────────────────────────────────────────

    async def scan_once(self) -> List[RTokenArbOpportunity]:
        """Run a single scan across all tracked RTokens."""
        self.stats["scans"] += 1
        opportunities: List[RTokenArbOpportunity] = []

        for key, state in self._rtoken_states.items():
            w3 = self.w3_providers.get(state.chain_id)
            if not w3:
                continue

            try:
                updated = await self._refresh_rtoken_state(w3, state)
                if not updated:
                    continue

                opp = self._evaluate_spread(state)
                if opp:
                    opportunities.append(opp)
                    self.stats["opportunities_found"] += 1
                    self.stats["total_spread_captured_usd"] += opp.net_profit_usd
            except Exception as e:
                logger.debug(
                    "RToken scan error %s chain %d: %s",
                    state.name, state.chain_id, e,
                )

        return opportunities

    async def _refresh_rtoken_state(
        self, w3: Web3, state: RTokenState
    ) -> bool:
        """Fetch latest on-chain data for an RToken."""
        loop = asyncio.get_event_loop()
        try:
            rtoken = w3.eth.contract(
                address=Web3.to_checksum_address(state.address),
                abi=RTOKEN_ABI,
            )

            total_supply = await loop.run_in_executor(
                None, rtoken.functions.totalSupply().call
            )
            baskets_needed = await loop.run_in_executor(
                None, rtoken.functions.basketsNeeded().call
            )

            state.total_supply = total_supply
            state.baskets_needed = baskets_needed
            state.last_updated = time.time()

            # Calculate collateral ratio
            if total_supply > 0:
                state.collateral_ratio = baskets_needed / total_supply
            else:
                state.collateral_ratio = 0.0

            return True
        except Exception as e:
            logger.debug("Failed to refresh %s: %s", state.name, e)
            return False

    def _evaluate_spread(self, state: RTokenState) -> Optional[RTokenArbOpportunity]:
        """
        Evaluate the spread between basket value and market price.
        Returns an opportunity if spread exceeds threshold.
        """
        if state.basket_value_usd <= 0 or state.market_price_usd <= 0:
            return None

        spread_pct = (
            (state.basket_value_usd - state.market_price_usd)
            / state.market_price_usd
            * 100
        )
        state.spread_pct = spread_pct

        abs_spread = abs(spread_pct)
        if abs_spread < self.min_spread_pct:
            return None

        # Direction: positive spread = basket > market → redeem arb
        if spread_pct > 0:
            direction = "redeem"
        else:
            direction = "mint_and_sell"

        gross_profit = self.trade_size_usd * (abs_spread / 100.0)
        gas_cost = 0.50 if state.chain_id in {42161, 10, 8453, 137} else 5.0
        net_profit = gross_profit - gas_cost

        if net_profit <= 0:
            return None

        logger.warning(
            "💎 RTOKEN ARB: %s on chain %d — %s spread=%.2f%% net=$%.2f",
            state.name, state.chain_id, direction, abs_spread, net_profit,
        )

        return RTokenArbOpportunity(
            rtoken_name=state.name,
            rtoken_address=state.address,
            chain_id=state.chain_id,
            direction=direction,
            basket_value_usd=state.basket_value_usd,
            market_price_usd=state.market_price_usd,
            spread_pct=abs_spread,
            estimated_profit_usd=gross_profit,
            gas_cost_usd=gas_cost,
            net_profit_usd=net_profit,
            timestamp=int(time.time()),
            collateral_tokens=list(state.basket_tokens),
        )

    def update_market_price(self, chain_id: int, address: str,
                            price_usd: float) -> None:
        """Update an RToken's market price from DEX observation."""
        key = f"{chain_id}:{address.lower()}"
        state = self._rtoken_states.get(key)
        if state:
            state.market_price_usd = price_usd

    def update_basket_value(self, chain_id: int, address: str,
                            value_usd: float) -> None:
        """Update an RToken's basket value from oracle/on-chain data."""
        key = f"{chain_id}:{address.lower()}"
        state = self._rtoken_states.get(key)
        if state:
            state.basket_value_usd = value_usd

    def get_all_states(self) -> List[RTokenState]:
        """Return current state of all monitored RTokens."""
        return list(self._rtoken_states.values())

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "uptime_seconds": time.time() - self.stats["start_time"],
            "chains_connected": len(self.w3_providers),
        }
