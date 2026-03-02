#!/usr/bin/env python3
"""
STAGE 1B — DEX Arbitrage Scanner
==================================
Scans for triangular and cross-DEX price discrepancies that can be
captured atomically with flash loans. Zero capital required.

Profit vector: Flash-borrow token A, swap A→B on DEX1, swap B→A on DEX2.
If output > input + flash fee + gas → profit.

Focuses on high-liquidity pairs with well-known pool addresses:
  - Uniswap V3 ↔ SushiSwap
  - Uniswap V3 ↔ Curve
  - Triangular: WETH→USDC→WBTC→WETH
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
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
# Data models
# ────────────────────────────────────────────────────────────────────────

@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity."""
    chain_id: int
    path: List[str]           # token addresses in order
    dex_route: List[str]      # DEX names per hop
    input_amount: int         # in wei
    expected_output: int      # in wei
    profit_wei: int
    profit_usd: float
    gas_cost_usd: float
    net_profit_usd: float
    flash_loan_fee_usd: float
    timestamp: int
    block_number: int


# ────────────────────────────────────────────────────────────────────────
# Well-known token addresses per chain
# ────────────────────────────────────────────────────────────────────────

TOKENS = {
    1: {  # Ethereum
        "WETH":  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "USDC":  "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT":  "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI":   "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "WBTC":  "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    },
    42161: {  # Arbitrum
        "WETH":  "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "USDC":  "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT":  "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "WBTC":  "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
    },
    8453: {  # Base
        "WETH":  "0x4200000000000000000000000000000000000006",
        "USDC":  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    },
    10: {  # Optimism
        "WETH":  "0x4200000000000000000000000000000000000006",
        "USDC":  "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "USDT":  "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
    },
    137: {  # Polygon
        "WMATIC": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
        "WETH":   "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        "USDC":   "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    },
}

# Uniswap V3 Quoter address (same on most chains)
UNISWAP_V3_QUOTER = {
    1:     "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
    42161: "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
    10:    "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
    8453:  "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
    137:   "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
}

QUOTER_ABI = json.loads('''[
    {"inputs":[
        {"name":"tokenIn","type":"address"},
        {"name":"tokenOut","type":"address"},
        {"name":"fee","type":"uint24"},
        {"name":"amountIn","type":"uint256"},
        {"name":"sqrtPriceLimitX96","type":"uint160"}
    ],
    "name":"quoteExactInputSingle",
    "outputs":[{"name":"amountOut","type":"uint256"}],
    "stateMutability":"nonpayable","type":"function"}
]''')

# Fee tiers for Uniswap V3
FEE_TIERS = [500, 3000, 10000]  # 0.05%, 0.3%, 1%


# ────────────────────────────────────────────────────────────────────────
# DEX Arbitrage Scanner
# ────────────────────────────────────────────────────────────────────────

class DexArbScanner:
    """
    Scans for cross-DEX and triangular arbitrage using Uniswap V3 Quoter.

    Strategy:
    1. For each token pair, get the best quote on each fee tier
    2. Check if buying on one tier and selling on another yields profit
    3. Also check triangular paths: A→B→C→A

    Zero capital: uses flash loans to fund the arb atomically.
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self.w3_providers: Dict[int, Web3] = {}
        self.quoters: Dict[int, any] = {}  # chain_id → quoter contract
        self.stats = {
            "scans": 0,
            "opportunities_found": 0,
            "total_profit_usd": 0.0,
            "start_time": time.time(),
        }

        # Min profit after gas/fees to be worth executing
        self.min_profit_usd = float(
            __import__("os").getenv("ARB_MIN_PROFIT_USD", "15")
        )

    async def initialize(self):
        """Connect to chains and set up quoter contracts."""
        for chain_id, chain_cfg in self.config.get_all_chains().items():
            if not chain_cfg.rpc_url or chain_id not in TOKENS:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(
                    chain_cfg.rpc_url, request_kwargs={"timeout": 10}
                ))
                if poa_middleware:
                    w3.middleware_onion.inject(poa_middleware, layer=0)

                if chain_id in UNISWAP_V3_QUOTER:
                    quoter = w3.eth.contract(
                        address=Web3.to_checksum_address(UNISWAP_V3_QUOTER[chain_id]),
                        abi=QUOTER_ABI,
                    )
                    self.quoters[chain_id] = quoter

                self.w3_providers[chain_id] = w3
            except Exception as e:
                logger.debug(f"DexArb: chain {chain_id} init error: {e}")

        logger.info(
            f"DEX Arb Scanner ready -- {len(self.quoters)} chains with quoters, "
            f"min profit ${self.min_profit_usd}"
        )

    async def scan_once(self) -> List[ArbOpportunity]:
        """Run one scan cycle across all chains."""
        self.stats["scans"] += 1
        results = []

        for chain_id in self.quoters:
            try:
                opps = await self._scan_chain(chain_id)
                results.extend(opps)
            except Exception as e:
                logger.debug(f"DexArb scan error chain {chain_id}: {e}")

        return results

    async def _scan_chain(self, chain_id: int) -> List[ArbOpportunity]:
        """Check all configured arb paths on a single chain."""
        tokens = TOKENS.get(chain_id, {})
        quoter = self.quoters[chain_id]
        w3 = self.w3_providers[chain_id]
        opps = []

        # Get current block
        block = w3.eth.block_number

        # Strategy 1: Cross-fee-tier arb (same pair, different fees)
        # Buy WETH/USDC on 0.05% pool, sell on 0.3% pool (or vice versa)
        pairs = self._get_pairs(tokens)
        for token_a_name, token_b_name, token_a, token_b, amount_in, decimals_a in pairs:
            quotes = {}
            for fee in FEE_TIERS:
                try:
                    out = quoter.functions.quoteExactInputSingle(
                        Web3.to_checksum_address(token_a),
                        Web3.to_checksum_address(token_b),
                        fee,
                        amount_in,
                        0,
                    ).call()
                    quotes[fee] = out
                except Exception:
                    pass

            if len(quotes) < 2:
                continue

            # For each fee tier's output, check reverse on other tiers
            for buy_fee, mid_amount in quotes.items():
                for sell_fee in FEE_TIERS:
                    if sell_fee == buy_fee or sell_fee not in quotes:
                        continue
                    try:
                        reverse_out = quoter.functions.quoteExactInputSingle(
                            Web3.to_checksum_address(token_b),
                            Web3.to_checksum_address(token_a),
                            sell_fee,
                            mid_amount,
                            0,
                        ).call()
                    except Exception:
                        continue

                    if reverse_out > amount_in:
                        profit_wei = reverse_out - amount_in
                        # Convert to USD
                        if "USDC" in token_a_name or "USDT" in token_a_name or "DAI" in token_a_name:
                            profit_usd = profit_wei / (10 ** decimals_a)
                        else:
                            # Approximate ETH price
                            profit_usd = (profit_wei / 1e18) * 2500

                        # Flash loan fee (Aave V3: 0.05%)
                        flash_fee_usd = (amount_in / (10 ** decimals_a)) * 0.0005
                        if decimals_a == 18:
                            flash_fee_usd = (amount_in / 1e18) * 2500 * 0.0005

                        # Gas cost estimate
                        chain_cfg = self.config.get_chain(chain_id)
                        gas_usd = 5.0 if not chain_cfg or not chain_cfg.is_l2 else 0.10

                        net = profit_usd - flash_fee_usd - gas_usd

                        if net >= self.min_profit_usd:
                            opp = ArbOpportunity(
                                chain_id=chain_id,
                                path=[token_a, token_b, token_a],
                                dex_route=[f"UniV3-{buy_fee}", f"UniV3-{sell_fee}"],
                                input_amount=amount_in,
                                expected_output=reverse_out,
                                profit_wei=profit_wei,
                                profit_usd=profit_usd,
                                gas_cost_usd=gas_usd,
                                net_profit_usd=net,
                                flash_loan_fee_usd=flash_fee_usd,
                                timestamp=int(time.time()),
                                block_number=block,
                            )
                            opps.append(opp)
                            self.stats["opportunities_found"] += 1
                            self.stats["total_profit_usd"] += net
                            logger.warning(
                                f"💰 ARB: {token_a_name}→{token_b_name}→{token_a_name} "
                                f"buy@{buy_fee/10000:.2f}% sell@{sell_fee/10000:.2f}% "
                                f"net=${net:.2f} chain={chain_id}"
                            )

        # Strategy 2: Triangular arb (A→B→C→A)
        triangles = self._get_triangles(tokens)
        for tri in triangles:
            try:
                opp = await self._check_triangle(
                    chain_id, w3, quoter, tri, block
                )
                if opp:
                    opps.append(opp)
            except Exception:
                pass

        return opps

    async def _check_triangle(
        self, chain_id, w3, quoter, triangle, block
    ) -> Optional[ArbOpportunity]:
        """Check a triangular arb path: A→B→C→A."""
        (name_a, addr_a, dec_a), (name_b, addr_b, _), (name_c, addr_c, _) = triangle

        # Start with 1 ETH worth or 10000 USDC worth
        if dec_a == 18:
            amount_in = int(1 * 1e18)  # 1 WETH
        else:
            amount_in = int(10000 * (10 ** dec_a))  # 10000 stablecoins

        best_fee = 3000  # default 0.3%

        # Hop 1: A → B
        try:
            out_1 = quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(addr_a),
                Web3.to_checksum_address(addr_b),
                best_fee, amount_in, 0
            ).call()
        except Exception:
            return None

        # Hop 2: B → C
        try:
            out_2 = quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(addr_b),
                Web3.to_checksum_address(addr_c),
                best_fee, out_1, 0
            ).call()
        except Exception:
            return None

        # Hop 3: C → A
        try:
            out_3 = quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(addr_c),
                Web3.to_checksum_address(addr_a),
                best_fee, out_2, 0
            ).call()
        except Exception:
            return None

        if out_3 <= amount_in:
            return None

        profit_wei = out_3 - amount_in
        if dec_a == 18:
            profit_usd = (profit_wei / 1e18) * 2500
        else:
            profit_usd = profit_wei / (10 ** dec_a)

        flash_fee_usd = profit_usd * 0.0005  # tiny
        chain_cfg = self.config.get_chain(chain_id)
        gas_usd = 8.0 if not chain_cfg or not chain_cfg.is_l2 else 0.15
        net = profit_usd - flash_fee_usd - gas_usd

        if net < self.min_profit_usd:
            return None

        self.stats["opportunities_found"] += 1
        self.stats["total_profit_usd"] += net
        logger.warning(
            f"💰 TRIANGLE: {name_a}->{name_b}->{name_c}->{name_a} "
            f"net=${net:.2f} chain={chain_id}"
        )

        return ArbOpportunity(
            chain_id=chain_id,
            path=[addr_a, addr_b, addr_c, addr_a],
            dex_route=[f"UniV3-{best_fee}"] * 3,
            input_amount=amount_in,
            expected_output=out_3,
            profit_wei=profit_wei,
            profit_usd=profit_usd,
            gas_cost_usd=gas_usd,
            net_profit_usd=net,
            flash_loan_fee_usd=flash_fee_usd,
            timestamp=int(time.time()),
            block_number=block,
        )

    def _get_pairs(self, tokens: Dict) -> list:
        """Generate token pairs with multiple trade sizes to catch arbs at any scale."""
        pairs = []
        if "WETH" in tokens and "USDC" in tokens:
            # Multiple sizes: 0.5 ETH, 2 ETH, 10 ETH
            pairs.append(("WETH", "USDC", tokens["WETH"], tokens["USDC"], int(0.5 * 1e18), 18))
            pairs.append(("WETH", "USDC", tokens["WETH"], tokens["USDC"], int(2 * 1e18), 18))
            pairs.append(("WETH", "USDC", tokens["WETH"], tokens["USDC"], int(10 * 1e18), 18))
            pairs.append(("USDC", "WETH", tokens["USDC"], tokens["WETH"], int(1000 * 1e6), 6))
            pairs.append(("USDC", "WETH", tokens["USDC"], tokens["WETH"], int(5000 * 1e6), 6))
            pairs.append(("USDC", "WETH", tokens["USDC"], tokens["WETH"], int(25000 * 1e6), 6))
        if "WETH" in tokens and "USDT" in tokens:
            pairs.append(("WETH", "USDT", tokens["WETH"], tokens["USDT"], int(1 * 1e18), 18))
            pairs.append(("WETH", "USDT", tokens["WETH"], tokens["USDT"], int(10 * 1e18), 18))
        if "WETH" in tokens and "DAI" in tokens:
            pairs.append(("WETH", "DAI", tokens["WETH"], tokens["DAI"], int(1 * 1e18), 18))
            pairs.append(("WETH", "DAI", tokens["WETH"], tokens["DAI"], int(10 * 1e18), 18))
        if "WETH" in tokens and "WBTC" in tokens:
            pairs.append(("WETH", "WBTC", tokens["WETH"], tokens["WBTC"], int(1 * 1e18), 18))
            pairs.append(("WETH", "WBTC", tokens["WETH"], tokens["WBTC"], int(10 * 1e18), 18))
        if "USDC" in tokens and "USDT" in tokens:
            pairs.append(("USDC", "USDT", tokens["USDC"], tokens["USDT"], int(5000 * 1e6), 6))
            pairs.append(("USDC", "USDT", tokens["USDC"], tokens["USDT"], int(50000 * 1e6), 6))
        if "WMATIC" in tokens and "USDC" in tokens:
            pairs.append(("WMATIC", "USDC", tokens["WMATIC"], tokens["USDC"], int(1000 * 1e18), 18))
            pairs.append(("WMATIC", "USDC", tokens["WMATIC"], tokens["USDC"], int(10000 * 1e18), 18))
        return pairs

    def _get_triangles(self, tokens: Dict) -> list:
        """Generate triangular arb paths."""
        triangles = []
        if all(k in tokens for k in ("WETH", "USDC", "WBTC")):
            triangles.append((
                ("WETH", tokens["WETH"], 18),
                ("USDC", tokens["USDC"], 6),
                ("WBTC", tokens["WBTC"], 8),
            ))
        if all(k in tokens for k in ("WETH", "USDC", "DAI")):
            triangles.append((
                ("WETH", tokens["WETH"], 18),
                ("USDC", tokens["USDC"], 6),
                ("DAI", tokens["DAI"], 18),
            ))
        if all(k in tokens for k in ("WETH", "USDT", "USDC")):
            triangles.append((
                ("WETH", tokens["WETH"], 18),
                ("USDT", tokens["USDT"], 6),
                ("USDC", tokens["USDC"], 6),
            ))
        return triangles

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "uptime_seconds": time.time() - self.stats["start_time"],
            "chains_active": len(self.quoters),
        }
