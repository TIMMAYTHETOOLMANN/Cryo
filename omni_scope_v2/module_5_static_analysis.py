#!/usr/bin/env python3
"""
Module 5 — Static Analysis Engine (Pre-Deployment MEV Discovery)
===================================================================
Analyses contract code to predict if a protocol will create MEV
opportunities before it goes live.

Capabilities:
  5.1  Symbolic Execution for Vulnerability Extraction (Manticore-style heuristics)
  5.2  Unverified Contract Decompilation (Panoramix-style function sig recovery)
  5.3  Liquidation Inference from Source Code (extract HF formulas / thresholds)
  5.4  MEV Function Pattern Matching (swap, flash, liquidat, borrow)

Data flow:
  New contracts (from Etherscan / Module 4) → fetch code → analyze
  → extract vulnerability patterns → score MEV potential
  → emit TriangulatedSignal → SignalBus
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import aiohttp

from .config import StaticAnalysisConfig, get_config
from .data_lake import DataLake
from .signal_bus import SignalBus, SignalSource, SignalType, TriangulatedSignal

logger = logging.getLogger(__name__)


# ── Vulnerability Patterns ───────────────────────────────────────

@dataclass
class VulnerabilityPattern:
    """A known vulnerability or MEV-relevant code pattern."""
    name: str
    description: str
    severity: str               # "critical", "high", "medium", "low", "info"
    pattern_type: str           # "function_selector", "opcode", "source_keyword", "bytecode"
    pattern: str                # regex or hex pattern
    mev_relevance: float = 0.0  # 0-1 how likely to create MEV opportunities


KNOWN_PATTERNS: List[VulnerabilityPattern] = [
    VulnerabilityPattern(
        name="reentrancy_guard_missing",
        description="State change after external call without reentrancy guard",
        severity="critical", pattern_type="source_keyword",
        pattern=r"\.call\{.*\}.*\n.*=",
        mev_relevance=0.8,
    ),
    VulnerabilityPattern(
        name="price_oracle_manipulation",
        description="Uses spot price from single DEX without TWAP",
        severity="high", pattern_type="source_keyword",
        pattern=r"getReserves|slot0|getAmountsOut",
        mev_relevance=0.9,
    ),
    VulnerabilityPattern(
        name="flash_loan_callback",
        description="Implements flash loan callback — potential arbitrage vector",
        severity="info", pattern_type="source_keyword",
        pattern=r"executeOperation|onFlashLoan|flashLoanSimple",
        mev_relevance=0.7,
    ),
    VulnerabilityPattern(
        name="liquidation_function",
        description="Contains liquidation logic — extraction opportunity",
        severity="info", pattern_type="source_keyword",
        pattern=r"liquidat|healthFactor|collateralFactor|isLiquidatable",
        mev_relevance=0.95,
    ),
    VulnerabilityPattern(
        name="sandwich_vulnerable_swap",
        description="Swap function without deadline or slippage protection",
        severity="high", pattern_type="source_keyword",
        pattern=r"swap.*\{[^}]*(?!deadline|slippage)",
        mev_relevance=0.85,
    ),
    VulnerabilityPattern(
        name="unprotected_mint_burn",
        description="Mint/burn without access control — share price manipulation",
        severity="critical", pattern_type="source_keyword",
        pattern=r"(function\s+mint|function\s+burn)(?!.*onlyOwner|require\(msg\.sender)",
        mev_relevance=0.9,
    ),
    VulnerabilityPattern(
        name="selfdestruct",
        description="Contract contains selfdestruct — potential rug vector",
        severity="critical", pattern_type="source_keyword",
        pattern=r"selfdestruct|suicide",
        mev_relevance=0.3,
    ),
    VulnerabilityPattern(
        name="delegatecall_proxy",
        description="Uses delegatecall — logic can be changed",
        severity="medium", pattern_type="source_keyword",
        pattern=r"delegatecall",
        mev_relevance=0.4,
    ),
    VulnerabilityPattern(
        name="tx_origin_auth",
        description="Uses tx.origin for authentication — phishing vector",
        severity="high", pattern_type="source_keyword",
        pattern=r"tx\.origin",
        mev_relevance=0.2,
    ),
    VulnerabilityPattern(
        name="unchecked_return",
        description="External call without return value check",
        severity="medium", pattern_type="source_keyword",
        pattern=r"\.call\{[^}]*\}\([^)]*\)\s*;",
        mev_relevance=0.5,
    ),
]

# Function selectors that indicate MEV-relevant operations
MEV_SELECTORS: Dict[str, str] = {
    "a9059cbb": "transfer(address,uint256)",
    "23b872dd": "transferFrom(address,address,uint256)",
    "095ea7b3": "approve(address,uint256)",
    "38ed1739": "swapExactTokensForTokens",
    "8803dbee": "swapTokensForExactTokens",
    "18cbafe5": "swapExactTokensForETH",
    "c04b8d59": "exactInput (V3)",
    "414bf389": "exactInputSingle (V3)",
    "e8eda9df": "liquidationCall",
    "ab9c4b5d": "flashLoan",
    "5cffe9de": "flashLoan (ERC-3156)",
}


# ── Data Models ──────────────────────────────────────────────────

@dataclass
class ContractAnalysis:
    """Result of static analysis on a contract."""
    address: str
    chain_id: int
    is_verified: bool
    source_code: str = ""
    bytecode: str = ""
    compiler_version: str = ""

    # Analysis results
    vulnerabilities: List[Dict[str, Any]] = field(default_factory=list)
    mev_score: float = 0.0          # 0-1 overall MEV opportunity score
    has_liquidation_logic: bool = False
    has_flash_loan_support: bool = False
    has_swap_function: bool = False
    has_oracle_dependency: bool = False
    extracted_selectors: List[str] = field(default_factory=list)
    extracted_thresholds: Dict[str, float] = field(default_factory=dict)

    # Timing
    analysed_at: float = field(default_factory=time.time)


@dataclass
class LiquidationInference:
    """Inferred liquidation parameters from source code."""
    protocol_address: str
    chain_id: int
    health_factor_formula: str = ""
    liquidation_threshold: float = 0.0
    liquidation_bonus: float = 0.0
    collateral_factors: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


# ── Module ───────────────────────────────────────────────────────

class StaticAnalysisEngine:
    """
    Module 5: Static Analysis Engine.

    Analyses newly deployed contracts for vulnerability patterns,
    MEV opportunities, and liquidation parameters. Enables day-one
    readiness for new lending protocols.
    """

    def __init__(
        self,
        bus: SignalBus,
        lake: DataLake,
        config: Optional[StaticAnalysisConfig] = None,
    ):
        self.bus = bus
        self.lake = lake
        self._cfg = config or get_config().static_analysis
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None

        # Analysis cache
        self._analysed: Dict[str, ContractAnalysis] = {}
        self._pending_queue: List[Dict[str, Any]] = []

        # Stats
        self._stats = {
            "contracts_analysed": 0,
            "verified_analysed": 0,
            "unverified_decompiled": 0,
            "vulnerabilities_found": 0,
            "mev_patterns_found": 0,
            "liquidation_inferences": 0,
            "signals_emitted": 0,
        }

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("[StaticAnalysis] Starting — %d vulnerability patterns loaded",
                     len(KNOWN_PATTERNS))

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[StaticAnalysis] Stopped")

    async def run_cycle(self):
        """Process pending contracts for analysis."""
        if not self._running or not self._cfg.enabled:
            return

        # Consume new contract signals from the bus
        recent = self.bus.get_recent(50)
        for sig in recent:
            if sig.signal_type == SignalType.NEW_PROTOCOL and sig.target_contract:
                key = f"{sig.chain_id}:{sig.target_contract}"
                if key not in self._analysed:
                    self._pending_queue.append({
                        "address": sig.target_contract,
                        "chain_id": sig.chain_id,
                    })

        # Analyse pending contracts (max 5 per cycle)
        for item in self._pending_queue[:5]:
            analysis = await self._analyse_contract(
                item["address"], item["chain_id"],
            )
            if analysis:
                key = f"{analysis.chain_id}:{analysis.address}"
                self._analysed[key] = analysis
                self._emit_analysis_signals(analysis)

        self._pending_queue = self._pending_queue[5:]

    # ── Contract Analysis ────────────────────────────────────

    async def _analyse_contract(self, address: str, chain_id: int) -> Optional[ContractAnalysis]:
        """Full static analysis of a contract."""
        analysis = ContractAnalysis(address=address, chain_id=chain_id, is_verified=False)

        # 1. Fetch source code (if verified)
        source = await self._fetch_source_code(address, chain_id)
        if source:
            analysis.is_verified = True
            analysis.source_code = source
            self._stats["verified_analysed"] += 1
        else:
            # 2. Fetch bytecode and decompile
            bytecode = await self._fetch_bytecode(address, chain_id)
            if bytecode:
                analysis.bytecode = bytecode
                if self._cfg.decompile_unverified:
                    analysis.source_code = self._decompile_bytecode(bytecode)
                    self._stats["unverified_decompiled"] += 1

        if not analysis.source_code and not analysis.bytecode:
            return None

        # 3. Run vulnerability pattern matching
        if analysis.source_code:
            analysis.vulnerabilities = self._scan_source_patterns(analysis.source_code)
            analysis.extracted_selectors = self._extract_function_selectors(analysis.source_code)
            analysis.extracted_thresholds = self._extract_numeric_thresholds(analysis.source_code)

        # 4. Bytecode analysis for function selectors
        if analysis.bytecode:
            bytecode_selectors = self._extract_bytecode_selectors(analysis.bytecode)
            analysis.extracted_selectors.extend(bytecode_selectors)

        # 5. Compute MEV score
        analysis.mev_score = self._compute_mev_score(analysis)

        # 6. Infer liquidation parameters
        if analysis.has_liquidation_logic:
            inference = self._infer_liquidation_params(analysis)
            if inference:
                self._stats["liquidation_inferences"] += 1

        self._stats["contracts_analysed"] += 1
        return analysis

    def _scan_source_patterns(self, source: str) -> List[Dict[str, Any]]:
        """Scan source code for known vulnerability patterns."""
        findings: List[Dict[str, Any]] = []

        for pattern in KNOWN_PATTERNS:
            if pattern.pattern_type == "source_keyword":
                matches = re.findall(pattern.pattern, source, re.IGNORECASE | re.MULTILINE)
                if matches:
                    findings.append({
                        "name": pattern.name,
                        "severity": pattern.severity,
                        "description": pattern.description,
                        "mev_relevance": pattern.mev_relevance,
                        "match_count": len(matches),
                    })
                    self._stats["vulnerabilities_found"] += 1

                    if pattern.mev_relevance > 0.6:
                        self._stats["mev_patterns_found"] += 1

        return findings

    def _extract_function_selectors(self, source: str) -> List[str]:
        """Extract function signatures from Solidity source code."""
        # Match function declarations
        pattern = r'function\s+(\w+)\s*\(([^)]*)\)'
        matches = re.findall(pattern, source)
        selectors = []
        for name, params in matches:
            sig = f"{name}({self._normalise_params(params)})"
            selector = hashlib.sha3_256(sig.encode()).hexdigest()[:8]
            selectors.append(selector)
        return selectors

    def _extract_bytecode_selectors(self, bytecode: str) -> List[str]:
        """Extract 4-byte function selectors from bytecode."""
        if not bytecode.startswith("0x"):
            bytecode = "0x" + bytecode

        selectors = []
        # Look for PUSH4 opcodes (0x63) followed by 4 bytes
        hex_code = bytecode[2:]
        i = 0
        while i < len(hex_code) - 10:
            if hex_code[i:i + 2] == "63":
                sel = hex_code[i + 2:i + 10]
                if sel in MEV_SELECTORS:
                    selectors.append(sel)
            i += 2
        return selectors

    def _extract_numeric_thresholds(self, source: str) -> Dict[str, float]:
        """Extract numeric constants that look like liquidation thresholds."""
        thresholds: Dict[str, float] = {}

        # Look for common threshold variable names
        patterns = {
            "liquidation_threshold": r'liquidationThreshold\s*=\s*(\d+)',
            "collateral_factor": r'collateralFactor[A-Za-z]*\s*=\s*(\d+)',
            "health_factor_min": r'(?:MIN_HEALTH|minHealth)\w*\s*=\s*(\d+)',
            "liquidation_bonus": r'liquidation(?:Bonus|Incentive)\s*=\s*(\d+)',
            "close_factor": r'closeFactor\s*=\s*(\d+)',
        }

        for name, pat in patterns.items():
            match = re.search(pat, source, re.IGNORECASE)
            if match:
                val = int(match.group(1))
                # Normalize: if value > 100, assume basis points or 1e18 scaled
                if val > 10000:
                    thresholds[name] = val / 1e18
                elif val > 100:
                    thresholds[name] = val / 10000.0
                else:
                    thresholds[name] = val / 100.0

        return thresholds

    def _compute_mev_score(self, analysis: ContractAnalysis) -> float:
        """Compute an overall MEV opportunity score (0-1)."""
        score = 0.0

        # Vulnerability-based scoring
        for vuln in analysis.vulnerabilities:
            score += vuln.get("mev_relevance", 0) * 0.15

        # Function selector scoring
        for sel in analysis.extracted_selectors:
            if sel in MEV_SELECTORS:
                score += 0.1

        # Feature detection
        source_lower = analysis.source_code.lower()
        if "liquidat" in source_lower:
            analysis.has_liquidation_logic = True
            score += 0.2
        if "flashloan" in source_lower or "flash" in source_lower:
            analysis.has_flash_loan_support = True
            score += 0.1
        if "swap" in source_lower:
            analysis.has_swap_function = True
            score += 0.1
        if "oracle" in source_lower or "price" in source_lower:
            analysis.has_oracle_dependency = True
            score += 0.1

        return min(1.0, score)

    def _decompile_bytecode(self, bytecode: str) -> str:
        """
        Heuristic decompilation of unverified contract bytecode.
        Extracts function signatures and basic structure.
        Production: use Panoramix or Heimdall decompiler.
        """
        selectors = self._extract_bytecode_selectors(bytecode)
        lines = ["// Decompiled from bytecode (heuristic)", ""]

        for sel in selectors:
            sig = MEV_SELECTORS.get(sel, f"unknown_{sel}")
            lines.append(f"// Function selector 0x{sel} -> {sig}")

        if not selectors:
            lines.append("// No known function selectors found")

        return "\n".join(lines)

    def _infer_liquidation_params(self, analysis: ContractAnalysis) -> Optional[LiquidationInference]:
        """Infer liquidation parameters from extracted thresholds."""
        thresholds = analysis.extracted_thresholds
        if not thresholds:
            return None

        inference = LiquidationInference(
            protocol_address=analysis.address,
            chain_id=analysis.chain_id,
            liquidation_threshold=thresholds.get("liquidation_threshold", 0),
            liquidation_bonus=thresholds.get("liquidation_bonus", 0),
            collateral_factors={"default": thresholds.get("collateral_factor", 0)},
            confidence=min(0.9, 0.3 + len(thresholds) * 0.15),
        )
        return inference

    @staticmethod
    def _normalise_params(params: str) -> str:
        """Normalise Solidity parameter list to canonical form."""
        if not params.strip():
            return ""
        parts = []
        for p in params.split(","):
            p = p.strip()
            tokens = p.split()
            if tokens:
                parts.append(tokens[0])
        return ",".join(parts)

    # ── Signal Emission ──────────────────────────────────────

    def _emit_analysis_signals(self, analysis: ContractAnalysis):
        """Emit signals based on analysis results."""
        if analysis.mev_score < 0.3:
            return

        # MEV pattern signal
        if analysis.mev_score >= 0.5:
            signal = TriangulatedSignal(
                signal_type=SignalType.MEV_PATTERN,
                source=SignalSource.STATIC_ANALYSIS,
                chain_id=analysis.chain_id,
                confidence=analysis.mev_score,
                estimated_profit_usd=0.0,
                gas_cost_estimate_usd=0.0,
                urgency_seconds=3600.0,
                target_contract=analysis.address,
                competition_estimate=0.1,
                execution_complexity=0.7,
                metadata={
                    "mev_score": analysis.mev_score,
                    "vulnerabilities": len(analysis.vulnerabilities),
                    "has_liquidation": analysis.has_liquidation_logic,
                    "has_flash_loan": analysis.has_flash_loan_support,
                    "has_swap": analysis.has_swap_function,
                    "has_oracle": analysis.has_oracle_dependency,
                    "thresholds": analysis.extracted_thresholds,
                },
            )
            self.bus.publish(signal)
            self._stats["signals_emitted"] += 1

        # New protocol with liquidation = high priority
        if analysis.has_liquidation_logic:
            signal = TriangulatedSignal(
                signal_type=SignalType.NEW_PROTOCOL,
                source=SignalSource.STATIC_ANALYSIS,
                chain_id=analysis.chain_id,
                confidence=0.7,
                estimated_profit_usd=0.0,
                gas_cost_estimate_usd=0.0,
                urgency_seconds=600.0,
                target_contract=analysis.address,
                competition_estimate=0.05,
                metadata={
                    "reason": "new_lending_protocol_detected",
                    "thresholds": analysis.extracted_thresholds,
                    "mev_score": analysis.mev_score,
                },
            )
            self.bus.publish(signal)
            self._stats["signals_emitted"] += 1

    # ── API ──────────────────────────────────────────────────

    async def _fetch_source_code(self, address: str, chain_id: int) -> Optional[str]:
        """Fetch verified source code from Etherscan-like API."""
        if not self._session:
            return None

        api_urls = {
            1: "https://api.etherscan.io/api",
            42161: "https://api.arbiscan.io/api",
            10: "https://api-optimistic.etherscan.io/api",
            137: "https://api.polygonscan.com/api",
            8453: "https://api.basescan.org/api",
        }
        url = api_urls.get(chain_id)
        if not url:
            return None

        try:
            params = {
                "module": "contract",
                "action": "getsourcecode",
                "address": address,
                "apikey": self._cfg.etherscan_api_key or "",
            }
            async with self._session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("result", [])
                    if results and isinstance(results, list):
                        source = results[0].get("SourceCode", "")
                        if source and source != "0x":
                            return source
        except Exception:
            pass
        return None

    async def _fetch_bytecode(self, address: str, chain_id: int) -> Optional[str]:
        """Fetch contract bytecode via Etherscan API."""
        if not self._session:
            return None

        api_urls = {
            1: "https://api.etherscan.io/api",
            42161: "https://api.arbiscan.io/api",
            10: "https://api-optimistic.etherscan.io/api",
            137: "https://api.polygonscan.com/api",
            8453: "https://api.basescan.org/api",
        }
        url = api_urls.get(chain_id)
        if not url:
            return None

        try:
            params = {
                "module": "proxy",
                "action": "eth_getCode",
                "address": address,
                "tag": "latest",
                "apikey": self._cfg.etherscan_api_key or "",
            }
            async with self._session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    code = data.get("result", "")
                    if code and code != "0x":
                        return code
        except Exception:
            pass
        return None

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "cache_size": len(self._analysed),
            "pending_queue": len(self._pending_queue),
        }
