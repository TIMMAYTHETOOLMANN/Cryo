#!/usr/bin/env python3
"""
Protocol Classifier
Classify discovered protocols by type and functionality

Analyzes:
- Function signatures
- Event patterns
- Integration points
- Risk profile
"""

import asyncio
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from web3 import Web3


class ProtocolType(Enum):
    """Protocol classification types"""
    LENDING = "lending"
    DEX_AMM = "dex_amm"
    DEX_ORDERBOOK = "dex_orderbook"
    YIELD_VAULT = "yield_vault"
    LIQUID_STAKING = "liquid_staking"
    DERIVATIVES = "derivatives"
    OPTIONS = "options"
    PERPETUALS = "perpetuals"
    BRIDGE = "bridge"
    STABLECOIN = "stablecoin"
    YIELD_AGGREGATOR = "yield_aggregator"
    INSURANCE = "insurance"
    ORACLE = "oracle"
    LAUNCHPAD = "launchpad"
    NFT_MARKETPLACE = "nft_marketplace"
    UNKNOWN = "unknown"


class RiskLevel(Enum):
    """Protocol risk assessment"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ProtocolClassification:
    """Classification result for a protocol"""
    protocol_name: str
    contract_address: str
    chain_id: int
    protocol_type: ProtocolType
    confidence: float  # 0-1
    risk_level: RiskLevel
    detected_functions: List[str] = field(default_factory=list)
    detected_events: List[str] = field(default_factory=list)
    integrations: List[str] = field(default_factory=list)  # Oracles, other protocols
    tvl_estimate_usd: float = 0
    is_upgradeable: bool = False
    is_proxy: bool = False
    admin_address: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class LiquidationConfig:
    """Liquidation configuration for lending protocols"""
    protocol_type: ProtocolType
    health_factor_function: str
    liquidation_function: str
    liquidation_threshold: float  # e.g., 0.8 = 80%
    liquidation_bonus: float  # e.g., 0.05 = 5%
    collateral_factor: float
    liquidation_close_factor: float  # How much can be liquidated at once
    supported: bool = True


class ProtocolClassifier:
    """
    Classify protocols by type and extract relevant configuration
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Classification cache
        self._classifications: Dict[str, ProtocolClassification] = {}

        # Liquidation configurations for known protocols
        self._liquidation_configs: Dict[str, LiquidationConfig] = {}

        # Statistics
        self.protocols_classified = 0

        print("🏷️  Protocol Classifier initialized")

    async def start(self):
        """Start classifier"""
        print("\n🏷️  Starting Protocol Classifier...")
        self.is_running = True
        print("   ✅ Protocol Classifier started")

    async def stop(self):
        """Stop classifier"""
        self.is_running = False
        print("   🏷️  Protocol Classifier stopped")

    def classify_from_functions(self, contract_address: str, chain_id: int,
                                functions: List[str]) -> ProtocolClassification:
        """Classify protocol based on function signatures"""
        function_set = set(f.lower() for f in functions)

        # Scoring for each protocol type
        scores: Dict[ProtocolType, int] = {t: 0 for t in ProtocolType}

        # Lending protocol detection
        lending_functions = {
            '0xa0712d68',  # mint
            '0x69328dec',  # redeem
            '0xc5ebeccd',  # redeemUnderlying
            '0xe5a32283',  # borrowBalanceCurrent
            '0x70e90269',  # exchangeRateCurrent
            '0x41013712',  # liquidationCall
            '0xe78d0f49',  # liquidate
            '0x16ea91e7',  # flashLoan
        }
        scores[ProtocolType.LENDING] = len(function_set & lending_functions)

        # DEX/AMM detection
        dex_functions = {
            '0x022c0d9f',  # swap
            '0x38ed1739',  # swapExactTokensForTokens
            '0xe8e33700',  # addLiquidity
            '0x441a3e70',  # removeLiquidityETH
            '0x8803dbee',  # removeLiquidity
            '0xf305d719',  # addLiquidityETH
        }
        scores[ProtocolType.DEX_AMM] = len(function_set & dex_functions)

        # Yield vault detection
        vault_functions = {
            '0x602622d0',  # deposit(uint256)
            '0xb6b55f25',  # deposit(uint256,address)
            '0x853828b6',  # withdraw(uint256)
            '0x782d6fe1',  # withdraw(uint256,address)
            '0xa694f3a9',  # harvest
            '0x2f185e11',  # compound
        }
        scores[ProtocolType.YIELD_VAULT] = len(function_set & vault_functions)

        # Liquid staking detection
        liquid_staking_functions = {
            '0x7577432e',  # stake
            '0x6eb1769a',  # unstake
            '0x39563636',  # submit
            '0x8a6b3074',  # getStakeInfo
        }
        scores[ProtocolType.LIQUID_STAKING] = len(function_set & liquid_staking_functions)

        # Bridge detection
        bridge_functions = {
            '0x83bd6eb4',  # bridge
            '0x6e9960c3',  # swapAndBridge
            '0x6231029f',  # send
            '0x9a9826a6',  # relay
        }
        scores[ProtocolType.BRIDGE] = len(function_set & bridge_functions)

        # Find highest scoring type
        max_score = max(scores.values())
        if max_score == 0:
            protocol_type = ProtocolType.UNKNOWN
            confidence = 0.5
        else:
            protocol_type = [t for t, s in scores.items() if s == max_score][0]
            confidence = min(0.5 + (max_score * 0.1), 0.95)

        # Detect proxy/upgradeable
        is_proxy = self._detect_proxy(functions)
        is_upgradeable = self._detect_upgradeable(functions)

        # Create classification
        classification = ProtocolClassification(
            protocol_name="",  # Would be set by caller
            contract_address=contract_address,
            chain_id=chain_id,
            protocol_type=protocol_type,
            confidence=confidence,
            risk_level=self._assess_risk(protocol_type, is_upgradeable),
            detected_functions=list(function_set),
            is_upgradeable=is_upgradeable,
            is_proxy=is_proxy,
            tags=self._generate_tags(protocol_type, functions),
        )

        # Cache classification
        key = f"{chain_id}:{contract_address.lower()}"
        self._classifications[key] = classification
        self.protocols_classified += 1

        return classification

    def _detect_proxy(self, functions: List[str]) -> bool:
        """Detect if contract is a proxy"""
        proxy_selectors = {
            '0x3659cfe6',  # upgradeTo(address)
            '0x4f1ef286',  # upgradeToAndCall(address,bytes)
            '0x5c60da1b',  # implementation()
            '0x8da5cb5b',  # owner()
            '0x13af4035',  # changeAdmin(address)
        }
        return any(f in proxy_selectors for f in functions)

    def _detect_upgradeable(self, functions: List[str]) -> bool:
        """Detect if contract is upgradeable"""
        upgrade_selectors = {
            '0x3659cfe6',  # upgradeTo
            '0x4f1ef286',  # upgradeToAndCall
            '0x99a88ec4',  # initialize
            '0x8129fc1c',  # initialize
        }
        return any(f in upgrade_selectors for f in functions)

    def _assess_risk(self, protocol_type: ProtocolType, is_upgradeable: bool) -> RiskLevel:
        """Assess protocol risk level"""
        # Base risk by type
        type_risk = {
            ProtocolType.LENDING: RiskLevel.MEDIUM,
            ProtocolType.DEX_AMM: RiskLevel.LOW,
            ProtocolType.YIELD_VAULT: RiskLevel.MEDIUM,
            ProtocolType.LIQUID_STAKING: RiskLevel.MEDIUM,
            ProtocolType.DERIVATIVES: RiskLevel.HIGH,
            ProtocolType.OPTIONS: RiskLevel.HIGH,
            ProtocolType.PERPETUALS: RiskLevel.HIGH,
            ProtocolType.BRIDGE: RiskLevel.HIGH,
            ProtocolType.STABLECOIN: RiskLevel.MEDIUM,
            ProtocolType.YIELD_AGGREGATOR: RiskLevel.MEDIUM,
            ProtocolType.INSURANCE: RiskLevel.LOW,
            ProtocolType.ORACLE: RiskLevel.LOW,
            ProtocolType.LAUNCHPAD: RiskLevel.MEDIUM,
            ProtocolType.NFT_MARKETPLACE: RiskLevel.LOW,
            ProtocolType.UNKNOWN: RiskLevel.HIGH,
        }

        base_risk = type_risk.get(protocol_type, RiskLevel.HIGH)

        # Upgradeable = higher risk
        if is_upgradeable and base_risk in [RiskLevel.LOW, RiskLevel.MEDIUM]:
            return RiskLevel(base_risk.value if base_risk != RiskLevel.LOW else 'medium')

        return base_risk

    def _generate_tags(self, protocol_type: ProtocolType, functions: List[str]) -> List[str]:
        """Generate descriptive tags"""
        tags = []

        # Add type tag
        tags.append(protocol_type.value)

        # Add feature tags
        function_set = set(functions)

        if '0x16ea91e7' in function_set:  # flashLoan
            tags.append('flash_loans')

        if '0x41013712' in function_set:  # liquidationCall
            tags.append('liquidatable')

        if '0x3659cfe6' in function_set:  # upgradeTo
            tags.append('upgradeable')

        if '0x8da5cb5b' in function_set:  # owner
            tags.append('ownable')

        return tags

    def get_liquidation_config(self, protocol_type: ProtocolType) -> Optional[LiquidationConfig]:
        """Get liquidation configuration for protocol type"""
        configs = {
            ProtocolType.LENDING: LiquidationConfig(
                protocol_type=ProtocolType.LENDING,
                health_factor_function='0x0415f470',  # getUserAccountData
                liquidation_function='0x41013712',  # liquidationCall
                liquidation_threshold=0.8,
                liquidation_bonus=0.05,
                collateral_factor=0.85,
                liquidation_close_factor=0.5,
                supported=True,
            ),
            ProtocolType.PERPETUALS: LiquidationConfig(
                protocol_type=ProtocolType.PERPETUALS,
                health_factor_function='0x',  # Varies by protocol
                liquidation_function='0x',
                liquidation_threshold=0.9,
                liquidation_bonus=0.02,
                collateral_factor=0.9,
                liquidation_close_factor=1.0,
                supported=True,
            ),
        }

        return configs.get(protocol_type)

    def get_classification(self, contract_address: str, chain_id: int) -> Optional[ProtocolClassification]:
        """Get cached classification"""
        key = f"{chain_id}:{contract_address.lower()}"
        return self._classifications.get(key)

    def get_lending_protocols(self) -> List[ProtocolClassification]:
        """Get all classified lending protocols"""
        return [c for c in self._classifications.values() if c.protocol_type == ProtocolType.LENDING]

    def get_liquidatable_protocols(self) -> List[ProtocolClassification]:
        """Get protocols that support liquidations"""
        return [
            c for c in self._classifications.values()
            if 'liquidatable' in c.tags
        ]

    def get_stats(self) -> Dict:
        """Get statistics"""
        type_counts: Dict[str, int] = {}
        for classification in self._classifications.values():
            type_name = classification.protocol_type.value
            type_counts[type_name] = type_counts.get(type_name, 0) + 1

        return {
            'protocols_classified': self.protocols_classified,
            'by_type': type_counts,
            'lending_protocols': len(self.get_lending_protocols()),
            'liquidatable': len(self.get_liquidatable_protocols()),
        }


# Pre-defined function signatures for common protocols
KNOWN_PROTOCOL_FUNCTIONS = {
    'Aave V3': {
        '0x617ba037': 'supply(address,address,uint256,uint16,address)',
        '0x6daeb026': 'withdraw(address,uint256,address)',
        '0x573ade81': 'borrow(address,uint256,uint256,uint16,address)',
        '0x525e148f': 'repayBorrow(address,address,uint256)',
        '0x41013712': 'liquidationCall(address,address,address,uint256,bool)',
        '0x873078b5': 'flashLoanSimple(address,address,uint256,bytes,uint256)',
    },
    'Compound V3': {
        '0xe8eda9df': 'supply(address,uint256)',
        '0x3af9e669': 'supplyFromSource(address,uint256)',
        '0x3c9089d4': 'withdraw(address,uint256)',
        '0x15361d58': 'withdrawToSelf(address,uint256)',
        '0x0144894a': 'absorb(address,address)',  # Liquidation
        '0x1d092503': 'buyCollateral(address,uint256,uint256,address)',
    },
    'Uniswap V3': {
        '0x414bf389': 'exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))',
        '0x414bf389': 'exactOutputSingle((address,address,uint24,address,uint256,uint256,uint160))',
        '0x88316456': 'increaseLiquidity((uint256,int24,int24,uint256,uint256,uint256,uint256))',
        '0x219f5d17': 'decreaseLiquidity((uint256,int24,int24,uint128,uint256,uint256))',
        '0xfc6f7865': 'collect((uint256,address,uint128,uint128))',
    },
    'Curve': {
        '0x7577432e': 'add_liquidity(uint256[2],uint256)',
        '0xb6b55f25': 'remove_liquidity(uint256,uint256[2])',
        '0x022c0d9f': 'exchange(int128,int128,uint256,uint256)',
        '0xa4a78b06': 'exchange_underlying(int128,int128,uint256,uint256)',
    },
}
