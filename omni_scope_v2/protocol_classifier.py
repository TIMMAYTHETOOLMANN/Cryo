#!/usr/bin/env python3
"""
protocol_classifier — Protocol Classification & Liquidation Config Extractor
===============================================================================
Analyzes newly discovered contracts to classify them by type, extract
liquidation parameters, and assess risk — enabling day-one liquidation
readiness on any protocol before competition arrives.

Capabilities:
  • Function-signature fingerprinting to detect protocol type
  • Event-pattern matching (Borrow, Liquidate, Deposit, etc.)
  • Automatic liquidation-config extraction (health factor formula,
    close factor, bonus, thresholds)
  • Upgradeable-proxy detection (EIP-1967 / Transparent / UUPS)
  • Risk scoring based on code patterns and admin privileges
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from web3 import Web3

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────

class ProtocolType(str, Enum):
    LENDING = "lending"
    DEX_AMM = "dex_amm"
    DEX_ORDERBOOK = "dex_orderbook"
    YIELD_VAULT = "yield_vault"
    LIQUID_STAKING = "liquid_staking"
    DERIVATIVES = "derivatives"
    PERPETUALS = "perpetuals"
    OPTIONS = "options"
    BRIDGE = "bridge"
    STABLECOIN = "stablecoin"
    YIELD_AGGREGATOR = "yield_aggregator"
    INSURANCE = "insurance"
    ORACLE = "oracle"
    LAUNCHPAD = "launchpad"
    NFT_MARKETPLACE = "nft_marketplace"
    RESTAKING = "restaking"
    CDP = "cdp"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class ProtocolClassification:
    """Classification result for a protocol contract."""
    contract_address: str
    chain_id: int
    protocol_type: ProtocolType
    confidence: float = 0.0  # 0-1
    risk_level: RiskLevel = RiskLevel.MEDIUM
    detected_functions: List[str] = field(default_factory=list)
    detected_events: List[str] = field(default_factory=list)
    integrations: List[str] = field(default_factory=list)
    is_upgradeable: bool = False
    is_proxy: bool = False
    proxy_type: str = ""
    admin_address: str = ""
    tvl_estimate_usd: float = 0.0
    tags: List[str] = field(default_factory=list)
    classified_at: float = field(default_factory=time.time)


@dataclass
class LiquidationConfig:
    """Extracted liquidation configuration for a lending protocol."""
    protocol_address: str
    chain_id: int
    protocol_type: ProtocolType = ProtocolType.LENDING
    health_factor_function: str = ""
    liquidation_function: str = ""
    liquidation_threshold_bps: int = 8000  # 80%
    liquidation_bonus_bps: int = 500  # 5%
    collateral_factor_bps: int = 7500  # 75%
    close_factor_bps: int = 5000  # 50% max liquidation
    oracle_address: str = ""
    oracle_type: str = "chainlink"
    min_debt_usd: float = 0.0
    uses_health_factor: bool = True
    supports_flash_liquidation: bool = False
    custom_params: Dict[str, Any] = field(default_factory=dict)


# ── Signature Database ───────────────────────────────────────────

# Function selector → (function name, protocol type hint)
FUNCTION_SIGNATURES: Dict[str, Tuple[str, ProtocolType]] = {
    # ── Lending ──────────────────────────────────────────────
    "0x0c340a24": ("liquidationCall", ProtocolType.LENDING),
    "0xa415bcad": ("borrow", ProtocolType.LENDING),
    "0xe8eda9df": ("deposit", ProtocolType.LENDING),
    "0x69328dec": ("withdraw", ProtocolType.LENDING),
    "0x573ade81": ("repay", ProtocolType.LENDING),
    "0x7a708e92": ("flashLoan", ProtocolType.LENDING),
    "0xab9c4b5d": ("flashLoanSimple", ProtocolType.LENDING),
    "0x3b4da69f": ("getUserAccountData", ProtocolType.LENDING),
    "0xbf92857c": ("getAccountLiquidity", ProtocolType.LENDING),  # Compound
    "0x4e67d523": ("absorb", ProtocolType.LENDING),  # Comet
    "0x685b2e40": ("liquidate", ProtocolType.LENDING),  # Morpho
    "0x17bfdfbc": ("borrowBalanceCurrent", ProtocolType.LENDING),
    "0xbd6d894d": ("exchangeRateCurrent", ProtocolType.LENDING),

    # ── DEX / AMM ────────────────────────────────────────────
    "0x38ed1739": ("swapExactTokensForTokens", ProtocolType.DEX_AMM),
    "0x8803dbee": ("swapTokensForExactTokens", ProtocolType.DEX_AMM),
    "0x7ff36ab5": ("swapExactETHForTokens", ProtocolType.DEX_AMM),
    "0xc04b8d59": ("exactInput", ProtocolType.DEX_AMM),   # UniV3
    "0xdb3e2198": ("exactOutputSingle", ProtocolType.DEX_AMM),
    "0x022c0d9f": ("swap", ProtocolType.DEX_AMM),  # UniV2 Pair
    "0x128acb08": ("swap", ProtocolType.DEX_AMM),  # UniV3 Pool
    "0x3df02124": ("exchange", ProtocolType.DEX_AMM),  # Curve

    # ── Vaults ───────────────────────────────────────────────
    "0x6e553f65": ("deposit", ProtocolType.YIELD_VAULT),
    "0xb6b55f25": ("deposit_uint256", ProtocolType.YIELD_VAULT),
    "0xba087652": ("redeem", ProtocolType.YIELD_VAULT),
    "0x07a2d13a": ("pricePerShare", ProtocolType.YIELD_VAULT),

    # ── Liquid Staking ───────────────────────────────────────
    "0xa1903eab": ("submit", ProtocolType.LIQUID_STAKING),  # Lido
    "0xccc143b8": ("requestWithdrawals", ProtocolType.LIQUID_STAKING),

    # ── Perpetuals ───────────────────────────────────────────
    "0x6d9de508": ("createIncreasePosition", ProtocolType.PERPETUALS),
    "0x4870496f": ("openPosition", ProtocolType.PERPETUALS),

    # ── Bridges ──────────────────────────────────────────────
    "0x9fbf10fc": ("swap", ProtocolType.BRIDGE),  # Stargate
    "0x0f5287b0": ("sendToL2", ProtocolType.BRIDGE),

    # ── Stablecoins / CDP ────────────────────────────────────
    "0x06fdde03": ("name", ProtocolType.UNKNOWN),
    "0x40c10f19": ("mint", ProtocolType.STABLECOIN),
    "0x42966c68": ("burn", ProtocolType.STABLECOIN),

    # ── Restaking ────────────────────────────────────────────
    "0xf7c618c1": ("depositIntoStrategy", ProtocolType.RESTAKING),
    "0xd9caed12": ("queueWithdrawal", ProtocolType.RESTAKING),
}

# Event topic0 → (event name, protocol type hint)
EVENT_SIGNATURES: Dict[str, Tuple[str, ProtocolType]] = {
    "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286":
        ("Borrow", ProtocolType.LENDING),
    "0xc6a898309e823ee50bac64e45ca8adba6690e99e7841c45d754e2a38e9019d9b":
        ("Repay", ProtocolType.LENDING),
    "0xe6c1892f8d36012439015afa98d1e46ef50e27d4": ("LiquidationCall", ProtocolType.LENDING),
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822":
        ("Swap", ProtocolType.DEX_AMM),
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67":
        ("Swap_V3", ProtocolType.DEX_AMM),
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef":
        ("Transfer", ProtocolType.UNKNOWN),
}

# ── Proxy detection slots ────────────────────────────────────────
EIP1967_IMPL_SLOT = int("0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc", 16)
EIP1967_ADMIN_SLOT = int("0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103", 16)
EIP1967_BEACON_SLOT = int("0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50", 16)


# ── Protocol Classifier ─────────────────────────────────────────

class ProtocolClassifier:
    """
    Classify protocols by analysing function selectors, event signatures,
    proxy patterns, and code characteristics.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, ProtocolClassification] = {}
        self._liquidation_configs: Dict[str, LiquidationConfig] = {}
        self._classified = 0

    # ── Main Classification ───────────────────────────────────

    def classify_from_selectors(
        self,
        address: str,
        chain_id: int,
        selectors: List[str],
        event_topics: Optional[List[str]] = None,
    ) -> ProtocolClassification:
        """
        Classify a contract by its detected function selectors and event topics.
        """
        key = f"{chain_id}:{address.lower()}"
        if key in self._cache:
            return self._cache[key]

        type_votes: Dict[ProtocolType, float] = {}
        detected_fns: List[str] = []
        detected_events: List[str] = []

        # Score function selectors
        for sel in selectors:
            sel = sel.lower()
            match = FUNCTION_SIGNATURES.get(sel)
            if match:
                fn_name, ptype = match
                detected_fns.append(fn_name)
                if ptype != ProtocolType.UNKNOWN:
                    type_votes[ptype] = type_votes.get(ptype, 0) + 1.0

        # Score event topics
        for topic in (event_topics or []):
            topic = topic.lower()
            match = EVENT_SIGNATURES.get(topic)
            if match:
                ev_name, ptype = match
                detected_events.append(ev_name)
                if ptype != ProtocolType.UNKNOWN:
                    type_votes[ptype] = type_votes.get(ptype, 0) + 0.8

        # Determine winner
        if not type_votes:
            best_type = ProtocolType.UNKNOWN
            confidence = 0.0
        else:
            total = sum(type_votes.values())
            best_type = max(type_votes, key=type_votes.get)
            confidence = min(type_votes[best_type] / max(total, 1), 1.0)

        classification = ProtocolClassification(
            contract_address=address,
            chain_id=chain_id,
            protocol_type=best_type,
            confidence=round(confidence, 3),
            risk_level=self._assess_risk(selectors, detected_fns),
            detected_functions=detected_fns,
            detected_events=detected_events,
        )

        self._cache[key] = classification
        self._classified += 1
        return classification

    def classify_from_bytecode(
        self,
        address: str,
        chain_id: int,
        bytecode: str,
    ) -> ProtocolClassification:
        """
        Classify from raw bytecode by extracting function selectors.
        Works for unverified contracts.
        """
        selectors = self._extract_selectors_from_bytecode(bytecode)
        return self.classify_from_selectors(address, chain_id, selectors)

    # ── Liquidation Config Extraction ─────────────────────────

    def extract_liquidation_config(
        self,
        address: str,
        chain_id: int,
        classification: Optional[ProtocolClassification] = None,
        source_code: Optional[str] = None,
    ) -> Optional[LiquidationConfig]:
        """
        Extract liquidation configuration from a lending protocol.
        Uses known patterns for identified protocols and heuristics
        for new ones.
        """
        key = f"{chain_id}:{address.lower()}"

        if key in self._liquidation_configs:
            return self._liquidation_configs[key]

        cls = classification or self._cache.get(key)
        if not cls or cls.protocol_type != ProtocolType.LENDING:
            return None

        config = LiquidationConfig(
            protocol_address=address,
            chain_id=chain_id,
        )

        # Detect based on detected functions
        fns = set(cls.detected_functions)

        if "liquidationCall" in fns:
            # Aave-style
            config.health_factor_function = "getUserAccountData"
            config.liquidation_function = "liquidationCall"
            config.liquidation_threshold_bps = 8250
            config.liquidation_bonus_bps = 500
            config.close_factor_bps = 10000  # 100% for Aave v3
            config.uses_health_factor = True
            config.supports_flash_liquidation = True
            config.custom_params = {"protocol_style": "aave"}

        elif "absorb" in fns:
            # Compound v3 / Comet style
            config.health_factor_function = "isLiquidatable"
            config.liquidation_function = "absorb"
            config.liquidation_threshold_bps = 8000
            config.liquidation_bonus_bps = 800
            config.close_factor_bps = 10000
            config.uses_health_factor = False
            config.supports_flash_liquidation = False
            config.custom_params = {"protocol_style": "compound_v3"}

        elif "liquidate" in fns:
            # Morpho or generic
            config.health_factor_function = "position"
            config.liquidation_function = "liquidate"
            config.liquidation_threshold_bps = 8600
            config.liquidation_bonus_bps = 500
            config.close_factor_bps = 10000
            config.uses_health_factor = True
            config.supports_flash_liquidation = True
            config.custom_params = {"protocol_style": "morpho"}

        elif "getAccountLiquidity" in fns:
            # Compound v2 / Fork style
            config.health_factor_function = "getAccountLiquidity"
            config.liquidation_function = "liquidateBorrow"
            config.liquidation_threshold_bps = 7500
            config.liquidation_bonus_bps = 800
            config.close_factor_bps = 5000  # 50%
            config.uses_health_factor = False
            config.supports_flash_liquidation = False
            config.custom_params = {"protocol_style": "compound_v2"}

        else:
            # Unknown lending protocol — conservative defaults
            config.health_factor_function = "unknown"
            config.liquidation_function = "unknown"
            config.custom_params = {"protocol_style": "unknown"}

        # If source code provided, try to extract specific thresholds
        if source_code:
            self._parse_source_thresholds(config, source_code)

        self._liquidation_configs[key] = config
        return config

    def _parse_source_thresholds(
        self, config: LiquidationConfig, source: str,
    ) -> None:
        """Parse actual thresholds from Solidity source code."""
        import re

        # Liquidation threshold patterns
        threshold_patterns = [
            r"liquidationThreshold\s*=\s*(\d+)",
            r"LIQUIDATION_THRESHOLD\s*=\s*(\d+)",
            r"liqThreshold\s*=\s*(\d+)",
        ]
        for pat in threshold_patterns:
            m = re.search(pat, source)
            if m:
                val = int(m.group(1))
                if val > 100:
                    config.liquidation_threshold_bps = val
                else:
                    config.liquidation_threshold_bps = val * 100

        # Liquidation bonus patterns
        bonus_patterns = [
            r"liquidationBonus\s*=\s*(\d+)",
            r"LIQUIDATION_BONUS\s*=\s*(\d+)",
            r"liquidationIncentive\s*=\s*(\d+)",
        ]
        for pat in bonus_patterns:
            m = re.search(pat, source)
            if m:
                val = int(m.group(1))
                if val > 10000:
                    config.liquidation_bonus_bps = val - 10000
                elif val > 100:
                    config.liquidation_bonus_bps = val
                else:
                    config.liquidation_bonus_bps = val * 100

        # Close factor
        close_patterns = [
            r"closeFactor\s*=\s*(\d+)",
            r"CLOSE_FACTOR\s*=\s*(\d+)",
        ]
        for pat in close_patterns:
            m = re.search(pat, source)
            if m:
                val = int(m.group(1))
                if val <= 100:
                    config.close_factor_bps = val * 100
                else:
                    config.close_factor_bps = val

    # ── Proxy Detection ───────────────────────────────────────

    async def detect_proxy(
        self, w3: Web3, address: str, chain_id: int,
    ) -> Tuple[bool, str, str]:
        """
        Detect if a contract is a proxy and return:
        (is_proxy, proxy_type, implementation_address)
        """
        address = Web3.to_checksum_address(address)

        # EIP-1967 Implementation Slot
        try:
            impl_raw = w3.eth.get_storage_at(address, EIP1967_IMPL_SLOT)
            impl_addr = "0x" + impl_raw.hex()[-40:]
            if impl_addr != "0x" + "0" * 40:
                admin_raw = w3.eth.get_storage_at(address, EIP1967_ADMIN_SLOT)
                admin_addr = "0x" + admin_raw.hex()[-40:]
                return True, "EIP-1967 Transparent Proxy", impl_addr
        except Exception:
            pass

        # Check Beacon slot
        try:
            beacon_raw = w3.eth.get_storage_at(address, EIP1967_BEACON_SLOT)
            beacon_addr = "0x" + beacon_raw.hex()[-40:]
            if beacon_addr != "0x" + "0" * 40:
                return True, "Beacon Proxy", beacon_addr
        except Exception:
            pass

        # Simple code-size check for minimal proxies (EIP-1167)
        try:
            code = w3.eth.get_code(address).hex()
            if code.startswith("363d3d373d3d3d363d73"):
                impl = "0x" + code[20:60]
                return True, "EIP-1167 Minimal Proxy", impl
        except Exception:
            pass

        return False, "", ""

    # ── Risk Assessment ───────────────────────────────────────

    @staticmethod
    def _assess_risk(
        selectors: List[str], detected_fns: List[str],
    ) -> RiskLevel:
        """Assess risk based on detected patterns."""
        dangerous_patterns = {
            "selfdestruct", "delegatecall", "tx.origin",
            "suicide", "callcode",
        }
        risk_score = 0

        fn_set = set(fn.lower() for fn in detected_fns)

        # Check for dangerous patterns
        for pattern in dangerous_patterns:
            if pattern in fn_set:
                risk_score += 30

        # Upgradeable adds some risk
        if "upgradeTo" in detected_fns or "upgradeToAndCall" in detected_fns:
            risk_score += 10

        # Few detected functions = less understanding = more risk
        if len(detected_fns) < 3:
            risk_score += 15

        if risk_score >= 40:
            return RiskLevel.CRITICAL
        if risk_score >= 25:
            return RiskLevel.HIGH
        if risk_score >= 10:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    # ── Bytecode Helpers ──────────────────────────────────────

    @staticmethod
    def _extract_selectors_from_bytecode(bytecode: str) -> List[str]:
        """
        Extract 4-byte function selectors from raw bytecode.
        Looks for PUSH4 opcodes (0x63) followed by selector bytes.
        """
        if bytecode.startswith("0x"):
            bytecode = bytecode[2:]

        selectors: Set[str] = set()
        i = 0
        while i < len(bytecode) - 10:
            # PUSH4 = 0x63
            if bytecode[i:i + 2] == "63":
                sel = "0x" + bytecode[i + 2:i + 10]
                selectors.add(sel)
            i += 2

        return list(selectors)

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        type_counts: Dict[str, int] = {}
        for cls in self._cache.values():
            t = cls.protocol_type.value
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "classified": self._classified,
            "cached": len(self._cache),
            "liquidation_configs": len(self._liquidation_configs),
            "by_type": type_counts,
        }
