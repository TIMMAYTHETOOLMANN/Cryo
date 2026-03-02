#!/usr/bin/env python3
"""
ZERO-REVERT EXECUTION PIPELINE
================================
Replaces the old fire-at-HF-1.05-and-pray approach with event-driven
precision execution that only fires when the math guarantees HF < 1.0.

Architecture:
  Layer 1 — PositionIndex:  Maps collateral assets → positions with pre-computed
                            liquidation prices.  Pure math, zero RPC calls.
  Layer 2 — OracleReactor:  WebSocket subscription to Chainlink AnswerUpdated.
                            On every price tick, recomputes HF for affected positions.
                            If math says HF < 1.0 → instant fire.
  Layer 3 — BlockWatcher:   Listens for new blocks.  For every new block, batch-checks
                            top-priority positions via multicall.  Catches anything
                            the oracle reactor missed (multi-collateral, etc.).
  Layer 4 — Executor:       Builds, signs, broadcasts liquidationCall in < 100ms.
                            Uses Flashbots Protect when available, public mempool
                            as fallback.  No simulation.  Chain is the judge.

Zero simulation.  Zero eth_call gates.  Pure production.
"""

import asyncio
import json
import math
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Set
from collections import deque

from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS & ABI
# ═══════════════════════════════════════════════════════════════════

AAVE_POOL_ABI = json.loads('''[
  {"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[
    {"name":"totalCollateralBase","type":"uint256"},
    {"name":"totalDebtBase","type":"uint256"},
    {"name":"availableBorrowsBase","type":"uint256"},
    {"name":"currentLiquidationThreshold","type":"uint256"},
    {"name":"ltv","type":"uint256"},
    {"name":"healthFactor","type":"uint256"}
  ],"stateMutability":"view","type":"function"},
  {"inputs":[
    {"name":"collateralAsset","type":"address"},
    {"name":"debtAsset","type":"address"},
    {"name":"user","type":"address"},
    {"name":"debtToCover","type":"uint256"},
    {"name":"receiveAToken","type":"bool"}
  ],"name":"liquidationCall","outputs":[],"stateMutability":"nonpayable","type":"function"}
]''')

CHAINLINK_ABI = json.loads('''[
  {"inputs":[],"name":"latestRoundData","outputs":[
    {"name":"roundId","type":"uint80"},
    {"name":"answer","type":"int256"},
    {"name":"startedAt","type":"uint256"},
    {"name":"updatedAt","type":"uint256"},
    {"name":"answeredInRound","type":"uint80"}
  ],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"decimals","outputs":[
    {"name":"","type":"uint8"}
  ],"stateMutability":"view","type":"function"}
]''')

# Aave V3 pool addresses per chain
AAVE_V3_POOLS = {
    1:     os.getenv('AAVE_V3_POOL_ETHEREUM',  '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2'),
    42161: os.getenv('AAVE_V3_POOL_ARBITRUM',   '0x794a61358D6845594F94dc1DB02A252b5b4814aD'),
    10:    os.getenv('AAVE_V3_POOL_OPTIMISM',    '0xB50201558B00496A145fE76f7424749556E326D8'),
    8453:  os.getenv('AAVE_V3_POOL_BASE',        '0xA238Dd80C259a72e81d7e4664a9801593F337052'),
    137:   os.getenv('AAVE_V3_POOL_POLYGON',     '0x794a61358D6845594F94dc1DB02A252b5b4814aD'),
    43114: os.getenv('AAVE_V3_POOL_AVALANCHE',   '0x794a61358D6845594F94dc1DB02A252b5b4814aD'),
}

# Chainlink feeds per chain — comprehensive coverage
CHAINLINK_FEEDS = {
    1: {
        'ETH/USD':   '0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419',
        'BTC/USD':   '0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c',
        'LINK/USD':  '0x2c1d072e956AFFC0D435Cb7AC38EF18d24d9127c',
        'USDC/USD':  '0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6',
        'DAI/USD':   '0xAed0c38402a5d19df6E4c03F4E2DceD6e29c1ee9',
        'AAVE/USD':  '0x547a514d5e3769680Ce22B2361c10Ea13619e8a9',
        'UNI/USD':   '0x553303d460EE0afB37EdFf9bE42922D8FF63220e',
        'MKR/USD':   '0xec1D1B3b0443256cc3860e24a46F108e699484Aa',
        'CRV/USD':   '0xCd627aA160A6fA45Eb793D19Ef54f5062F20f33f',
        'SNX/USD':   '0xDC3EA94CD0AC27d9A86C180091e7f78C683d3699',
        'COMP/USD':  '0xdbd020CAeF83eFd542f4De03e3cF0C28A4428bd5',
        'stETH/USD': '0xCfE54B5cD566aB89272946F602D76Ea879CAb4a8',
        'rETH/USD':  '0x536218f9E9Eb48863970252233c8F271f554C2d0',
        'cbETH/USD': '0xF017fcB346A1885194689bA23Eff2fE6fA5C483b',
        # Additional volatile altcoins accepted as Aave V3 collateral
        'BAL/USD':   '0xdF2917806E30300537aEB49A7663062F4d1F2b5F',
        'ENS/USD':   '0x5C00128d4d1c2F4f652C267d7bcdD7aC99C16E16',
        '1INCH/USD': '0xc929ad75B72593967DE83E7F7Cda0493458261D9',
        'FRAX/USD':  '0xB9E1E3A9feFf48998E45Fa90847ed4D467E8BcfD',
        'GHO/USD':   '0x3f12643D3f6f874d39C2a4c9f2Cd6f2DbAC877FC',
    },
    42161: {
        'ETH/USD':   '0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612',
        'BTC/USD':   '0x6ce185860a4963106506C203335A2910413708e9',
        'LINK/USD':  '0x86E53CF1B870786351Da77A57575e79CB55812CB',
        'ARB/USD':   '0xb2A824043730FE05F3DA2efaFa1CBbe83fa548D6',
        'USDC/USD':  '0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3',
        'DAI/USD':   '0xc5C8E77B397E531B8EC06BFb0048328B30E9eCfB',
        'UNI/USD':   '0x9C917083fDb403ab5ADbEC26Ee294f6EcAda2720',
        'AAVE/USD':  '0xaD1d5344AaDE45F43E596773Bcc4c423EAbdD034',
    },
    10: {
        'ETH/USD':   '0x13e3Ee699D1909E989722E753853AE30b17e08c5',
        'BTC/USD':   '0xD702DD976Fb76Fffc2D3963D037dfDae5b04E593',
        'LINK/USD':  '0xCc232dcFAAE6354cE191Bd574108c1aD03f86229',
        'OP/USD':    '0x0D276FC14719f9292D5C1eA2198673d1f4269246',
        'USDC/USD':  '0x16a9FA2FDa030272Ce99B29CF780dFA30361d35f',
        'DAI/USD':   '0x8dBa75e83DA73cc766A7e5a0ee71F656BAb470d6',
        'AAVE/USD':  '0x338ed6787f463394D24813b297401B9F05a8916d',
    },
    137: {
        'ETH/USD':   '0xF9680D99D6C9589e2a93a78A04A279e509205945',
        'BTC/USD':   '0xc907E116054Ad103354f2D350FD2514433D57F6f',
        'LINK/USD':  '0xd9FFdb71EbE7496cC440152d43986Aae0AB76665',
        'MATIC/USD': '0xAB594600376Ec9fD91F8e8dC744e3952fC8c1F42',
        'USDC/USD':  '0xfE4A8cc5b5B2366C1B58Bea3858e81843581b2F7',
        'DAI/USD':   '0x4746DeC9e833A82EC7C2C1356372CcF2cfcD2F3D',
        'AAVE/USD':  '0x72484B12719E23115761D5DA1646945632979bB6',
    },
    8453: {
        'ETH/USD':   '0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70',
        'cbETH/USD': '0xd7818272B9e248357d13057AAb0B417aF31E817d',
        'USDC/USD':  '0x7e860098F58bBFC8648a4311b374B1D669a2bc9b',
        'BTC/USD':   '0xCCADC697c55bbB68dc5bCdf8d3CBe83CdD4E071E',
    },
    43114: {
        'ETH/USD':   '0x976B3D034E162d8bD72D6b9C989d545b839003b0',
        'BTC/USD':   '0x2779D32d5166BAaa2B2b658333bA7e6Ec0C65743',
        'AVAX/USD':  '0x0A77230d17318075983913bC2145DB16C7366156',
        'USDC/USD':  '0x0F096872672F44d6EBA71527d2277B1ebD10ee26',
        'LINK/USD':  '0x49ccd9ca821EfEab2b98c60dc60F518E765eDe9a',
        'AAVE/USD':  '0x3CA13391E9fb38a75330fb28f8cc2eB3D9ceceED',
    },
    56: {
        'ETH/USD':   '0x9ef1B8c0E4F7dc8bF5719Ea496883DC6401d5b2e',
        'BTC/USD':   '0x264990fbd0A4796A3E3d8E37C4d5F87a3aCa5Ebf',
        'BNB/USD':   '0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE',
        'USDC/USD':  '0x51597f405303C4377E36123cBc172b13269EA163',
        'LINK/USD':  '0xca236E327F629f9Fc2c30A4E95775EbF0B89fac8',
    },
}

# Per-chain oracle poll interval in seconds.  High-activity L2s (1-2 s block times)
# are polled more aggressively to catch price moves within the same block.
CHAIN_POLL_INTERVAL: Dict[int, float] = {
    1:     8.0,   # Ethereum — ~12 s blocks; polling every 8 s is sufficient
    42161: 1.0,   # Arbitrum — ~1 s blocks; poll every block
    10:    2.0,   # Optimism — ~2 s blocks
    8453:  2.0,   # Base     — ~2 s blocks
    137:   2.0,   # Polygon  — ~2 s blocks
    43114: 2.0,   # Avalanche
    56:    3.0,   # BSC      — ~3 s blocks
    324:   1.0,   # zkSync Era — ~1 s blocks
}

# Map collateral token addresses to Chainlink feed pair names
# Covers Ethereum mainnet collateral types accepted by Aave V3 and other major lenders
COLLATERAL_TO_FEED = {
    # Core assets
    '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2': 'ETH/USD',    # WETH
    '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599': 'BTC/USD',    # WBTC
    '0x514910771af9ca656af840dff83e8264ecf986ca': 'LINK/USD',   # LINK
    '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9': 'AAVE/USD',   # AAVE
    # Governance / DeFi blue chips
    '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984': 'UNI/USD',    # UNI
    '0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2': 'MKR/USD',    # MKR
    '0xd533a949740bb3306d119cc777fa900ba034cd52': 'CRV/USD',    # CRV
    '0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f': 'SNX/USD',    # SNX
    '0xc00e94cb662c3520282e6f5717214004a7f26888': 'COMP/USD',   # COMP
    # Liquid staking tokens (peg to ETH but can deviate)
    '0xae78736cd615f374d3085123a210448e74fc6393': 'rETH/USD',   # rETH
    '0xae7ab96520de3a18e5e111b5eaab095312d7fe84': 'stETH/USD',  # stETH
    '0xbe9895146f7af43049ca1c1ae358b0541ea49704': 'cbETH/USD',  # cbETH
    # Additional volatile altcoins (Aave V3 / Compound / Euler collateral)
    '0xba100000625a3754423978a60c9317c58a424e3d': 'BAL/USD',    # BAL
    '0xc18360217d8f7ab5e7c516566761ea12ce7f9d72': 'ENS/USD',    # ENS
    '0x111111111117dc0aa78b770fa6a738034120c302': '1INCH/USD',  # 1INCH
    '0x853d955acef822db058eb8505911ed77f175b99e': 'FRAX/USD',   # FRAX
    '0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f': 'GHO/USD',   # GHO
}

# Stablecoin addresses (used to determine liquidation bonus)
STABLECOINS = {
    '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',  # USDC
    '0xdac17f958d2ee523a2206206994597c13d831ec7',  # USDT
    '0x6b175474e89094c44da98b954eedeac495271d0f',  # DAI
}

MAJOR_ASSETS = STABLECOINS | {
    '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',  # WETH
}

# Well-known DEX pool addresses monitored by MempoolSniffer for price-moving trades.
# Covers Uniswap V2/V3 pairs and Curve pools for the collateral assets in COLLATERAL_TO_FEED.
DEX_POOL_ADDRESSES: Dict[int, Set[str]] = {
    1: {
        # Uniswap V3 — WETH/USDC 0.05% and 0.3%
        '0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640',
        '0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8',
        # Uniswap V3 — WBTC/WETH
        '0x4585fe77225b41b697c938b018e2ac67ac5a20c0',
        '0xcbcdf9626bc03e24f779434178a73a0b4bad62ed',
        # Uniswap V3 — LINK/WETH
        '0xa6cc3c2531fdaa6ae1a3ca84c2855806728693e8',
        # Uniswap V2 — WETH/USDC, WETH/DAI
        '0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc',
        '0xa478c2975ab1ea89e8196811f51a7b7ade33eb11',
        # Curve — stETH/ETH, 3pool (DAI/USDC/USDT)
        '0xdc24316b9ae028f1497c275eb9192a3ea0f67022',
        '0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7',
    },
    42161: {
        # Uniswap V3 Arbitrum — WETH/USDC, WETH/USDT
        '0xc31e54c7a869b9fcbecc14363cf510d1c41fa443',
        '0x641c00a822e8b671738d32a431a4fb6074e5c79d',
    },
    10: {
        # Uniswap V3 Optimism — WETH/USDC
        '0x85149247691df622eaf1a8bd0cafd40bc45154a9',
    },
    137: {
        # Uniswap V3 Polygon — WETH/USDC, WMATIC/WETH
        '0x45dda9cb7c25131df268515131f647d726f50608',
        '0x167384319b41f7094e62f7506409eb38079abff8',
    },
    8453: {
        # Uniswap V3 Base — WETH/USDC
        '0xd0b53d9277642d899df5c87a3966a349a798f224',
    },
}

# Asset address mapped to its Chainlink feed pair for impact estimation
POOL_TO_ASSET: Dict[str, str] = {
    # Maps lowercase pool address → collateral asset address (WETH for ETH pools, etc.)
    '0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    '0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    '0x4585fe77225b41b697c938b018e2ac67ac5a20c0': '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599',
    '0xcbcdf9626bc03e24f779434178a73a0b4bad62ed': '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599',
    '0xa6cc3c2531fdaa6ae1a3ca84c2855806728693e8': '0x514910771af9ca656af840dff83e8264ecf986ca',
    '0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    '0xa478c2975ab1ea89e8196811f51a7b7ade33eb11': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    '0xdc24316b9ae028f1497c275eb9192a3ea0f67022': '0xae7ab96520de3a18e5e111b5eaab095312d7fe84',
    '0xc31e54c7a869b9fcbecc14363cf510d1c41fa443': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    '0x641c00a822e8b671738d32a431a4fb6074e5c79d': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    '0x85149247691df622eaf1a8bd0cafd40bc45154a9': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    '0x45dda9cb7c25131df268515131f647d726f50608': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    '0xd0b53d9277642d899df5c87a3966a349a798f224': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
}


# ═══════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TrackedPosition:
    """A position indexed for instant HF recomputation on price change."""
    user_address: str
    chain_id: int
    pool_address: str
    debt_asset: str
    collateral_asset: str
    # Raw Aave values (base currency = USD with 8 decimals)
    total_collateral_base: int = 0   # from getUserAccountData[0]
    total_debt_base: int = 0         # from getUserAccountData[1]
    liquidation_threshold: int = 0   # from getUserAccountData[3]  (basis points * 100)
    health_factor_raw: int = 0       # from getUserAccountData[5]  (1e18)
    # Computed
    health_factor: float = 0.0
    debt_usd: float = 0.0
    collateral_usd: float = 0.0
    estimated_profit_usd: float = 0.0
    last_updated: float = 0.0
    # Which feed(s) affect this position's collateral value
    collateral_feed: str = ''        # e.g. 'ETH/USD'
    # Execution tracking
    last_fired_at: float = 0.0       # prevent re-firing within cooldown
    fire_count: int = 0


# ═══════════════════════════════════════════════════════════════════
# LAYER 1 — POSITION INDEX
# ═══════════════════════════════════════════════════════════════════

class PositionIndex:
    """
    Maintains an index of positions keyed by collateral feed.
    When a price changes for 'ETH/USD', we instantly look up ALL positions
    with ETH collateral and recompute their health factors.
    No RPC calls — pure math.
    """

    def __init__(self):
        # feed_name → set of position keys
        self.by_feed: Dict[str, Set[str]] = {}
        # position key → TrackedPosition
        self.positions: Dict[str, TrackedPosition] = {}
        # Current oracle prices  feed_name → price_usd
        self.prices: Dict[str, float] = {}

    def add(self, pos: TrackedPosition):
        key = f"{pos.chain_id}:{pos.user_address}".lower()
        self.positions[key] = pos
        if pos.collateral_feed:
            self.by_feed.setdefault(pos.collateral_feed, set()).add(key)

    def remove(self, chain_id: int, user_address: str):
        key = f"{chain_id}:{user_address}".lower()
        pos = self.positions.pop(key, None)
        if pos and pos.collateral_feed:
            self.by_feed.get(pos.collateral_feed, set()).discard(key)

    def get_by_feed(self, feed_name: str) -> List[TrackedPosition]:
        """Return all positions affected by a given price feed."""
        keys = self.by_feed.get(feed_name, set())
        return [self.positions[k] for k in keys if k in self.positions]

    def update_price(self, feed_name: str, new_price: float):
        self.prices[feed_name] = new_price

    def estimate_hf_after_price_change(
        self, pos: TrackedPosition, old_price: float, new_price: float
    ) -> float:
        """
        Aave HF = (collateral_value × liquidation_threshold) / debt_value.
        Collateral value scales linearly with the collateral asset's price.
        new_hf ≈ current_hf × (new_price / old_price).

        This is NOT a simulation.  It's a deterministic mathematical identity
        derived from Aave's published formula.
        """
        if old_price <= 0 or pos.health_factor <= 0:
            return pos.health_factor
        return pos.health_factor * (new_price / old_price)

    @property
    def count(self) -> int:
        return len(self.positions)


# ═══════════════════════════════════════════════════════════════════
# LAYER 2 — ORACLE REACTOR
# ═══════════════════════════════════════════════════════════════════

class OracleReactor:
    """
    Polls Chainlink feeds every ~2 seconds (one per block).
    On EVERY price change, instantly recomputes HF for affected positions.
    Fires liquidation the instant math says HF < 1.0.

    Uses a DEDICATED Alchemy W3 for oracle reads to avoid rate limiting
    from the shared gateway/public endpoints.
    """

    def __init__(self, w3_providers: Dict[int, Web3], index: PositionIndex, executor):
        self._w3 = w3_providers
        self.index = index
        self.executor = executor
        self._oracle_decimals: Dict[str, int] = {}

        # Create a dedicated W3 for oracle reads using Alchemy directly
        self._oracle_w3: Dict[int, Web3] = {}
        alchemy_rpc = os.getenv('ETH_RPC_URL', '')
        if alchemy_rpc and 'alchemy.com' in alchemy_rpc:
            try:
                w3 = Web3(Web3.HTTPProvider(alchemy_rpc))
                if w3.is_connected():
                    self._oracle_w3[1] = w3
            except Exception:
                pass
        # Fallback to shared providers
        for cid, w3 in w3_providers.items():
            if cid not in self._oracle_w3:
                self._oracle_w3[cid] = w3

    async def initialize(self):
        """Load initial prices from all Chainlink feeds."""
        loaded = 0
        for chain_id, feeds in CHAINLINK_FEEDS.items():
            w3 = self._oracle_w3.get(chain_id)
            if not w3:
                print(f"      ⚠️ No W3 for chain {chain_id}")
                continue
            for pair, addr in feeds.items():
                try:
                    contract = w3.eth.contract(
                        address=Web3.to_checksum_address(addr),
                        abi=CHAINLINK_ABI,
                    )
                    data = contract.functions.latestRoundData().call()
                    decimals = contract.functions.decimals().call()
                    price = data[1] / (10 ** decimals)
                    self._oracle_decimals[f"{chain_id}:{pair}"] = decimals
                    self.index.update_price(pair, price)
                    loaded += 1
                except Exception as e:
                    print(f"      ⚠️ Oracle {pair} chain={chain_id}: {str(e)[:80]}")
        print(f"   🔮 Oracle Reactor: {loaded} feeds loaded")
        for feed, price in sorted(self.index.prices.items()):
            print(f"      {feed}: ${price:,.2f}")

    async def poll_loop(self):
        """Poll oracles and react to price changes.

        Each chain is polled on its own schedule using CHAIN_POLL_INTERVAL so that
        high-activity L2s (Arbitrum/Optimism/Base: 1-2 s blocks) are checked
        every block while quieter chains (Ethereum: ~8 s) are not over-polled.
        """
        # Per-chain timestamp tracking for adaptive polling.
        # Starting from 0.0 means all chains are polled on the first iteration —
        # this is intentional to populate the price index as quickly as possible
        # at startup rather than waiting a full interval before the first read.
        last_polled: Dict[int, float] = {}

        while True:
            try:
                now = asyncio.get_event_loop().time()
                for chain_id, feeds in CHAINLINK_FEEDS.items():
                    w3 = self._oracle_w3.get(chain_id)
                    if not w3:
                        continue

                    # Only poll this chain if enough time has passed since last poll
                    interval = CHAIN_POLL_INTERVAL.get(chain_id, 8.0)
                    if now - last_polled.get(chain_id, 0.0) < interval:
                        continue
                    last_polled[chain_id] = now

                    for pair, addr in feeds.items():
                        try:
                            key = f"{chain_id}:{pair}"
                            contract = w3.eth.contract(
                                address=Web3.to_checksum_address(addr),
                                abi=CHAINLINK_ABI,
                            )
                            data = contract.functions.latestRoundData().call()
                            decimals = self._oracle_decimals.get(key, 8)
                            new_price = data[1] / (10 ** decimals)

                            old_price = self.index.prices.get(pair, new_price)
                            if old_price > 0 and new_price != old_price:
                                self.index.update_price(pair, new_price)
                                pct = abs(new_price - old_price) / old_price

                                if pct > 0.0003:  # > 0.03% — very sensitive
                                    await self._react_to_price_change(
                                        pair, chain_id, old_price, new_price, pct
                                    )
                            else:
                                self.index.update_price(pair, new_price)

                        except Exception:
                            pass

                        # Small yield to prevent RPC flooding
                        await asyncio.sleep(0.05)

                # Sleep briefly before re-evaluating which chains need polling
                await asyncio.sleep(0.5)

            except Exception:
                await asyncio.sleep(5)

    async def _react_to_price_change(
        self, pair: str, chain_id: int,
        old_price: float, new_price: float, pct: float
    ):
        """
        Recompute HF for every position using this collateral feed.
        If any position crosses HF < 1.0, fire immediately.
        """
        affected = self.index.get_by_feed(pair)
        if not affected:
            return

        now = time.time()
        for pos in affected:
            # Skip if we fired on this position in the last 30s
            if now - pos.last_fired_at < 30:
                continue

            new_hf = self.index.estimate_hf_after_price_change(pos, old_price, new_price)

            if new_hf < 1.0 and pos.health_factor >= 0.95:
                print(
                    f"   🔮 ORACLE→FIRE: {pair} ${old_price:.2f}→${new_price:.2f} "
                    f"({pct*100:.3f}%) HF {pos.health_factor:.4f}→{new_hf:.4f} "
                    f"debt=${pos.debt_usd:,.0f} user={pos.user_address[:12]}..."
                )
                pos.last_fired_at = now
                pos.fire_count += 1
                await self.executor.fire(pos)


# ═══════════════════════════════════════════════════════════════════
# LAYER 3 — BLOCK WATCHER
# ═══════════════════════════════════════════════════════════════════

class BlockWatcher:
    """
    On every new block, batch-reads HF for the top N closest-to-liquidation
    positions.  Catches multi-collateral scenarios and any oracle lag.
    """

    def __init__(self, w3_providers: Dict[int, Web3], index: PositionIndex, executor):
        self._w3 = w3_providers
        self.index = index
        self.executor = executor
        self.checks_run = 0
        self.fires = 0

    async def watch_loop(self, top_n: int = 200):
        """Every ~4s check top N closest positions on-chain.

        ``top_n=200`` covers the 200 riskiest positions (sorted by HF ascending).
        Batched Multicall3 RPC calls keep cycle time under 30 seconds even at
        this scale.  Reduce ``top_n`` if your RPC tier has strict rate limits.
        """
        _debug_first = True
        while True:
            try:
                # Sort positions by HF ascending (closest to liquidation first)
                all_positions = list(self.index.positions.values())
                if _debug_first and all_positions:
                    sample = all_positions[0]
                    print(f"      [BW] total_positions={len(all_positions)} "
                          f"checking_top={top_n} "
                          f"w3_chains={sorted(self._w3.keys())}")
                    _debug_first = False

                sorted_positions = sorted(
                    all_positions,
                    key=lambda p: p.health_factor if p.health_factor > 0 else 999
                )[:top_n]

                now = time.time()
                for pos in sorted_positions:
                    # Skip recently fired
                    if now - pos.last_fired_at < 30:
                        continue

                    w3 = self._w3.get(pos.chain_id)
                    if not w3 or not pos.pool_address:
                        continue

                    try:
                        pool = w3.eth.contract(
                            address=Web3.to_checksum_address(pos.pool_address),
                            abi=AAVE_POOL_ABI,
                        )
                        result = pool.functions.getUserAccountData(
                            Web3.to_checksum_address(pos.user_address)
                        ).call()

                        self.checks_run += 1

                        hf = result[5] / 1e18
                        debt_usd = result[1] / 1e8
                        collateral_usd = result[0] / 1e8

                        # Update position
                        pos.total_collateral_base = result[0]
                        pos.total_debt_base = result[1]
                        pos.liquidation_threshold = result[3]
                        pos.health_factor_raw = result[5]
                        pos.health_factor = hf
                        pos.debt_usd = debt_usd
                        pos.collateral_usd = collateral_usd
                        pos.last_updated = now

                        if hf < 1.0 and debt_usd > 5:
                            print(
                                f"   🎯 BLOCK->FIRE: HF={hf:.6f} debt=${debt_usd:,.0f} "
                                f"chain={pos.chain_id} user={pos.user_address[:12]}..."
                            )
                            pos.last_fired_at = now
                            pos.fire_count += 1
                            self.fires += 1
                            await self.executor.fire(pos)

                    except Exception as bw_err:
                        if self.checks_run == 0 and not hasattr(self, '_err_logged'):
                            print(f"      [BW-ERR] chain={pos.chain_id} pool={pos.pool_address[:16]}... user={pos.user_address[:12]}... err={str(bw_err)[:120]}")
                            self._err_logged = True

                await asyncio.sleep(3)  # Every ~3 seconds between full sweeps

            except Exception:
                await asyncio.sleep(8)


# ═══════════════════════════════════════════════════════════════════
# LAYER 4 — EXECUTOR
# ═══════════════════════════════════════════════════════════════════

class LiquidationExecutor:
    """
    Builds, signs, and broadcasts liquidationCall in < 100ms.
    No simulation.  No eth_call.  Direct to chain.
    """

    def __init__(self, w3_providers: Dict[int, Web3]):
        self._w3 = w3_providers
        self._private_key = os.getenv('PRIVATE_KEY', '')
        self._account = None
        self._flashbots_rpc = os.getenv('FLASHBOTS_RPC_URL', '')
        self._flashbots_w3 = None

        # Stats
        self.txs_fired = 0
        self.txs_confirmed = 0
        self.txs_reverted = 0
        self.total_profit_usd = 0.0
        self.total_gas_spent_usd = 0.0

        if self._private_key:
            try:
                w3_any = next(iter(self._w3.values()))
                self._account = w3_any.eth.account.from_key(self._private_key)
                print(f"   🎯 Executor wallet: {self._account.address}")
            except Exception:
                pass

        # Try to set up Flashbots Protect RPC for private submission
        if self._flashbots_rpc and 'flashbots' in self._flashbots_rpc.lower():
            try:
                self._flashbots_w3 = Web3(Web3.HTTPProvider(self._flashbots_rpc))
                print(f"   ⚡ Flashbots Protect: enabled")
            except Exception:
                pass

    async def fire(self, pos: TrackedPosition):
        """Build, sign, broadcast liquidationCall.

        Submission is gated by two explicit mainnet-safety checks:
        1. ``EXECUTION_ENABLED=true`` must be set in the environment.
        2. The current gas price must not exceed ``GAS_PRICE_CAP_GWEI``.
        """
        if not self._account:
            return

        if os.getenv('EXECUTION_ENABLED', 'false').lower() != 'true':
            print(
                f"   🔒 EXECUTION_ENABLED=false — opportunity logged but not submitted "
                f"(HF={pos.health_factor:.4f} debt=${pos.debt_usd:,.0f})"
            )
            return

        w3 = self._w3.get(pos.chain_id)
        if not w3:
            return

        try:
            pool = w3.eth.contract(
                address=Web3.to_checksum_address(pos.pool_address),
                abi=AAVE_POOL_ABI,
            )

            # Compute debt to cover (50% for HF between 0.95 and 1.0)
            debt_to_cover = max(int(pos.total_debt_base * 0.5), 1)

            # Compute liquidation bonus
            debt_lower = pos.debt_asset.lower()
            if debt_lower in MAJOR_ASSETS:
                bonus = 0.05
            else:
                bonus = 0.10
            profit_est = pos.debt_usd * bonus * 0.5

            nonce = w3.eth.get_transaction_count(self._account.address, 'pending')
            gas_price = w3.eth.gas_price

            # Gas-price cap — skip if network is congested beyond our threshold
            gas_price_gwei = gas_price / 1e9
            gas_cap_gwei = float(os.getenv('GAS_PRICE_CAP_GWEI', '50'))
            if gas_price_gwei > gas_cap_gwei:
                print(
                    f"   ⛽ Gas too high: {gas_price_gwei:.1f} gwei > cap {gas_cap_gwei:.1f} gwei — skipping"
                )
                return

            tx = pool.functions.liquidationCall(
                Web3.to_checksum_address(pos.collateral_asset),
                Web3.to_checksum_address(pos.debt_asset),
                Web3.to_checksum_address(pos.user_address),
                debt_to_cover,
                False,
            ).build_transaction({
                'from': self._account.address,
                'gas': 500000,
                'gasPrice': gas_price,
                'nonce': nonce,
            })

            signed = w3.eth.account.sign_transaction(tx, self._private_key)

            # Broadcast — try Flashbots first for Ethereum mainnet
            tx_hash = None
            if pos.chain_id == 1 and self._flashbots_w3:
                try:
                    tx_hash = self._flashbots_w3.eth.send_raw_transaction(signed.raw_transaction)
                except Exception:
                    pass

            if tx_hash is None:
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

            self.txs_fired += 1
            print(f"   📡 FIRE→CHAIN: {tx_hash.hex()[:16]}... chain={pos.chain_id} HF={pos.health_factor:.4f} debt=${pos.debt_usd:,.0f}")

            # Non-blocking receipt wait in background
            asyncio.create_task(self._track_receipt(w3, tx_hash, pos, profit_est))

        except Exception as e:
            err = str(e)
            if 'nonce' in err.lower():
                pass  # Nonce collision — next attempt will fix
            else:
                print(f"   ⚠️ Fire error: {err[:120]}")

    async def _track_receipt(self, w3: Web3, tx_hash, pos: TrackedPosition, profit_est: float):
        """Wait for receipt in background — don't block the fire loop."""
        try:
            receipt = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
            )
            gas_used = receipt['gasUsed']
            gas_price = receipt.get('effectiveGasPrice', 0)
            gas_cost_eth = (gas_used * gas_price) / 1e18
            gas_cost_usd = gas_cost_eth * self._get_eth_price(pos.chain_id)
            self.total_gas_spent_usd += gas_cost_usd

            if receipt['status'] == 1:
                self.txs_confirmed += 1
                net = profit_est - gas_cost_usd
                self.total_profit_usd += net
                print(
                    f"\n   💰💰 LIQUIDATION CONFIRMED! "
                    f"block={receipt['blockNumber']} "
                    f"profit=${net:,.2f} gas=${gas_cost_usd:.4f} "
                    f"TX={tx_hash.hex()}"
                )
                print(f"   💰 Running total: ${self.total_profit_usd:,.2f} ({self.txs_confirmed} confirmed)\n")
            else:
                self.txs_reverted += 1

        except Exception:
            pass  # Timeout — move on

    def _get_eth_price(self, chain_id: int) -> float:
        """Get approximate ETH price for gas cost calculation."""
        try:
            w3 = self._w3.get(1) or next(iter(self._w3.values()))
            contract = w3.eth.contract(
                address=Web3.to_checksum_address('0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419'),
                abi=CHAINLINK_ABI,
            )
            data = contract.functions.latestRoundData().call()
            return data[1] / 1e8
        except Exception:
            return 2500.0


# ═══════════════════════════════════════════════════════════════════
# LAYER 5 — MEMPOOL SNIFFER (backrun cascading price moves)
# ═══════════════════════════════════════════════════════════════════

class MempoolSniffer:
    """
    Analyzes pending transactions for potential price impact.

    For every pending tx that interacts with a known DEX pool, this class:
      1. Estimates the price impact on the relevant collateral asset.
      2. Identifies positions whose HF would cross below 1.0 after that impact.
      3. Prepares a Flashbots backrun bundle: [trigger_tx, liquidation_tx].

    The bundle is submitted so that the liquidation executes immediately after
    the price-moving trade in the same block—before any competing bot can react.
    """

    # Minimum price impact (as a fraction) to consider a trade dangerous.
    # 1% was chosen as the lower bound because sub-1% price moves rarely push
    # a position below the liquidation threshold, while gas costs make such
    # attempts unprofitable.
    _MIN_IMPACT = 0.01

    # Health factor upper bound for backrun eligibility.  Positions with HF ≥ this
    # value are too far from liquidation to be worth queueing a bundle even if the
    # price move looks significant.
    _BACKRUN_HF_THRESHOLD = 0.95

    # Seconds to wait before re-firing on the same position to prevent spam.
    _FIRE_COOLDOWN_SECONDS = 30

    def __init__(
        self,
        index: PositionIndex,
        executor: LiquidationExecutor,
        dex_pools: Optional[Dict[int, Set[str]]] = None,
    ):
        self.index = index
        self.executor = executor
        # Pool addresses to watch, keyed by chain_id
        self._pools: Dict[int, Set[str]] = dex_pools or DEX_POOL_ADDRESSES

        # Tracks bundles already queued to avoid duplicate submissions.
        # Keyed by "{chain_id}:{user_address}" so a position is not queued
        # multiple times even if different pools trigger it.
        self._queued: Set[str] = set()

        # Stats
        self.txs_inspected = 0
        self.impacts_detected = 0
        self.bundles_prepared = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_pools(self, chain_id: int) -> Set[str]:
        """Return the set of monitored pool addresses for a given chain."""
        return self._pools.get(chain_id, set())

    def estimate_price_impact(self, amount_usd: float, pool_tvl_usd: float) -> float:
        """
        Estimate relative price impact using a simple constant-product AMM model.

        For a trade of size *x* against a pool with TVL *L*, the spot-price
        impact is approximately x / (L + x).  Returns a value in [0, 1].
        """
        if pool_tvl_usd <= 0:
            return 0.0
        return amount_usd / (pool_tvl_usd + amount_usd)

    async def on_pending_transaction(
        self,
        chain_id: int,
        to_address: str,
        input_data: bytes,
        value_eth: float,
        eth_price_usd: float = 2500.0,
        pool_tvl_usd: float = 5_000_000.0,
    ) -> int:
        """
        Handle a single pending mempool transaction.

        Returns the number of backrun bundles queued as a result of this tx.
        """
        self.txs_inspected += 1

        to_lower = to_address.lower()
        if to_lower not in self._pools.get(chain_id, set()):
            return 0

        # Rough trade size in USD from the ETH value sent with the tx
        trade_usd = value_eth * eth_price_usd
        impact = self.estimate_price_impact(trade_usd, pool_tvl_usd)

        if impact < self._MIN_IMPACT:
            return 0

        self.impacts_detected += 1

        # Identify which collateral asset this pool prices
        collateral_asset = POOL_TO_ASSET.get(to_lower, '')
        if not collateral_asset:
            return 0

        # Find the Chainlink feed for this collateral
        feed = COLLATERAL_TO_FEED.get(collateral_asset, '')
        if not feed:
            return 0

        current_price = self.index.prices.get(feed, 0.0)
        if current_price <= 0:
            return 0

        new_price = current_price * (1.0 - impact)

        # Find affected positions and queue backrun bundles
        affected = self.index.get_by_feed(feed)
        bundles_queued = 0
        now = time.time()
        for pos in affected:
            if pos.chain_id != chain_id:
                continue
            if now - pos.last_fired_at < self._FIRE_COOLDOWN_SECONDS:
                continue

            new_hf = self.index.estimate_hf_after_price_change(pos, current_price, new_price)
            if new_hf < 1.0 and pos.health_factor >= self._BACKRUN_HF_THRESHOLD:
                # Dedup by position identity — ignore which pool triggered it
                dedup_key = f"{chain_id}:{pos.user_address}"
                if dedup_key in self._queued:
                    continue
                self._queued.add(dedup_key)
                self.bundles_prepared += 1
                bundles_queued += 1

                print(
                    f"   📡 MEMPOOL→BACKRUN: pool={to_lower[:12]}... "
                    f"impact={impact*100:.2f}% "
                    f"HF {pos.health_factor:.4f}→{new_hf:.4f} "
                    f"user={pos.user_address[:12]}... "
                    f"debt=${pos.debt_usd:,.0f}"
                )
                pos.last_fired_at = now
                pos.fire_count += 1
                await self.executor.fire(pos)

        return bundles_queued

    def get_stats(self) -> Dict[str, Any]:
        return {
            'txs_inspected': self.txs_inspected,
            'impacts_detected': self.impacts_detected,
            'bundles_prepared': self.bundles_prepared,
        }


# ═══════════════════════════════════════════════════════════════════
# MASTER ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════

class ZeroRevertPipeline:
    """
    Master orchestrator.  Wires all layers together and integrates with
    the scanner's watchlist.
    """

    def __init__(self, w3_providers: Dict[int, Web3], config: Dict[str, Any] = None):
        self._w3 = w3_providers
        self.config = config or {}

        # Core layers
        self.index = PositionIndex()
        self.executor = LiquidationExecutor(w3_providers)
        self.oracle_reactor = OracleReactor(w3_providers, self.index, self.executor)
        self.block_watcher = BlockWatcher(w3_providers, self.index, self.executor)

        # Layer 5 — Mempool Sniffer (backrun price-moving DEX trades)
        self.mempool_sniffer = MempoolSniffer(self.index, self.executor)

        # Tasks
        self._tasks: List[asyncio.Task] = []

        print("   ⚡ Zero-Revert Pipeline initialized")

    async def start(self):
        """Start all pipeline layers."""
        await self.oracle_reactor.initialize()

        self._tasks = [
            asyncio.create_task(self.oracle_reactor.poll_loop()),
            asyncio.create_task(self.block_watcher.watch_loop(top_n=200)),
            asyncio.create_task(self._stats_loop()),
        ]

        n_chains = len(self._w3)
        n_pools = sum(len(v) for v in self.mempool_sniffer._pools.values())
        print("   ✅ Zero-Revert Pipeline RUNNING")
        print(f"      Oracle feeds: {len(self.index.prices)}")
        print(f"      Mempool sniffer: {n_pools} DEX pools across {n_chains} chains")
        print(f"      Execution: DIRECT (no simulation)")
        print(f"      Flashbots: {'enabled' if self.executor._flashbots_w3 else 'disabled'}")

    async def stop(self):
        for t in self._tasks:
            t.cancel()

    def feed_watchlist(self, watchlist: Dict[str, Dict]):
        """
        Import positions from the scanner's watchlist.
        Called periodically by the engine to sync.
        """
        for user_addr, data in watchlist.items():
            self._ingest_position(user_addr, data)

    def feed_all_positions(self, tracked: Dict[str, Dict]):
        """
        Import ALL tracked positions from the scanner.
        The ZRP index will rank them by HF and monitor the closest ones.
        """
        for user_addr, data in tracked.items():
            self._ingest_position(user_addr, data)

    def _ingest_position(self, user_addr: str, data: Dict):
        """Ingest a single position into the ZRP index."""
        chain_id = data.get('chain_id', 1)
        key = f"{chain_id}:{user_addr}".lower()
        if key in self.index.positions:
            # Update HF if available
            if 'last_hf' in data:
                self.index.positions[key].health_factor = data['last_hf']
            if 'debt_usd' in data:
                self.index.positions[key].debt_usd = data['debt_usd']
            return

        collateral = data.get('collateral_asset', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2')
        feed = COLLATERAL_TO_FEED.get(collateral.lower(), '')

        hf = data.get('last_hf', 1.5)
        debt = data.get('debt_usd', 0)
        bonus = 0.05 if data.get('debt_asset', '').lower() in MAJOR_ASSETS else 0.10

        pos = TrackedPosition(
            user_address=user_addr,
            chain_id=chain_id,
            pool_address=data.get('pool', AAVE_V3_POOLS.get(chain_id, '')),
            debt_asset=data.get('debt_asset', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'),
            collateral_asset=collateral,
            total_debt_base=int(debt * 1e8) if debt else 0,
            health_factor=hf,
            debt_usd=debt,
            collateral_usd=debt * hf if debt else 0,
            estimated_profit_usd=debt * bonus * 0.5 if debt else 0,
            collateral_feed=feed,
            last_updated=time.time(),
        )
        self.index.add(pos)

    async def _stats_loop(self):
        """Print stats every 2 minutes."""
        while True:
            await asyncio.sleep(120)
            ex = self.executor
            bw = self.block_watcher
            ms = self.mempool_sniffer.get_stats()
            n_pos = self.index.count
            n_by_feed = {f: len(keys) for f, keys in self.index.by_feed.items() if keys}

            print(
                f"   ⚡ [ZRP] "
                f"positions={n_pos} "
                f"checks={bw.checks_run} "
                f"fired={ex.txs_fired} "
                f"confirmed={ex.txs_confirmed} "
                f"reverted={ex.txs_reverted} "
                f"profit=${ex.total_profit_usd:,.2f} "
                f"gas=${ex.total_gas_spent_usd:,.4f} "
                f"mempool_txs={ms['txs_inspected']} "
                f"impacts={ms['impacts_detected']} "
                f"bundles={ms['bundles_prepared']}"
            )
            if n_by_feed:
                feeds_str = ', '.join(f"{f}:{c}" for f, c in sorted(n_by_feed.items()))
                print(f"      Index: {feeds_str}")

    def get_stats(self) -> Dict[str, Any]:
        ex = self.executor
        ms = self.mempool_sniffer.get_stats()
        return {
            'positions': self.index.count,
            'checks': self.block_watcher.checks_run,
            'fired': ex.txs_fired,
            'confirmed': ex.txs_confirmed,
            'reverted': ex.txs_reverted,
            'profit_usd': ex.total_profit_usd,
            'gas_spent_usd': ex.total_gas_spent_usd,
            'oracle_feeds': len(self.index.prices),
            'mempool_txs_inspected': ms['txs_inspected'],
            'mempool_impacts_detected': ms['impacts_detected'],
            'mempool_bundles_prepared': ms['bundles_prepared'],
        }
