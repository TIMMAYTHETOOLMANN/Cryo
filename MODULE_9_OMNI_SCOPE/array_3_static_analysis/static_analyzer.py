#!/usr/bin/env python3
"""
ARRAY 3 — Static Analysis Engine: Pre-Deployment MEV Discovery
=================================================================
Analyzes contract bytecode and source to predict MEV opportunities
BEFORE transactions occur.

Capabilities:
  1. Verified Contract Source Analysis — parse Solidity for MEV-vulnerable patterns
  2. Unverified Bytecode Decompilation — extract function signatures + flow patterns
  3. Lending Protocol Formula Extraction — pre-compute HF formulas for new protocols
  4. Transaction Ordering Sensitivity — detect functions where TX order matters (sandwich)

Data flow: Contract code → analyze → flag patterns → emit OpportunitySignal → DataBus
"""

import asyncio
import logging
import re
import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from web3 import Web3

from ..config import OmniScopeConfig, get_omni_config
from ..data_bus import DataBus, OpportunitySignal, SignalType, SignalSource

logger = logging.getLogger(__name__)


class VulnerabilityType(Enum):
    SANDWICH_VULNERABLE = "sandwich_vulnerable"
    FRONT_RUN_RISK = "front_run_risk"
    ORACLE_MANIPULABLE = "oracle_manipulable"
    FLASH_LOAN_ATTACK = "flash_loan_attack"
    REENTRANCY = "reentrancy"
    PRICE_MANIPULATION = "price_manipulation"
    GOVERNANCE_EXPLOIT = "governance_exploit"


@dataclass
class AnalysisResult:
    """Result of static analysis on a contract."""
    contract_address: str
    chain_id: int
    is_verified: bool
    source_language: str  # "solidity", "vyper", "bytecode_only"
    vulnerabilities: List[VulnerabilityType] = field(default_factory=list)
    mev_opportunities: List[str] = field(default_factory=list)
    lending_formulas: Dict[str, str] = field(default_factory=dict)
    function_count: int = 0
    has_price_oracle: bool = False
    has_liquidation: bool = False
    has_flash_loan: bool = False
    risk_score: float = 0.0  # 0=safe, 1=highly exploitable
    analysis_time_ms: float = 0.0


# Patterns that indicate MEV-vulnerable contract logic
SOLIDITY_PATTERNS = {
    # Sandwich-vulnerable: price changes within function execution
    "sandwich": [
        r"getAmountsOut\s*\(",
        r"swapExact\w+\s*\(",
        r"amountOutMin\s*[=<]",
        r"deadline\s*[=<]",
    ],
    # Front-run risk: state changes that benefit from ordering
    "front_run": [
        r"approve\s*\(\s*address",
        r"transfer\s*\(\s*address",
        r"mint\s*\(",
        r"claim\s*\(",
    ],
    # Oracle manipulation: reliance on spot prices
    "oracle_manipulation": [
        r"getReserves\s*\(",
        r"slot0\s*\(",
        r"latestAnswer\s*\(",
        r"getPrice\s*\(",
        r"price0CumulativeLast",
    ],
    # Lending protocol indicators
    "lending": [
        r"healthFactor",
        r"liquidat\w+\s*\(",
        r"collateral\w*Factor",
        r"borrowRate",
        r"getLTV\s*\(",
        r"getAccountLiquidity",
    ],
    # Flash loan callback patterns
    "flash_loan": [
        r"executeOperation\s*\(",
        r"flashLoan\s*\(",
        r"uniswapV\dFlashCallback",
        r"onFlashLoan\s*\(",
    ],
}

# Bytecode opcodes that indicate specific patterns
OPCODE_PATTERNS = {
    "delegatecall": "f4",    # DELEGATECALL opcode
    "selfdestruct": "ff",    # SELFDESTRUCT
    "create2": "f5",         # CREATE2 (deterministic deployment)
    "sload": "54",           # Storage read (state dependency)
    "sstore": "55",          # Storage write
    "timestamp": "42",       # TIMESTAMP (time manipulation risk)
    "caller": "33",          # CALLER
}


class StaticAnalyzer:
    """
    Analyzes contract code for MEV/exploit patterns.
    Works on both verified Solidity source and raw bytecode.
    """

    def __init__(self, bus: DataBus, config: Optional[OmniScopeConfig] = None):
        self.bus = bus
        self.config = config or get_omni_config()
        self._cfg = self.config.static_analysis
        self._running = False

        # Cache of analyzed contracts
        self._analyzed: Dict[str, AnalysisResult] = {}
        self._w3_cache: Dict[int, Web3] = {}

        self.stats = {
            "contracts_analyzed": 0,
            "vulnerabilities_found": 0,
            "lending_protocols_profiled": 0,
            "mev_opportunities_found": 0,
            "signals_emitted": 0,
        }

    async def start(self):
        """Start the analysis engine as a background service."""
        self._running = True
        logger.info("🔬 Array 3: Static Analysis Engine starting…")

        while self._running:
            # In production: consume new contracts from Array 2 via DataBus
            await asyncio.sleep(30)

    async def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Public API: Analyze a contract
    # ------------------------------------------------------------------

    def analyze_source(
        self, contract_address: str, chain_id: int, source_code: str
    ) -> AnalysisResult:
        """Analyze verified Solidity/Vyper source code."""
        start = time.time()
        key = f"{chain_id}:{contract_address}"

        if key in self._analyzed:
            return self._analyzed[key]

        result = AnalysisResult(
            contract_address=contract_address,
            chain_id=chain_id,
            is_verified=True,
            source_language="solidity" if "pragma solidity" in source_code else "vyper",
        )

        # Count functions
        result.function_count = len(re.findall(r"function\s+\w+", source_code))

        # Pattern matching
        vulns: Set[VulnerabilityType] = set()
        mev_opps: List[str] = []

        for category, patterns in SOLIDITY_PATTERNS.items():
            matches = sum(
                1 for p in patterns if re.search(p, source_code, re.IGNORECASE)
            )
            if matches >= 2:  # At least 2 pattern matches to flag
                if category == "sandwich":
                    vulns.add(VulnerabilityType.SANDWICH_VULNERABLE)
                    mev_opps.append("Sandwich attack on swap functions")
                elif category == "front_run":
                    vulns.add(VulnerabilityType.FRONT_RUN_RISK)
                    mev_opps.append("Front-running on state-changing functions")
                elif category == "oracle_manipulation":
                    vulns.add(VulnerabilityType.ORACLE_MANIPULABLE)
                    mev_opps.append("Oracle manipulation via spot price reliance")
                    result.has_price_oracle = True

            if category == "lending" and matches >= 2:
                result.has_liquidation = True
                mev_opps.append("Liquidation MEV on new lending protocol")
                self.stats["lending_protocols_profiled"] += 1

                # Extract health factor formula hints
                hf_match = re.search(
                    r"healthFactor\s*=\s*([^;]+);", source_code
                )
                if hf_match:
                    result.lending_formulas["health_factor"] = hf_match.group(1).strip()

                ltv_match = re.search(
                    r"(?:ltv|loanToValue|collateralFactor)\s*=\s*([^;]+);",
                    source_code, re.IGNORECASE,
                )
                if ltv_match:
                    result.lending_formulas["ltv"] = ltv_match.group(1).strip()

            if category == "flash_loan" and matches >= 1:
                result.has_flash_loan = True

        result.vulnerabilities = list(vulns)
        result.mev_opportunities = mev_opps
        result.risk_score = min(len(vulns) * 0.2, 1.0)

        elapsed = (time.time() - start) * 1000
        result.analysis_time_ms = elapsed

        self._analyzed[key] = result
        self.stats["contracts_analyzed"] += 1
        self.stats["vulnerabilities_found"] += len(vulns)
        self.stats["mev_opportunities_found"] += len(mev_opps)

        # Emit signals for high-value findings
        if result.has_liquidation:
            self.bus.publish(OpportunitySignal(
                signal_type=SignalType.NEW_PROTOCOL,
                source=SignalSource.STATIC_ANALYZER,
                chain_id=chain_id,
                confidence=0.7,
                estimated_profit_usd=300.0,
                gas_cost_estimate_usd=0,
                urgency_seconds=7200,
                target_contract=contract_address,
                competition_estimate=0.05,
                execution_complexity=0.8,
                metadata={
                    "formulas": result.lending_formulas,
                    "vulnerabilities": [v.value for v in vulns],
                },
            ))
            self.stats["signals_emitted"] += 1

        if VulnerabilityType.SANDWICH_VULNERABLE in vulns:
            self.bus.publish(OpportunitySignal(
                signal_type=SignalType.SANDWICH,
                source=SignalSource.STATIC_ANALYZER,
                chain_id=chain_id,
                confidence=0.5,
                estimated_profit_usd=50.0,
                gas_cost_estimate_usd=10.0,
                urgency_seconds=86400,
                target_contract=contract_address,
                competition_estimate=0.3,
                execution_complexity=0.5,
            ))
            self.stats["signals_emitted"] += 1

        return result

    def analyze_bytecode(
        self, contract_address: str, chain_id: int, bytecode: bytes
    ) -> AnalysisResult:
        """Analyze unverified contract bytecode."""
        start = time.time()
        key = f"{chain_id}:{contract_address}"

        result = AnalysisResult(
            contract_address=contract_address,
            chain_id=chain_id,
            is_verified=False,
            source_language="bytecode_only",
        )

        hex_code = bytecode.hex()

        # Opcode analysis
        vulns: Set[VulnerabilityType] = set()
        for name, opcode in OPCODE_PATTERNS.items():
            count = hex_code.count(opcode)
            if name == "delegatecall" and count > 0:
                vulns.add(VulnerabilityType.FRONT_RUN_RISK)
            if name == "selfdestruct" and count > 0:
                vulns.add(VulnerabilityType.FLASH_LOAN_ATTACK)

        # Check for known function selectors in bytecode
        lending_sigs = {"573ade81", "f5e3c462", "c5ebeaec"}  # liquidation selectors
        for sig in lending_sigs:
            if sig in hex_code:
                result.has_liquidation = True
                break

        result.vulnerabilities = list(vulns)
        result.risk_score = min(len(vulns) * 0.15, 1.0)
        result.analysis_time_ms = (time.time() - start) * 1000

        self._analyzed[key] = result
        self.stats["contracts_analyzed"] += 1

        return result

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "cache_size": len(self._analyzed),
        }
