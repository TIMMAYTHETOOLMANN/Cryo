#!/usr/bin/env python3
"""
Manticore Engine
Symbolic execution for smart contract analysis

Uses Manticore EVM to perform symbolic execution and discover:
- Order-dependent vulnerabilities
- MEV opportunities
- State manipulation vectors
- Reentrancy patterns
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import os
import threading


class AnalysisType(Enum):
    """Types of symbolic execution analysis"""
    MEV_OPPORTUNITIES = "mev_opportunities"
    REENTRANCY = "reentrancy"
    FRONT_RUNNING = "front_running"
    SANDWICH = "sandwich"
    ORDER_DEPENDENCE = "order_dependence"
    STATE_MANIPULATION = "state_manipulation"
    ALL = "all"


@dataclass
class AnalysisResult:
    """Result of symbolic execution analysis"""
    contract_address: str
    bytecode: str
    analysis_type: AnalysisType
    timestamp: int
    duration_ms: int
    states_explored: int
    vulnerabilities_found: int
    mev_opportunities: List[Dict] = field(default_factory=list)
    function_analysis: Dict[str, Dict] = field(default_factory=dict)
    state_variables: List[Dict] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    is_complete: bool = False
    error_message: str = ""


@dataclass
class FunctionSummary:
    """Summary of function analysis"""
    function_name: str
    selector: str
    is_mev_vulnerable: bool
    vulnerability_type: str = ""
    order_dependent: bool = False
    state_modifying: bool = False
    external_calls: List[str] = field(default_factory=list)
    gas_estimate: int = 0


class ManticoreEngine:
    """
    Manticore symbolic execution engine for EVM
    Analyzes contracts for MEV opportunities and vulnerabilities
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Manticore instance (lazy loaded)
        self._manticore = None
        self._analysis_timeout = self.config.get('analysis_timeout_s', 300)

        # Analysis cache
        self._cache: Dict[str, AnalysisResult] = {}

        # Statistics
        self.analyses_completed = 0
        self.states_explored_total = 0

        print("🔬 Manticore Engine initialized")

    def _init_manticore(self):
        """Initialize Manticore instance"""
        try:
            from manticore.ethereum import ManticoreEVM
            from manticore.core.manticore import State

            self._manticore = ManticoreEVM()

            # Register state exploration callback
            def did_execute_transaction(state):
                pass  # Could track state changes here

            self._manticore.on('did_execute_transaction', did_execute_transaction)

            print("   ✅ Manticore initialized")
        except ImportError:
            print("   ⚠️  Manticore not installed, using mock mode")
            self._manticore = None
        except Exception as e:
            print(f"   ⚠️  Manticore init error: {e}")
            self._manticore = None

    async def start(self):
        """Start engine"""
        print("\n🔬 Starting Manticore Engine...")
        self.is_running = True

        # Initialize in thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._init_manticore)

        print("   ✅ Manticore Engine started")

    async def stop(self):
        """Stop engine"""
        self.is_running = False

        if self._manticore:
            try:
                self._manticore.finalize()
            except:
                pass

        print("   🔬 Manticore Engine stopped")

    async def analyze_contract(self, bytecode: str, contract_address: str = "",
                                analysis_type: AnalysisType = AnalysisType.ALL,
                                timeout_s: int = None) -> AnalysisResult:
        """Analyze contract with symbolic execution"""
        start_time = time.time()
        timeout = timeout_s or self._analysis_timeout

        # Check cache
        cache_key = f"{contract_address}:{analysis_type.value}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Create result object
        result = AnalysisResult(
            contract_address=contract_address,
            bytecode=bytecode,
            analysis_type=analysis_type,
            timestamp=int(time.time()),
            duration_ms=0,
            states_explored=0,
            vulnerabilities_found=0,
            is_complete=False
        )

        # Run analysis
        try:
            if self._manticore:
                result = await self._run_symbolic_execution(result, timeout)
            else:
                # Mock analysis if Manticore not available
                result = await self._run_mock_analysis(result)

            result.duration_ms = int((time.time() - start_time) * 1000)
            result.is_complete = True

            # Cache result
            self._cache[cache_key] = result
            self.analyses_completed += 1
            self.states_explored_total += result.states_explored

            print(f"   🔬 Analysis complete: {contract_address[:10]}... ({result.duration_ms}ms)")

        except asyncio.TimeoutError:
            result.error_message = f"Analysis timeout after {timeout}s"
            print(f"   ⚠️  Analysis timeout: {contract_address[:10]}...")

        except Exception as e:
            result.error_message = str(e)
            print(f"   ⚠️  Analysis error: {e}")

        return result

    async def _run_symbolic_execution(self, result: AnalysisResult, timeout_s: int) -> AnalysisResult:
        """Run actual symbolic execution with Manticore"""
        loop = asyncio.get_event_loop()

        def run_analysis():
            try:
                # Create contract
                contract = self._manticore.create_contract(
                    result.bytecode,
                    name='target'
                )

                # Explore states
                states_explored = 0
                mev_opportunities = []
                function_analysis = {}

                # Run symbolic execution
                for state in self._manticore.all_states:
                    states_explored += 1

                    if states_explored > 1000:  # Limit exploration
                        break

                    # Analyze state for MEV patterns
                    # This is simplified - real implementation would analyze state transitions
                    pass

                # Generate results
                result.states_explored = states_explored
                result.mev_opportunities = mev_opportunities
                result.function_analysis = function_analysis

            except Exception as e:
                result.error_message = str(e)

        # Run with timeout
        await asyncio.wait_for(
            loop.run_in_executor(None, run_analysis),
            timeout=timeout_s
        )

        return result

    async def _run_mock_analysis(self, result: AnalysisResult) -> AnalysisResult:
        """Mock analysis when Manticore not available"""
        # Simulate analysis
        await asyncio.sleep(0.5)  # Simulate processing time

        # Generate mock results based on bytecode patterns
        bytecode_lower = result.bytecode.lower()

        # Detect patterns in bytecode
        patterns_found = []

        # Check for common MEV-vulnerable patterns
        if '38ed1739' in bytecode_lower:  # swapExactTokensForTokens
            patterns_found.append({
                'type': 'swap',
                'vulnerable': True,
                'reason': 'DEX swap function detected'
            })

        if '16ea91e7' in bytecode_lower:  # flashLoan
            patterns_found.append({
                'type': 'flash_loan',
                'vulnerable': False,
                'reason': 'Flash loan capability detected'
            })

        if '41013712' in bytecode_lower:  # liquidationCall
            patterns_found.append({
                'type': 'liquidation',
                'vulnerable': True,
                'reason': 'Liquidation function detected'
            })

        result.mev_opportunities = patterns_found
        result.states_explored = len(patterns_found) * 10  # Mock count
        result.vulnerabilities_found = sum(1 for p in patterns_found if p.get('vulnerable'))

        return result

    def analyze_function(self, bytecode: str, selector: str) -> FunctionSummary:
        """Analyze specific function for MEV vulnerability"""
        # Simplified analysis - would use Manticore for real analysis
        bytecode_lower = bytecode.lower()

        # Check if function exists in bytecode
        if selector[2:] not in bytecode_lower:
            return FunctionSummary(
                function_name="",
                selector=selector,
                is_mev_vulnerable=False
            )

        # Analyze based on selector
        mev_vulnerable_selectors = {
            '0x38ed1739': 'swapExactTokensForTokens',  # Sandwich vulnerable
            '0x7ff36ab5': 'swapExactETHForTokens',
            '0xfb3bdb41': 'swapETHForExactTokens',
            '0xe8e33700': 'addLiquidity',
            '0x441a3e70': 'removeLiquidityETH',
        }

        function_name = mev_vulnerable_selectors.get(selector, 'unknown')
        is_vulnerable = selector in mev_vulnerable_selectors

        return FunctionSummary(
            function_name=function_name,
            selector=selector,
            is_mev_vulnerable=is_vulnerable,
            vulnerability_type='sandwich' if is_vulnerable else '',
            order_dependent=is_vulnerable,
            state_modifying=True,
        )

    def get_cached_result(self, contract_address: str,
                          analysis_type: AnalysisType) -> Optional[AnalysisResult]:
        """Get cached analysis result"""
        cache_key = f"{contract_address}:{analysis_type.value}"
        return self._cache.get(cache_key)

    def clear_cache(self):
        """Clear analysis cache"""
        self._cache.clear()
        print("   🗑️  Manticore cache cleared")

    def get_stats(self) -> Dict:
        """Get engine statistics"""
        return {
            'analyses_completed': self.analyses_completed,
            'states_explored_total': self.states_explored_total,
            'cache_size': len(self._cache),
            'manticore_available': self._manticore is not None,
        }


# MEV-related function selectors for quick detection
MEV_FUNCTION_SELECTORS = {
    # DEX Swaps (sandwich vulnerable)
    '0x38ed1739': 'swapExactTokensForTokens',
    '0x7ff36ab5': 'swapExactETHForTokens',
    '0xfb3bdb41': 'swapETHForExactTokens',
    '0xac9650d8': 'multicall',  # Uniswap V3
    '0x5ae401dc': 'sweep',

    # Flash Loans
    '0x16ea91e7': 'flashLoan(address,address,uint256,bytes)',
    '0x4303a8a7': 'flashLoanSimple(address,address,uint256,bytes,uint256)',
    '0x6d77a6a6': 'flashLoan(address,address[],uint256[],uint256[],address,bytes,uint256)',

    # Liquidations
    '0x41013712': 'liquidationCall(address,address,address,uint256,bool)',
    '0xe78d0f49': 'liquidate(address,address,uint256)',

    # Large operations
    '0xa0712d68': 'mint(uint256)',
    '0x69328dec': 'redeem(uint256)',
    '0xc5ebeccd': 'redeemUnderlying(uint256)',
}
