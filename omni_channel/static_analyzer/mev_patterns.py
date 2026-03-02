#!/usr/bin/env python3
"""
MEV Pattern Matcher
Detect MEV-vulnerable patterns in contract code

Patterns detected:
- Sandwich vulnerabilities
- Front-running opportunities
- Back-running setups
- Order-dependent logic
- State manipulation vectors
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum


class MEVType(Enum):
    """Types of MEV opportunities"""
    SANDWICH = "sandwich"
    FRONT_RUN = "front_run"
    BACK_RUN = "back_run"
    ARBITRAGE = "arbitrage"
    LIQUIDATION = "liquidation"
    ORACLE_MANIPULATION = "oracle_manipulation"
    STATE_MANIPULATION = "state_manipulation"
    ORDER_DEPENDENCE = "order_dependence"


@dataclass
class MEVPattern:
    """Detected MEV pattern"""
    pattern_id: str
    mev_type: MEVType
    contract_address: str
    function_selector: str
    function_name: str
    confidence: float  # 0-1
    severity: str  # low, medium, high, critical
    description: str
    exploitation_vector: str
    mitigation_suggestions: List[str] = field(default_factory=list)
    gas_estimate: int = 0
    min_profit_usd: float = 0
    code_location: Dict = field(default_factory=dict)


@dataclass
class PatternMatch:
    """Result of pattern matching"""
    pattern: MEVPattern
    matched_at: int
    bytecode_offset: int
    context: str


class MEVPatternMatcher:
    """
    Detect MEV-vulnerable patterns in contract bytecode
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Pattern database
        self._patterns: Dict[MEVType, List[Dict]] = self._init_patterns()

        # Statistics
        self.patterns_matched = 0
        self.contracts_analyzed = 0

        print("🎯 MEV Pattern Matcher initialized")

    async def start(self):
        """Start pattern matcher"""
        print("\n🎯 Starting MEV Pattern Matcher...")
        self.is_running = True
        print("   ✅ MEV Pattern Matcher started")

    async def stop(self):
        """Stop matcher"""
        self.is_running = False
        print("   🎯 MEV Pattern Matcher stopped")

    def _init_patterns(self) -> Dict[MEVType, List[Dict]]:
        """Initialize MEV pattern database"""
        return {
            MEVType.SANDWICH: [
                {
                    'name': 'Uniswap V2 Swap',
                    'selectors': ['0x38ed1739', '0x7ff36ab5', '0xfb3bdb41'],
                    'description': 'Standard Uniswap V2 swap - sandwich vulnerable',
                    'severity': 'medium',
                    'min_profit_threshold': 100,  # USD
                },
                {
                    'name': 'Uniswap V3 Swap',
                    'selectors': ['0x414bf389', '0xac9650d8'],
                    'description': 'Uniswap V3 exact input/output - sandwich vulnerable',
                    'severity': 'medium',
                    'min_profit_threshold': 50,
                },
            ],
            MEVType.FRONT_RUN: [
                {
                    'name': 'Large Swap Detection',
                    'selectors': ['0x38ed1739', '0x7ff36ab5'],
                    'description': 'Large swap that will move price',
                    'severity': 'high',
                    'min_profit_threshold': 500,
                },
                {
                    'name': 'Oracle Update',
                    'selectors': ['0x515f074f', '0x80e634d8'],
                    'description': 'Oracle price update - can front-run dependent liquidations',
                    'severity': 'critical',
                    'min_profit_threshold': 1000,
                },
            ],
            MEVType.BACK_RUN: [
                {
                    'name': 'Flash Loan',
                    'selectors': ['0x16ea91e7', '0x4303a8a7', '0x6d77a6a6'],
                    'description': 'Flash loan - can back-run with arbitrage',
                    'severity': 'medium',
                    'min_profit_threshold': 200,
                },
                {
                    'name': 'Large Liquidity Addition',
                    'selectors': ['0xe8e33700', '0xf305d719'],
                    'description': 'Large liquidity addition - price impact opportunity',
                    'severity': 'low',
                    'min_profit_threshold': 50,
                },
            ],
            MEVType.LIQUIDATION: [
                {
                    'name': 'Aave Liquidation',
                    'selectors': ['0x41013712'],
                    'description': 'Aave liquidation call',
                    'severity': 'high',
                    'min_profit_threshold': 500,
                },
                {
                    'name': 'Compound Liquidation',
                    'selectors': ['0x0144894a'],
                    'description': 'Compound absorb/liquidation',
                    'severity': 'high',
                    'min_profit_threshold': 500,
                },
            ],
            MEVType.ORDER_DEPENDENCE: [
                {
                    'name': 'State Price Update',
                    'selectors': ['0x602622d0', '0x853828b6'],
                    'description': 'Function that updates state price - order matters',
                    'severity': 'high',
                    'min_profit_threshold': 300,
                },
            ],
        }

    def analyze_bytecode(self, bytecode: str, contract_address: str = "") -> List[MEVPattern]:
        """Analyze bytecode for MEV patterns"""
        patterns_found = []
        bytecode_lower = bytecode.lower()
        self.contracts_analyzed += 1

        for mev_type, pattern_list in self._patterns.items():
            for pattern in pattern_list:
                for selector in pattern.get('selectors', []):
                    if selector[2:] in bytecode_lower:
                        # Pattern matched
                        mev_pattern = MEVPattern(
                            pattern_id=f"{mev_type.value}_{pattern['name']}_{int(time.time())}",
                            mev_type=mev_type,
                            contract_address=contract_address,
                            function_selector=selector,
                            function_name=pattern['name'],
                            confidence=self._calculate_confidence(mev_type, bytecode_lower, selector),
                            severity=pattern.get('severity', 'medium'),
                            description=pattern['description'],
                            exploitation_vector=self._get_exploitation_vector(mev_type),
                            mitigation_suggestions=self._get_mitigations(mev_type),
                            gas_estimate=self._estimate_gas(mev_type),
                            min_profit_usd=pattern.get('min_profit_threshold', 0),
                            code_location={
                                'selector': selector,
                                'offset': bytecode_lower.find(selector[2:])
                            }
                        )

                        patterns_found.append(mev_pattern)
                        self.patterns_matched += 1

        return patterns_found

    def analyze_functions(self, functions: List[Dict]) -> List[MEVPattern]:
        """Analyze list of function signatures for MEV patterns"""
        patterns_found = []

        for func in functions:
            selector = func.get('selector', '')
            name = func.get('name', '')

            for mev_type, pattern_list in self._patterns.items():
                for pattern in pattern_list:
                    if selector in pattern.get('selectors', []):
                        mev_pattern = MEVPattern(
                            pattern_id=f"{mev_type.value}_{name}_{int(time.time())}",
                            mev_type=mev_type,
                            contract_address=func.get('contract_address', ''),
                            function_selector=selector,
                            function_name=name,
                            confidence=0.9,
                            severity=pattern.get('severity', 'medium'),
                            description=pattern['description'],
                            exploitation_vector=self._get_exploitation_vector(mev_type),
                            mitigation_suggestions=self._get_mitigations(mev_type),
                        )
                        patterns_found.append(mev_pattern)

        return patterns_found

    def _calculate_confidence(self, mev_type: MEVType, bytecode: str, selector: str) -> float:
        """Calculate confidence score for pattern match"""
        confidence = 0.5  # Base confidence

        # More selectors found = higher confidence
        pattern_selectors = []
        for pattern in self._patterns[mev_type]:
            pattern_selectors.extend(pattern.get('selectors', []))

        found_count = sum(1 for s in pattern_selectors if s[2:] in bytecode)
        confidence += min(found_count * 0.1, 0.3)

        # Check for supporting patterns
        if mev_type == MEVType.SANDWICH:
            # Check for swap router patterns
            if 'e592427a' in bytecode:  # Uniswap V3 router
                confidence += 0.1

        elif mev_type == MEVType.LIQUIDATION:
            # Check for lending protocol patterns
            if '617ba037' in bytecode:  # Aave supply
                confidence += 0.15

        return min(confidence, 1.0)

    def _get_exploitation_vector(self, mev_type: MEVType) -> str:
        """Get description of how to exploit this MEV type"""
        vectors = {
            MEVType.SANDWICH: "Place buy order before victim, sell after victim buys",
            MEVType.FRONT_RUN: "Execute transaction with higher gas before victim",
            MEVType.BACK_RUN: "Execute transaction after victim to capture value",
            MEVType.LIQUIDATION: "Liquidate undercollateralized position for bonus",
            MEVType.ORDER_DEPENDENCE: "Race to execute before state changes",
            MEVType.ORACLE_MANIPULATION: "Manipulate oracle price before dependent transactions",
            MEVType.ARBITRAGE: "Buy low on one DEX, sell high on another",
            MEVType.STATE_MANIPULATION: "Manipulate contract state for profit",
        }
        return vectors.get(mev_type, "Unknown exploitation vector")

    def _get_mitigations(self, mev_type: MEVType) -> List[str]:
        """Get mitigation suggestions for MEV type"""
        mitigations = {
            MEVType.SANDWICH: [
                "Use private RPC (Flashbots)",
                "Set slippage tolerance",
                "Use TWAP oracles",
                "Split large orders"
            ],
            MEVType.FRONT_RUN: [
                "Use commit-reveal scheme",
                "Implement transaction delays",
                "Use private mempool"
            ],
            MEVType.LIQUIDATION: [
                "Maintain healthy collateralization",
                "Monitor position closely",
                "Set up liquidation alerts"
            ],
            MEVType.ORDER_DEPENDENCE: [
                "Use batch auctions",
                "Implement fair ordering",
                "Use commit-reveal"
            ],
        }
        return mitigations.get(mev_type, ["Review contract security"])

    def _estimate_gas(self, mev_type: MEVType) -> int:
        """Estimate gas cost for MEV execution"""
        estimates = {
            MEVType.SANDWICH: 400000,  # Two swaps
            MEVType.FRONT_RUN: 200000,  # Single transaction
            MEVType.BACK_RUN: 200000,
            MEVType.LIQUIDATION: 300000,  # Liquidation call
            MEVType.ARBITRAGE: 500000,  # Multiple swaps
            MEVType.ORDER_DEPENDENCE: 150000,
        }
        return estimates.get(mev_type, 200000)

    def get_sandwich_opportunities(self, patterns: List[MEVPattern]) -> List[MEVPattern]:
        """Filter for sandwich opportunities"""
        return [p for p in patterns if p.mev_type == MEVType.SANDWICH]

    def get_liquidation_opportunities(self, patterns: List[MEVPattern]) -> List[MEVPattern]:
        """Filter for liquidation opportunities"""
        return [p for p in patterns if p.mev_type == MEVType.LIQUIDATION]

    def get_high_confidence(self, patterns: List[MEVPattern], min_confidence: float = 0.7) -> List[MEVPattern]:
        """Filter for high confidence patterns"""
        return [p for p in patterns if p.confidence >= min_confidence]

    def get_stats(self) -> Dict:
        """Get statistics"""
        return {
            'patterns_matched': self.patterns_matched,
            'contracts_analyzed': self.contracts_analyzed,
            'pattern_types': len(self._patterns),
        }


# Function selectors organized by MEV category
MEV_SELECTORS = {
    'sandwich': [
        '0x38ed1739',  # swapExactTokensForTokens
        '0x7ff36ab5',  # swapExactETHForTokens
        '0xfb3bdb41',  # swapETHForExactTokens
        '0x414bf389',  # exactInputSingle
    ],
    'liquidation': [
        '0x41013712',  # liquidationCall
        '0xe78d0f49',  # liquidate
        '0x0144894a',  # absorb
    ],
    'flash_loan': [
        '0x16ea91e7',  # flashLoan
        '0x4303a8a7',  # flashLoanSimple
        '0x6d77a6a6',  # flashLoan
    ],
    'oracle': [
        '0x515f074f',  # submit (Chainlink)
        '0x80e634d8',  # updateAnswer
    ],
}
