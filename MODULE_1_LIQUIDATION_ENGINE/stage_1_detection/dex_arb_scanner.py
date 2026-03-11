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


@dataclass
class TransactionRecord:
    """A swap transaction observed on-chain or in the mempool."""
    tx_hash: str
    sender: str               # EOA that submitted the tx
    token_in: str             # input token address
    token_out: str            # output token address
    amount_in: int            # in wei
    amount_out: int           # in wei
    dex: str                  # DEX name
    chain_id: int
    block_number: int
    timestamp: int
    contract_address: str = ""  # interacted contract (for entity linking)


@dataclass
class ArbPairCandidate:
    """A candidate arbitrage pair detected by the heuristic engine."""
    tx_a: TransactionRecord
    tx_b: TransactionRecord
    heuristics_passed: List[str]   # which of H1-H4 matched
    marginal_difference_pct: float # intermediate amount difference %
    time_gap_seconds: int
    is_cyclic: bool
    is_entity_linked: bool
    estimated_profit_usd: float = 0.0


# ────────────────────────────────────────────────────────────────────────
# Arbitrage heuristic thresholds (configurable via env)
# ────────────────────────────────────────────────────────────────────────

# H2: Marginal difference threshold (0.5% per academic research)
MARGINAL_DIFF_THRESHOLD = float(
    __import__("os").getenv("ARB_MARGINAL_DIFF_PCT", "0.5")
) / 100.0

# H3: Temporal windows
TEMPORAL_WINDOW_STABLE_NATIVE_S = int(
    __import__("os").getenv("ARB_TEMPORAL_WINDOW_STABLE_S", "12")
)
TEMPORAL_WINDOW_DEFAULT_S = int(
    __import__("os").getenv("ARB_TEMPORAL_WINDOW_DEFAULT_S", "3600")
)

# Stablecoin symbols for H3 window selection
STABLECOIN_SYMBOLS = {"USDC", "USDT", "DAI", "FRAX", "LUSD", "GHO", "BUSD"}
NATIVE_SYMBOLS = {"WETH", "WMATIC", "WAVAX", "WBNB"}


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

# SushiSwap V2 / Uniswap V2 Router addresses (for cross-DEX arbs)
SUSHI_V2_ROUTERS: Dict[int, str] = {
    1:     "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",  # SushiSwap
    42161: "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",  # SushiSwap Arb
    137:   "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",  # SushiSwap Polygon
}

UNI_V2_ROUTERS: Dict[int, str] = {
    1:     "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # Uniswap V2
    8453:  "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",  # Aerodrome (Base)
}

V2_ROUTER_ABI = json.loads('''[
    {"inputs":[
        {"name":"amountIn","type":"uint256"},
        {"name":"path","type":"address[]"}
    ],
    "name":"getAmountsOut",
    "outputs":[{"name":"amounts","type":"uint256[]"}],
    "stateMutability":"view","type":"function"}
]''')


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
        self.v2_routers: Dict[int, List[tuple]] = {}  # chain_id → [(name, contract)]
        self.stats = {
            "scans": 0,
            "opportunities_found": 0,
            "total_profit_usd": 0.0,
            "cross_dex_checks": 0,
            "start_time": time.time(),
        }

        # Min profit after gas/fees to be worth executing
        self.min_profit_usd = float(
            __import__("os").getenv("ARB_MIN_PROFIT_USD", "0.01")
        )

        # L2 chains have much lower gas, so lower the threshold
        self._l2_chains = {42161, 10, 8453, 137}  # Arbitrum, Optimism, Base, Polygon
        self.min_profit_usd_l2 = float(
            __import__("os").getenv("ARB_MIN_PROFIT_USD_L2", "0.01")
        )

    def _estimate_gas_usd(self, chain_id: int, gas_units: int = 350_000) -> float:
        """Estimate gas cost in USD using live gas price, not hardcoded values."""
        w3 = self.w3_providers.get(chain_id)
        if not w3:
            return 0.50  # Conservative fallback
        try:
            gas_price_wei = w3.eth.gas_price
            gas_price_gwei = gas_price_wei / 1e9
            eth_price = 2000.0  # Will be updated from oracle
            gas_cost_eth = (gas_units * gas_price_wei) / 1e18
            return gas_cost_eth * eth_price
        except Exception:
            # Fallback: use realistic defaults (not $5-8!)
            if chain_id in self._l2_chains:
                return 0.02  # L2s are sub-cent
            return 0.10  # ETH mainnet at low gas

    def _flash_loan_fee(self, amount_usd: float) -> float:
        """Flash loan fee — Balancer charges 0%, so effectively free."""
        return 0.0  # Balancer (preferred provider) has 0% flash loan fee

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

        # Initialize V2 routers for cross-DEX arb detection
        for chain_id, w3 in self.w3_providers.items():
            routers_for_chain = []
            for name, router_map in [("SushiV2", SUSHI_V2_ROUTERS), ("UniV2", UNI_V2_ROUTERS)]:
                addr = router_map.get(chain_id)
                if addr:
                    try:
                        contract = w3.eth.contract(
                            address=Web3.to_checksum_address(addr),
                            abi=V2_ROUTER_ABI,
                        )
                        routers_for_chain.append((name, contract))
                    except Exception:
                        pass
            if routers_for_chain:
                self.v2_routers[chain_id] = routers_for_chain

        logger.info(
            f"DEX Arb Scanner ready -- {len(self.quoters)} V3 chains, "
            f"{len(self.v2_routers)} V2 chains, "
            f"min profit L1=${self.min_profit_usd} L2=${self.min_profit_usd_l2}"
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

                        # Flash loan fee (Balancer: 0%)
                        flash_fee_usd = self._flash_loan_fee(amount_in / (10 ** decimals_a) if decimals_a != 18 else (amount_in / 1e18) * 2500)

                        # Live gas cost estimate
                        gas_usd = self._estimate_gas_usd(chain_id, 350_000)

                        net = profit_usd - flash_fee_usd - gas_usd

                        # Use L2-specific threshold for cheaper chains
                        min_profit = self.min_profit_usd_l2 if chain_id in self._l2_chains else self.min_profit_usd

                        if net >= min_profit:
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

        # Strategy 3: Cross-DEX arb (UniV3 vs SushiV2/UniV2)
        v2_list = self.v2_routers.get(chain_id, [])
        if v2_list:
            for v2_name, v2_contract in v2_list:
                try:
                    cross_opps = await self._scan_cross_dex(
                        chain_id, w3, quoter, v2_name, v2_contract, tokens, block
                    )
                    opps.extend(cross_opps)
                except Exception as e:
                    logger.debug(f"Cross-DEX scan error {v2_name} chain {chain_id}: {e}")

        return opps

    async def _scan_cross_dex(
        self, chain_id: int, w3: Web3, v3_quoter, v2_name: str, v2_router,
        tokens: Dict, block: int,
    ) -> List[ArbOpportunity]:
        """
        Compare UniV3 quotes against V2 router quotes.
        Buy on the cheaper DEX, sell on the more expensive one.
        """
        opps = []
        pairs = self._get_pairs(tokens)
        min_profit = self.min_profit_usd_l2 if chain_id in self._l2_chains else self.min_profit_usd

        for token_a_name, token_b_name, token_a, token_b, amount_in, decimals_a in pairs:
            self.stats["cross_dex_checks"] += 1

            # Get V3 quote (best fee tier)
            best_v3_out = 0
            best_v3_fee = 3000
            for fee in FEE_TIERS:
                try:
                    out = v3_quoter.functions.quoteExactInputSingle(
                        Web3.to_checksum_address(token_a),
                        Web3.to_checksum_address(token_b),
                        fee, amount_in, 0
                    ).call()
                    if out > best_v3_out:
                        best_v3_out = out
                        best_v3_fee = fee
                except Exception:
                    pass

            if best_v3_out == 0:
                continue

            # Get V2 quote
            try:
                v2_amounts = v2_router.functions.getAmountsOut(
                    amount_in,
                    [Web3.to_checksum_address(token_a), Web3.to_checksum_address(token_b)]
                ).call()
                v2_out = v2_amounts[-1]
            except Exception:
                continue

            # Check both directions: V3→V2 and V2→V3
            for buy_dex, buy_out, sell_dex, sell_router, sell_amount in [
                (f"UniV3-{best_v3_fee}", best_v3_out, v2_name, v2_router, best_v3_out),
                (v2_name, v2_out, f"UniV3-{best_v3_fee}", v3_quoter, v2_out),
            ]:
                # Get reverse quote from sell DEX
                reverse_out = 0
                if "UniV3" in sell_dex:
                    for fee in FEE_TIERS:
                        try:
                            out = sell_router.functions.quoteExactInputSingle(
                                Web3.to_checksum_address(token_b),
                                Web3.to_checksum_address(token_a),
                                fee, sell_amount, 0
                            ).call()
                            if out > reverse_out:
                                reverse_out = out
                        except Exception:
                            pass
                else:
                    try:
                        amounts = sell_router.functions.getAmountsOut(
                            sell_amount,
                            [Web3.to_checksum_address(token_b), Web3.to_checksum_address(token_a)]
                        ).call()
                        reverse_out = amounts[-1]
                    except Exception:
                        pass

                if reverse_out <= amount_in:
                    continue

                profit_wei = reverse_out - amount_in
                if "USDC" in token_a_name or "USDT" in token_a_name or "DAI" in token_a_name:
                    profit_usd = profit_wei / (10 ** decimals_a)
                else:
                    profit_usd = (profit_wei / 1e18) * 2500

                flash_fee_usd = self._flash_loan_fee(amount_in / (10 ** decimals_a) if decimals_a != 18 else (amount_in / 1e18) * 2500)
                gas_usd = self._estimate_gas_usd(chain_id, 450_000)  # Cross-DEX uses more gas
                net = profit_usd - flash_fee_usd - gas_usd

                if net >= min_profit:
                    opp = ArbOpportunity(
                        chain_id=chain_id,
                        path=[token_a, token_b, token_a],
                        dex_route=[buy_dex, sell_dex],
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
                        f"💰 CROSS-DEX: {token_a_name}→{token_b_name}→{token_a_name} "
                        f"buy@{buy_dex} sell@{sell_dex} "
                        f"net=${net:.2f} chain={chain_id}"
                    )

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

        flash_fee_usd = self._flash_loan_fee(profit_usd)
        gas_usd = self._estimate_gas_usd(chain_id, 500_000)  # Triangular uses 3 hops
        net = profit_usd - flash_fee_usd - gas_usd

        # Use L2-specific threshold
        min_profit = self.min_profit_usd_l2 if chain_id in self._l2_chains else self.min_profit_usd

        if net < min_profit:
            if net > 0:
                logger.debug(
                    f"Near-miss triangle: {name_a}->{name_b}->{name_c} "
                    f"net=${net:.2f} < min ${min_profit:.2f} chain={chain_id}"
                )
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
            "chains_v3": len(self.quoters),
            "chains_v2": len(self.v2_routers),
        }

    # ────────────────────────────────────────────────────────────────
    # Universal Arbitrage Detection — Four-Heuristic Pair Matching
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_token_symbol(address: str, chain_id: int) -> str:
        """Resolve a token address to its symbol for heuristic checks."""
        chain_tokens = TOKENS.get(chain_id, {})
        addr_lower = address.lower()
        for symbol, addr in chain_tokens.items():
            if addr.lower() == addr_lower:
                return symbol
        return ""

    @staticmethod
    def _is_stablecoin_native_pair(token_a: str, token_b: str,
                                    chain_id: int) -> bool:
        """Check if both tokens are from the stablecoin-native set."""
        sym_a = DexArbScanner._resolve_token_symbol(token_a, chain_id)
        sym_b = DexArbScanner._resolve_token_symbol(token_b, chain_id)
        combined = {sym_a, sym_b}
        has_stable = bool(combined & STABLECOIN_SYMBOLS)
        has_native = bool(combined & NATIVE_SYMBOLS)
        return has_stable and has_native

    @staticmethod
    def _check_cyclic(tx_a: TransactionRecord,
                      tx_b: TransactionRecord) -> bool:
        """H1 — Cyclic: input/output assets form a closed loop."""
        return (
            tx_a.token_in.lower() == tx_b.token_out.lower()
            and tx_a.token_out.lower() == tx_b.token_in.lower()
        )

    @staticmethod
    def _check_marginal_difference(tx_a: TransactionRecord,
                                   tx_b: TransactionRecord) -> Tuple[bool, float]:
        """
        H2 — Marginal Difference: the intermediate amounts differ
        by at most ``MARGINAL_DIFF_THRESHOLD`` (default 0.5%).
        We compare tx_a.amount_out with tx_b.amount_in (the
        intermediate leg that should be nearly equal).
        """
        if tx_a.amount_out == 0 or tx_b.amount_in == 0:
            return False, float("inf")
        reference = max(tx_a.amount_out, tx_b.amount_in)
        diff = abs(tx_a.amount_out - tx_b.amount_in) / reference
        return diff <= MARGINAL_DIFF_THRESHOLD, diff

    @staticmethod
    def _check_temporal_window(tx_a: TransactionRecord,
                               tx_b: TransactionRecord) -> Tuple[bool, int]:
        """
        H3 — Temporal Window: the time gap must be ≤12 s for
        stablecoin-native pairs and ≤1 h otherwise.
        """
        gap = abs(tx_a.timestamp - tx_b.timestamp)
        is_sn_pair = DexArbScanner._is_stablecoin_native_pair(
            tx_a.token_in, tx_a.token_out, tx_a.chain_id
        )
        limit = (TEMPORAL_WINDOW_STABLE_NATIVE_S
                 if is_sn_pair
                 else TEMPORAL_WINDOW_DEFAULT_S)
        return gap <= limit, gap

    @staticmethod
    def _check_entity_link(tx_a: TransactionRecord,
                           tx_b: TransactionRecord) -> bool:
        """
        H4 — Entity Link: same EOA submitted both transactions,
        or both interact with the same MEV contract.
        """
        if tx_a.sender.lower() == tx_b.sender.lower():
            return True
        if (tx_a.contract_address
                and tx_a.contract_address.lower() == tx_b.contract_address.lower()):
            return True
        return False

    def find_arbitrage_pairs(
        self,
        transactions: List[TransactionRecord],
        *,
        min_heuristics: int = 2,
    ) -> List[ArbPairCandidate]:
        """
        Apply the four universal arbitrage heuristics to every
        transaction pair and return candidates that pass at least
        ``min_heuristics`` checks.

        Heuristics (per academic research):
          H1 — Cyclic: input/output assets form a loop
          H2 — Marginal Difference: intermediate amounts differ ≤0.5%
          H3 — Temporal Window: ≤12 s (stablecoin-native) or ≤1 h
          H4 — Entity Link: same sender or same MEV contract

        Parameters
        ----------
        transactions : list[TransactionRecord]
            Recent/pending swap transactions to analyse.
        min_heuristics : int
            Minimum number of heuristics that must pass (default 2).

        Returns
        -------
        list[ArbPairCandidate]
            Ranked list of candidate pairs, most heuristics first.
        """
        candidates: List[ArbPairCandidate] = []

        for i, tx_a in enumerate(transactions):
            for tx_b in transactions[i + 1:]:
                # Only consider same-chain pairs
                if tx_a.chain_id != tx_b.chain_id:
                    continue

                passed: List[str] = []

                # H1 — Cyclic
                is_cyclic = self._check_cyclic(tx_a, tx_b)
                if is_cyclic:
                    passed.append("H1_CYCLIC")

                # H2 — Marginal Difference
                marginal_ok, marginal_pct = self._check_marginal_difference(
                    tx_a, tx_b
                )
                if marginal_ok:
                    passed.append("H2_MARGINAL")

                # H3 — Temporal Window
                temporal_ok, time_gap = self._check_temporal_window(tx_a, tx_b)
                if temporal_ok:
                    passed.append("H3_TEMPORAL")

                # H4 — Entity Link
                is_entity = self._check_entity_link(tx_a, tx_b)
                if is_entity:
                    passed.append("H4_ENTITY")

                if len(passed) >= min_heuristics:
                    candidates.append(ArbPairCandidate(
                        tx_a=tx_a,
                        tx_b=tx_b,
                        heuristics_passed=passed,
                        marginal_difference_pct=marginal_pct * 100,
                        time_gap_seconds=time_gap,
                        is_cyclic=is_cyclic,
                        is_entity_linked=is_entity,
                    ))

        # Sort by number of heuristics passed (descending)
        candidates.sort(key=lambda c: len(c.heuristics_passed), reverse=True)
        return candidates
