#!/usr/bin/env python3
"""
Liquidation Formula Extractor
Extract health factor formulas and liquidation thresholds from lending protocols

Analyzes:
- Health factor calculation
- Liquidation thresholds
- Collateral factors
- Liquidation bonus/penalty
- Close factors
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from web3 import Web3


@dataclass
class HealthFactorConfig:
    """Health factor configuration for a lending protocol"""
    protocol_name: str
    contract_address: str
    chain_id: int
    health_factor_function: str
    liquidation_function: str
    liquidation_threshold: float  # e.g., 0.8 = can liquidate when HF < 0.8
    liquidation_bonus: float  # e.g., 0.05 = 5% bonus
    collateral_factor: float  # e.g., 0.85 = 85% collateralization required
    liquidation_close_factor: float  # e.g., 0.5 = can close 50% at once
    minimum_health_factor: float  # e.g., 0.95 = target HF after liquidation
    supported_assets: List[str] = field(default_factory=list)
    oracle_address: str = ""
    is_active: bool = True


@dataclass
class PositionData:
    """User position data for health factor calculation"""
    user_address: str
    supplied_assets: Dict[str, float]  # asset -> amount
    borrowed_assets: Dict[str, float]  # asset -> amount
    collateral_assets: List[str] = field(default_factory=list)
    health_factor: float = 0
    can_be_liquidated: bool = False
    liquidation_profit_usd: float = 0


class LiquidationFormulaExtractor:
    """
    Extract liquidation formulas and configurations from lending protocols
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Known protocol configurations
        self._protocol_configs: Dict[str, HealthFactorConfig] = {}
        self._initialize_known_protocols()

        # Web3 providers
        self.w3_providers: Dict[int, Web3] = {}
        self._init_providers()

        # Statistics
        self.protocols_configured = 0
        self.positions_analyzed = 0

        print("📊 Liquidation Formula Extractor initialized")

    def _init_providers(self):
        """Initialize Web3 providers"""
        import os
        rpc_endpoints = {
            1: os.getenv('ETH_RPC_URL', 'https://eth.llamarpc.com'),
            42161: os.getenv('ARBITRUM_RPC_URL', 'https://arb1.arbitrum.io/rpc'),
            10: os.getenv('OPTIMISM_RPC_URL', 'https://mainnet.optimism.io'),
            137: os.getenv('POLYGON_RPC_URL', 'https://polygon-rpc.com'),
            8453: os.getenv('BASE_RPC_URL', 'https://mainnet.base.org'),
        }

        for chain_id, rpc_url in rpc_endpoints.items():
            try:
                self.w3_providers[chain_id] = Web3(Web3.HTTPProvider(rpc_url))
            except Exception as e:
                print(f"   ⚠️  RPC init error for chain {chain_id}: {e}")

    def _initialize_known_protocols(self):
        """Initialize known lending protocol configurations"""
        # Aave V3
        self._protocol_configs['aave_v3'] = HealthFactorConfig(
            protocol_name='Aave V3',
            contract_address='0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2',
            chain_id=1,
            health_factor_function='0x6f400813',  # getUserAccountData
            liquidation_function='0x41013712',  # liquidationCall
            liquidation_threshold=0.95,  # Can liquidate when HF < 0.95
            liquidation_bonus=0.05,  # 5% liquidation bonus
            collateral_factor=0.85,  # 85% collateralization
            liquidation_close_factor=0.5,  # Can close 50% at once
            minimum_health_factor=0.98,  # Target HF after liquidation
            supported_assets=['ETH', 'WBTC', 'USDC', 'USDT', 'DAI'],
            oracle_address='0x54586bE62E3c3580375aE3723220A5CD44E25966',  # Aave oracle
        )
        self.protocols_configured += 1

        # Aave V3 - Arbitrum
        self._protocol_configs['aave_v3_arbitrum'] = HealthFactorConfig(
            protocol_name='Aave V3',
            contract_address='0x794a61358D6845594F94dc1DB02A252b5b4814aD',
            chain_id=42161,
            health_factor_function='0x6f400813',
            liquidation_function='0x41013712',
            liquidation_threshold=0.95,
            liquidation_bonus=0.05,
            collateral_factor=0.85,
            liquidation_close_factor=0.5,
            minimum_health_factor=0.98,
            supported_assets=['ETH', 'WBTC', 'USDC', 'USDT'],
        )
        self.protocols_configured += 1

        # Compound V3
        self._protocol_configs['compound_v3'] = HealthFactorConfig(
            protocol_name='Compound V3',
            contract_address='0xc3d688B66703497DAA19211EEdff47f25384cdc3',
            chain_id=1,
            health_factor_function='0x0415f470',  # getUserAccountData
            liquidation_function='0x0144894a',  # absorb
            liquidation_threshold=1.0,  # Can liquidate when HF < 1
            liquidation_bonus=0.08,  # 8% liquidation bonus
            collateral_factor=0.8,  # 80% collateralization
            liquidation_close_factor=1.0,  # Can close 100% at once
            minimum_health_factor=1.0,
            supported_assets=['ETH', 'WBTC', 'USDC', 'COMP'],
        )
        self.protocols_configured += 1

        # Morpho
        self._protocol_configs['morpho'] = HealthFactorConfig(
            protocol_name='Morpho',
            contract_address='0x777777c9898D384F785Ee44Acfe945efDFf5f3E0',
            chain_id=1,
            health_factor_function='0x5b3ef507',  # getUserData
            liquidation_function='0x51945437',  # liquidate
            liquidation_threshold=0.95,
            liquidation_bonus=0.05,
            collateral_factor=0.85,
            liquidation_close_factor=0.5,
            minimum_health_factor=0.98,
            supported_assets=['ETH', 'WBTC', 'USDC', 'DAI'],
        )
        self.protocols_configured += 1

        print(f"   📊 Initialized {self.protocols_configured} protocol configurations")

    async def start(self):
        """Start extractor"""
        print("\n📊 Starting Liquidation Formula Extractor...")
        self.is_running = True
        print("   ✅ Liquidation Formula Extractor started")

    async def stop(self):
        """Stop extractor"""
        self.is_running = False
        print("   📊 Liquidation Formula Extractor stopped")

    def get_protocol_config(self, protocol_name: str, chain_id: int = None) -> Optional[HealthFactorConfig]:
        """Get configuration for protocol"""
        for config in self._protocol_configs.values():
            if config.protocol_name == protocol_name:
                if chain_id is None or config.chain_id == chain_id:
                    return config
        return None

    def get_config_by_address(self, contract_address: str) -> Optional[HealthFactorConfig]:
        """Get configuration by contract address"""
        address_lower = contract_address.lower()
        for config in self._protocol_configs.values():
            if config.contract_address.lower() == address_lower:
                return config
        return None

    def calculate_health_factor(self, protocol: str, position: PositionData) -> float:
        """Calculate health factor for position"""
        config = self._protocol_configs.get(protocol)
        if not config:
            return 0

        # Simplified health factor calculation
        # Real implementation would fetch prices from oracles

        total_collateral_usd = 0
        total_borrow_usd = 0

        # Calculate total collateral value
        for asset in position.supplied_assets:
            amount = position.supplied_assets[asset]
            price = self._get_asset_price(asset)
            total_collateral_usd += amount * price

        # Calculate total borrow value
        for asset in position.borrowed_assets:
            amount = position.borrowed_assets[asset]
            price = self._get_asset_price(asset)
            total_borrow_usd += amount * price

        # Health factor = (collateral * collateral_factor) / borrow
        if total_borrow_usd <= 0:
            return float('inf')  # No borrow = infinite HF

        adjusted_collateral = total_collateral_usd * config.collateral_factor
        health_factor = adjusted_collateral / total_borrow_usd

        position.health_factor = health_factor
        position.can_be_liquidated = health_factor < config.liquidation_threshold

        # Calculate liquidation profit
        if position.can_be_liquidated:
            # Profit = debt * liquidation_bonus
            position.liquidation_profit_usd = total_borrow_usd * config.liquidation_bonus

        self.positions_analyzed += 1

        return health_factor

    def get_liquidation_amount(self, protocol: str, position: PositionData,
                                debt_asset: str, collateral_asset: str) -> float:
        """Calculate optimal liquidation amount"""
        config = self._protocol_configs.get(protocol)
        if not config:
            return 0

        # Get debt amount
        debt_amount = position.borrowed_assets.get(debt_asset, 0)
        if debt_amount <= 0:
            return 0

        # Get collateral amount
        collateral_amount = position.supplied_assets.get(collateral_asset, 0)
        if collateral_amount <= 0:
            return 0

        # Calculate max liquidatable amount
        debt_price = self._get_asset_price(debt_asset)
        collateral_price = self._get_asset_price(collateral_asset)

        # Max debt to repay = close_factor * total_debt
        max_debt_repay = debt_amount * config.liquidation_close_factor

        # Collateral needed = debt_repay * (1 + bonus) * price_ratio
        collateral_needed = (max_debt_repay * (1 + config.liquidation_bonus) *
                            debt_price / collateral_price)

        # Actual liquidation is minimum of what's allowed and what's available
        actual_collateral = min(collateral_needed, collateral_amount)
        actual_debt = actual_collateral * collateral_price / debt_price / (1 + config.liquidation_bonus)

        return actual_debt

    def _get_asset_price(self, asset: str) -> float:
        """Get asset price in USD (would use oracle)"""
        prices = {
            'ETH': 2000,
            'WETH': 2000,
            'WBTC': 40000,
            'USDC': 1,
            'USDT': 1,
            'DAI': 1,
            'COMP': 50,
            'AAVE': 80,
        }
        return prices.get(asset.upper(), 1)

    def analyze_contract_for_liquidation(self, bytecode: str,
                                          contract_address: str) -> Optional[HealthFactorConfig]:
        """Analyze contract bytecode for liquidation capability"""
        bytecode_lower = bytecode.lower()

        # Check for liquidation function
        liquidation_selectors = {
            '0x41013712': 'aave',
            '0xe78d0f49': 'generic',
            '0x0144894a': 'compound',
            '0x51945437': 'morpho',
        }

        detected_protocol = None
        for selector, proto in liquidation_selectors.items():
            if selector[2:] in bytecode_lower:
                detected_protocol = proto
                break

        if not detected_protocol:
            return None

        # Return matching config or create new one
        for config in self._protocol_configs.values():
            if detected_protocol in config.protocol_name.lower():
                return config

        # Create generic config
        return HealthFactorConfig(
            protocol_name=f"Unknown_{detected_protocol}",
            contract_address=contract_address,
            chain_id=1,
            health_factor_function='',
            liquidation_function=list(liquidation_selectors.keys())[0],
            liquidation_threshold=0.95,
            liquidation_bonus=0.05,
            collateral_factor=0.85,
            liquidation_close_factor=0.5,
            minimum_health_factor=0.98,
        )

    def get_all_protocols(self) -> List[HealthFactorConfig]:
        """Get all protocol configurations"""
        return list(self._protocol_configs.values())

    def get_liquidatable_protocols(self) -> List[HealthFactorConfig]:
        """Get protocols that support liquidations"""
        return [c for c in self._protocol_configs.values() if c.liquidation_function]

    def get_stats(self) -> Dict:
        """Get statistics"""
        return {
            'protocols_configured': self.protocols_configured,
            'positions_analyzed': self.positions_analyzed,
            'liquidatable_protocols': len(self.get_liquidatable_protocols()),
        }


# Pre-calculated liquidation thresholds for common assets
ASSET_LIQUIDATION_THRESHOLDS = {
    'ETH': {'ltv': 0.825, 'liquidation_threshold': 0.86, 'liquidation_bonus': 0.05},
    'WBTC': {'ltv': 0.70, 'liquidation_threshold': 0.75, 'liquidation_bonus': 0.05},
    'USDC': {'ltv': 0.87, 'liquidation_threshold': 0.90, 'liquidation_bonus': 0.05},
    'USDT': {'ltv': 0.85, 'liquidation_threshold': 0.88, 'liquidation_bonus': 0.05},
    'DAI': {'ltv': 0.77, 'liquidation_threshold': 0.80, 'liquidation_bonus': 0.05},
}
