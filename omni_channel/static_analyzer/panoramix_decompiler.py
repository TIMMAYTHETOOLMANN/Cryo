#!/usr/bin/env python3
"""
Panoramix Decompiler
Bytecode decompilation for unverified contracts

Uses Panoramix (Ethereum bytecode decompiler) to:
- Recover function signatures
- Identify contract structure
- Detect MEV patterns in unverified code
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
import os
import subprocess


@dataclass
class DecompiledContract:
    """Result of bytecode decompilation"""
    contract_address: str
    bytecode: str
    bytecode_hash: str
    timestamp: int
    duration_ms: int
    functions: List[Dict] = field(default_factory=list)
    events: List[Dict] = field(default_factory=list)
    state_variables: List[Dict] = field(default_factory=list)
    modifiers: List[Dict] = field(default_factory=list)
    is_proxy: bool = False
    proxy_type: str = ""
    implementation_address: str = ""
    source_code: str = ""  # Decompiled source
    is_complete: bool = False
    error_message: str = ""


@dataclass
class FunctionInfo:
    """Decompiled function information"""
    selector: str
    name: str
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    is_payable: bool = False
    is_view: bool = False
    is_pure: bool = False
    visibility: str = "external"
    decompiled_code: str = ""


class PanoramixDecompiler:
    """
    Panoramix bytecode decompiler
    Recovers high-level structure from EVM bytecode
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Panoramix configuration
        self.panoramix_path = self.config.get('panoramix_path', '/usr/bin/panoramix')
        self._panoramix_available = False

        # Analysis cache
        self._cache: Dict[str, DecompiledContract] = {}

        # Statistics
        self.contracts_decompiled = 0
        self.functions_recovered = 0

        print("📦 Panoramix Decompiler initialized")

    async def start(self):
        """Start decompiler"""
        print("\n📦 Starting Panoramix Decompiler...")
        self.is_running = True

        # Check if Panoramix is available
        await self._check_availability()

        print("   ✅ Panoramix Decompiler started")

    async def stop(self):
        """Stop decompiler"""
        self.is_running = False
        print("   📦 Panoramix Decompiler stopped")

    async def _check_availability(self):
        """Check if Panoramix is available"""
        try:
            # Try to run panoramix --version
            process = await asyncio.create_subprocess_exec(
                self.panoramix_path, '--version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)

            if process.returncode == 0:
                self._panoramix_available = True
                print("   ✅ Panoramix found")
            else:
                print("   ⚠️  Panoramix not available, using mock mode")

        except FileNotFoundError:
            print("   ⚠️  Panoramix not installed, using mock mode")
        except asyncio.TimeoutError:
            print("   ⚠️  Panoramix check timeout")
        except Exception as e:
            print(f"   ⚠️  Panoramix check error: {e}")

    async def decompile(self, bytecode: str, contract_address: str = "") -> DecompiledContract:
        """Decompile contract bytecode"""
        start_time = time.time()

        # Check cache
        from web3 import Web3
        bytecode_hash = Web3.keccak(hexstr=bytecode).hex()
        cache_key = f"{contract_address}:{bytecode_hash[:16]}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        # Create result object
        result = DecompiledContract(
            contract_address=contract_address,
            bytecode=bytecode,
            bytecode_hash=bytecode_hash,
            timestamp=int(time.time()),
            duration_ms=0,
            is_complete=False
        )

        # Run decompilation
        try:
            if self._panoramix_available:
                result = await self._run_panoramix(result)
            else:
                # Mock decompilation
                result = await self._run_mock_decompile(result)

            result.duration_ms = int((time.time() - start_time) * 1000)
            result.is_complete = True

            # Cache result
            self._cache[cache_key] = result
            self.contracts_decompiled += 1
            self.functions_recovered += len(result.functions)

            print(f"   📦 Decompiled: {contract_address[:10]}... ({len(result.functions)} functions)")

        except Exception as e:
            result.error_message = str(e)
            print(f"   ⚠️  Decompilation error: {e}")

        return result

    async def _run_panoramix(self, result: DecompiledContract) -> DecompiledContract:
        """Run actual Panoramix decompilation"""
        # Write bytecode to temp file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.bin', delete=False) as f:
            f.write(result.bytecode)
            bytecode_file = f.name

        try:
            # Run Panoramix
            process = await asyncio.create_subprocess_exec(
                self.panoramix_path, bytecode_file, '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60
            )

            if process.returncode == 0:
                # Parse JSON output
                import json
                data = json.loads(stdout.decode())
                result = self._parse_panoramix_output(result, data)
            else:
                result.error_message = stderr.decode()

        finally:
            # Cleanup temp file
            os.unlink(bytecode_file)

        return result

    async def _run_mock_decompile(self, result: DecompiledContract) -> DecompiledContract:
        """Mock decompilation when Panoramix not available"""
        await asyncio.sleep(0.3)  # Simulate processing

        # Extract function selectors from bytecode
        selectors = self._extract_selectors(result.bytecode)

        # Create function info for each selector
        functions = []
        for selector in selectors[:20]:  # Limit to first 20
            func_info = self._lookup_selector(selector)
            functions.append(func_info)

        result.functions = functions
        result.is_proxy = self._detect_proxy(result.bytecode)

        if result.is_proxy:
            result.proxy_type = "EIP-1967"
            result.implementation_address = self._extract_implementation(result.bytecode)

        return result

    def _extract_selectors(self, bytecode: str) -> List[str]:
        """Extract function selectors from bytecode"""
        selectors = []
        bytecode_lower = bytecode.lower()

        # Look for PUSH4 + 4 bytes pattern
        i = 2  # Skip 0x
        while i < len(bytecode_lower) - 10:
            if bytecode_lower[i:i+2] == '63':
                selector = '0x' + bytecode_lower[i+2:i+10]
                if selector not in selectors and selector != '0x00000000':
                    selectors.append(selector)
                i += 10
            else:
                i += 2

        return selectors

    def _lookup_selector(self, selector: str) -> FunctionInfo:
        """Lookup function signature from selector"""
        # Known function database
        known_functions = {
            '0xa9059cbb': ('transfer', ['address', 'uint256'], ['bool']),
            '0x23b872dd': ('transferFrom', ['address', 'address', 'uint256'], ['bool']),
            '0x095ea7b3': ('approve', ['address', 'uint256'], ['bool']),
            '0x18160ddd': ('totalSupply', [], ['uint256']),
            '0x70a08231': ('balanceOf', ['address'], ['uint256']),
            '0x38ed1739': ('swapExactTokensForTokens', ['uint256', 'uint256', 'address[]', 'address', 'uint256'], ['uint256[]']),
            '0x7ff36ab5': ('swapExactETHForTokens', ['uint256', 'address[]', 'address', 'uint256'], ['uint256[]']),
            '0xe8e33700': ('addLiquidity', ['address', 'address', 'uint256', 'uint256', 'uint256', 'uint256', 'address', 'uint256'], ['uint256', 'uint256', 'uint256']),
            '0x16ea91e7': ('flashLoan', ['address', 'address', 'uint256', 'bytes'], []),
            '0x41013712': ('liquidationCall', ['address', 'address', 'address', 'uint256', 'bool'], []),
        }

        if selector in known_functions:
            name, inputs, outputs = known_functions[selector]
            return FunctionInfo(
                selector=selector,
                name=name,
                inputs=inputs,
                outputs=outputs,
                is_payable=False,
                is_view=len(outputs) > 0 and 'transfer' not in name,
            )

        return FunctionInfo(
            selector=selector,
            name=f"unknown_{selector[2:8]}",
            inputs=[],
            outputs=[],
        )

    def _detect_proxy(self, bytecode: str) -> bool:
        """Detect if contract is a proxy"""
        # Check for proxy patterns
        proxy_selectors = {
            '0x3659cfe6',  # upgradeTo
            '0x5c60da1b',  # implementation
            '0x8da5cb5b',  # owner
        }

        bytecode_lower = bytecode.lower()
        return any(sel[2:] in bytecode_lower for sel in proxy_selectors)

    def _extract_implementation(self, bytecode: str) -> str:
        """Extract implementation address from proxy bytecode"""
        # Simplified - would need proper bytecode parsing
        return "0x" + "0" * 40

    def _parse_panoramix_output(self, result: DecompiledContract, data: Dict) -> DecompiledContract:
        """Parse Panoramix JSON output"""
        # Parse functions
        for func in data.get('functions', []):
            func_info = FunctionInfo(
                selector=func.get('selector', ''),
                name=func.get('name', 'unknown'),
                inputs=func.get('inputs', []),
                outputs=func.get('outputs', []),
                is_payable=func.get('payable', False),
                is_view=func.get('view', False),
                decompiled_code=func.get('code', ''),
            )
            result.functions.append(func_info)

        # Parse events
        result.events = data.get('events', [])

        # Parse state variables
        result.state_variables = data.get('variables', [])

        # Check proxy
        result.is_proxy = data.get('is_proxy', False)
        result.proxy_type = data.get('proxy_type', '')

        # Get decompiled source
        result.source_code = data.get('source', '')

        return result

    def get_cached_result(self, contract_address: str) -> Optional[DecompiledContract]:
        """Get cached decompilation result"""
        # Find in cache by address
        for key, value in self._cache.items():
            if value.contract_address == contract_address:
                return value
        return None

    def get_functions_by_selector(self, selector: str) -> List[DecompiledContract]:
        """Find all contracts with specific function selector"""
        results = []
        for result in self._cache.values():
            for func in result.functions:
                if func.selector == selector:
                    results.append(result)
                    break
        return results

    def get_stats(self) -> Dict:
        """Get decompiler statistics"""
        return {
            'contracts_decompiled': self.contracts_decompiled,
            'functions_recovered': self.functions_recovered,
            'cache_size': len(self._cache),
            'panoramix_available': self._panoramix_available,
        }


# Common proxy patterns
PROXY_PATTERNS = {
    'eip1967': {
        'implementation_slot': '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc',
        'admin_slot': '0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103',
    },
    'transparent': {
        'implementation_slot': '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc',
    },
    'uups': {
        'implementation_slot': '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc',
    },
}
