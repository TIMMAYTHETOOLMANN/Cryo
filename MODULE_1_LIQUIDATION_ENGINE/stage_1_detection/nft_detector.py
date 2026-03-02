#!/usr/bin/env python3
"""
STAGE 1 — NFT-Collateralized Loan Detector (Script 3 Enhancement #3)
======================================================================
Detects liquidation opportunities on NFT lending protocols:
  - BendDAO (NFT-backed ETH loans)
  - NFTfi (peer-to-peer NFT loans)
  - JPEG'd (NFT-backed stablecoin loans)
  - ParaSpace (NFT + ERC-20 lending)

NFT liquidations carry 10-15% bonuses due to illiquidity premium,
significantly higher than standard DeFi liquidations (5%).

Zero capital: flash loan covers debt, seize NFT, sell atomically
via Seaport/Sudoswap/Blur within the same TX.
"""

import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass

from web3 import Web3

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


@dataclass
class NFTLiquidationOpportunity:
    """Detected NFT liquidation candidate."""
    chain_id: int
    protocol: str
    borrower: str
    nft_contract: str
    nft_token_id: int
    collection_name: str
    debt_asset: str
    debt_amount_usd: float
    floor_price_usd: float
    liquidation_bonus: float
    estimated_profit_usd: float
    health_factor: float
    marketplace_exit: str  # "seaport", "sudoswap", "blur"
    timestamp: int = 0

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = int(time.time())


# Protocol ABIs for NFT lending health checks
BENDDAO_ABI_SNIPPET = [
    {
        "inputs": [{"name": "nftAsset", "type": "address"},
                   {"name": "nftTokenId", "type": "uint256"}],
        "name": "getNftDebtData",
        "outputs": [
            {"name": "loanId", "type": "uint256"},
            {"name": "reserveAsset", "type": "address"},
            {"name": "totalCollateral", "type": "uint256"},
            {"name": "totalDebt", "type": "uint256"},
            {"name": "availableBorrows", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

# Known NFT collections with floor price oracles
SUPPORTED_COLLECTIONS = {
    "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D": {
        "name": "BAYC",
        "floor_oracle": "chainlink_nft_floor",
        "avg_bonus": 0.10,
    },
    "0xb7F7F6C52F2e2fdb1963Eab30438024864c313F6": {
        "name": "CryptoPunks (Wrapped)",
        "floor_oracle": "chainlink_nft_floor",
        "avg_bonus": 0.10,
    },
    "0x60E4d786628Fea6478F785A6d7e704777c86a7c6": {
        "name": "MAYC",
        "floor_oracle": "chainlink_nft_floor",
        "avg_bonus": 0.12,
    },
    "0xED5AF388653567Af2F388E6224dC7C4b3241C544": {
        "name": "Azuki",
        "floor_oracle": "chainlink_nft_floor",
        "avg_bonus": 0.15,
    },
}

# Marketplace configs for atomic NFT sales
NFT_MARKETPLACES = {
    "sudoswap": {"gas_units": 200_000, "fee_pct": 0.005},
    "blur": {"gas_units": 250_000, "fee_pct": 0.005},
    "seaport": {"gas_units": 300_000, "fee_pct": 0.025},
}


class NFTLiquidationDetector:
    """
    Scans NFT lending protocols for underwater positions.
    Higher bonuses (10-15%) and lower competition than standard DeFi.
    """

    # BendDAO addresses (Ethereum mainnet)
    BENDDAO_LENDING_POOL = "0x70b97A0da65C15dfb0FFA02aEE6FA36e507C2762"
    BENDDAO_DATA_PROVIDER = "0x1F4cB49E42CC2107dAb74af2c29B7b2a24C32FF3"

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._w3: Optional[Web3] = None

    def _get_w3(self) -> Optional[Web3]:
        if self._w3:
            return self._w3
        chain = self.config.get_chain(1)  # NFT lending is primarily Ethereum
        if chain and chain.rpc_url:
            self._w3 = Web3(Web3.HTTPProvider(chain.rpc_url, request_kwargs={"timeout": 15}))
        return self._w3

    async def scan(self) -> List[NFTLiquidationOpportunity]:
        """Scan all supported NFT lending protocols."""
        results: List[NFTLiquidationOpportunity] = []

        w3 = self._get_w3()
        if not w3:
            return results

        # BendDAO scanning
        try:
            benddao_opps = await self._scan_benddao(w3)
            results.extend(benddao_opps)
        except Exception as e:
            logger.debug(f"BendDAO scan error: {e}")

        if results:
            logger.info(f"🎨 NFT Detector: {len(results)} NFT liquidation opportunities")

        return results

    async def _scan_benddao(self, w3: Web3) -> List[NFTLiquidationOpportunity]:
        """Scan BendDAO for underwater NFT positions."""
        found = []

        for nft_addr, collection in SUPPORTED_COLLECTIONS.items():
            try:
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(self.BENDDAO_DATA_PROVIDER),
                    abi=BENDDAO_ABI_SNIPPET,
                )

                # In production: iterate known active loan IDs from events
                # For now: the adapter is ready to be called with specific token IDs
                # when the indexer provides them

            except Exception as e:
                logger.debug(f"BendDAO {collection['name']} error: {e}")

        return found

    def evaluate_nft_exit(
        self,
        floor_price_usd: float,
        debt_usd: float,
        bonus: float,
        gas_gwei: float = 30.0,
        eth_price: float = 2500.0,
    ) -> Dict[str, float]:
        """Evaluate all marketplace exits for an NFT liquidation."""
        results = {}
        seized_value = debt_usd * (1 + bonus)

        for name, mp in NFT_MARKETPLACES.items():
            mp_fee = floor_price_usd * mp["fee_pct"]
            gas_cost = (mp["gas_units"] * gas_gwei) / 1e9 * eth_price
            net = seized_value - debt_usd - mp_fee - gas_cost
            results[name] = net

        return results
