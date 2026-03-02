#!/usr/bin/env python3
"""
Clone Detector
Detect cross-chain protocol clones by analyzing bytecode and function signatures

Identifies:
- Successful protocol clones on new chains
- Factory deployments
- Fork detection
- Bytecode similarity matching
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Callable, Awaitable, Set, Tuple
from dataclasses import dataclass, field
from web3 import Web3
from eth_abi import decode
import hashlib

from ..data_lake.data_models import ChainId


@dataclass
class FunctionSignature:
    """Contract function signature"""
    selector: str  # 4-byte function selector
    name: str  # Function name
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    is_view: bool = False
    is_payable: bool = False


@dataclass
class ProtocolClone:
    """Detected protocol clone"""
    original_address: str
    clone_address: str
    original_chain: int
    clone_chain: int
    similarity_score: float  # 0-1, 1 = exact clone
    detected_at: int
    bytecode_match: bool = False
    function_match: bool = False
    matched_functions: List[str] = field(default_factory=list)
    protocol_name: str = ""
    version: str = ""  # Version if identifiable
    is_verified: bool = False


@dataclass
class BytecodeFingerprint:
    """Unique fingerprint of contract bytecode"""
    contract_address: str
    chain_id: int
    bytecode_hash: str  # Full bytecode keccak256
    code_hash: str  # EIP-1052 code hash
    function_selectors: List[str] = field(default_factory=list)
    immutable_hash: str = ""  # Hash of immutable parts
    size: int = 0


class CloneDetector:
    """
    Detect cross-chain protocol clones
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Web3 providers
        self.w3_providers: Dict[int, Web3] = {}
        self._init_providers()

        # Data storage
        self._known_protocols: Dict[str, Dict] = {}  # Protocol name -> chain -> address
        self._bytecode_fingerprints: Dict[str, BytecodeFingerprint] = {}
        self._detected_clones: List[ProtocolClone] = []

        # Callbacks
        self._clone_callbacks: List[Callable[[ProtocolClone], Awaitable[None]]] = []

        # Statistics
        self.protocols_indexed = 0
        self.clones_detected = 0

        print("🔍 Clone Detector initialized")

    def _init_providers(self):
        """Initialize Web3 providers"""
        import os
        rpc_endpoints = {
            ChainId.ETHEREUM.value: os.getenv('ETH_RPC_URL', 'https://eth.llamarpc.com'),
            ChainId.ARBITRUM.value: os.getenv('ARBITRUM_RPC_URL', 'https://arb1.arbitrum.io/rpc'),
            ChainId.OPTIMISM.value: os.getenv('OPTIMISM_RPC_URL', 'https://mainnet.optimism.io'),
            ChainId.POLYGON.value: os.getenv('POLYGON_RPC_URL', 'https://polygon-rpc.com'),
            ChainId.BASE.value: os.getenv('BASE_RPC_URL', 'https://mainnet.base.org'),
        }

        for chain_id, rpc_url in rpc_endpoints.items():
            try:
                self.w3_providers[chain_id] = Web3(Web3.HTTPProvider(rpc_url))
            except Exception as e:
                print(f"   ⚠️  Failed to init RPC for chain {chain_id}: {e}")

    async def start(self):
        """Start clone detection"""
        print("\n🔍 Starting Clone Detector...")
        self.is_running = True

        # Start background monitoring
        asyncio.create_task(self._monitor_loop())

        print("   ✅ Clone Detector started")

    async def stop(self):
        """Stop detection"""
        self.is_running = False
        print("   🔍 Clone Detector stopped")

    async def _monitor_loop(self):
        """Background monitoring"""
        while self.is_running:
            try:
                # Check for new clones every 5 minutes
                await self._scan_for_clones()
                await asyncio.sleep(300)

            except Exception as e:
                print(f"   ⚠️  Clone Detector error: {e}")
                await asyncio.sleep(60)

    def add_known_protocol(self, protocol_name: str, chain_id: int, address: str, bytecode: str = ""):
        """Add known protocol to index"""
        if protocol_name not in self._known_protocols:
            self._known_protocols[protocol_name] = {}

        self._known_protocols[protocol_name][chain_id] = {
            'address': address,
            'bytecode': bytecode,
            'fingerprint': self._create_fingerprint(address, chain_id, bytecode) if bytecode else None
        }

        self.protocols_indexed += 1
        print(f"   📚 Indexed protocol: {protocol_name} on chain {chain_id}")

    async def _scan_for_clones(self):
        """Scan for new clones across chains"""
        # This would scan new deployments and compare against known protocols
        # For now, placeholder for the scanning logic
        pass

    async def check_contract(self, address: str, chain_id: int, bytecode: str = None) -> List[ProtocolClone]:
        """Check if contract is a clone of known protocol"""
        if not bytecode:
            bytecode = await self._get_bytecode(address, chain_id)

        fingerprint = self._create_fingerprint(address, chain_id, bytecode)
        clones = []

        # Check against all known protocols
        for protocol_name, chains in self._known_protocols.items():
            for src_chain_id, data in chains.items():
                if src_chain_id == chain_id:
                    continue  # Skip same chain

                if data.get('fingerprint'):
                    similarity = self._calculate_similarity(fingerprint, data['fingerprint'])

                    if similarity > 0.8:  # 80% similarity threshold
                        clone = ProtocolClone(
                            original_address=data['address'],
                            clone_address=address,
                            original_chain=src_chain_id,
                            clone_chain=chain_id,
                            similarity_score=similarity,
                            detected_at=int(time.time()),
                            bytecode_match=similarity > 0.95,
                            function_match=self._compare_functions(fingerprint, data['fingerprint']),
                            matched_functions=self._get_matched_functions(fingerprint, data['fingerprint']),
                            protocol_name=protocol_name,
                            is_verified=False  # Would check explorer
                        )
                        clones.append(clone)
                        self._detected_clones.append(clone)
                        self.clones_detected += 1

                        print(f"   🔍 Found clone: {protocol_name} on chain {chain_id} ({similarity:.1%} match)")

        return clones

    def _create_fingerprint(self, address: str, chain_id: int, bytecode: str) -> BytecodeFingerprint:
        """Create bytecode fingerprint"""
        # Extract function selectors (every 4 bytes where pattern matches)
        selectors = self._extract_selectors(bytecode)

        # Calculate hashes
        bytecode_hash = Web3.keccak(hexstr=bytecode).hex()
        code_hash = Web3.keccak(hexstr=bytecode[2:]).hex()  # Simplified code hash

        # Hash of immutable parts (everything after last push with address)
        immutable_hash = self._hash_immutable_parts(bytecode)

        return BytecodeFingerprint(
            contract_address=address,
            chain_id=chain_id,
            bytecode_hash=bytecode_hash,
            code_hash=code_hash,
            function_selectors=selectors,
            immutable_hash=immutable_hash,
            size=len(bytecode) // 2  # Bytes
        )

    def _extract_selectors(self, bytecode: str) -> List[str]:
        """Extract function selectors from bytecode"""
        selectors = []
        bytecode_lower = bytecode.lower()

        # Look for 4-byte selectors (common pattern: PUSH4 + 4 bytes)
        i = 2  # Skip 0x prefix
        while i < len(bytecode_lower) - 10:
            # PUSH4 opcode is 63
            if bytecode_lower[i:i+2] == '63':
                selector = bytecode_lower[i+2:i+10]
                if selector not in selectors and selector != '00000000':
                    selectors.append('0x' + selector)
                i += 10
            else:
                i += 2

        return selectors[:50]  # Limit to first 50

    def _hash_immutable_parts(self, bytecode: str) -> str:
        """Hash immutable parts of bytecode"""
        # Simplified: just hash the entire bytecode for now
        # Real implementation would strip constructor arguments
        return hashlib.sha256(bytecode.encode()).hexdigest()

    def _calculate_similarity(self, fp1: BytecodeFingerprint, fp2: BytecodeFingerprint) -> float:
        """Calculate similarity between two fingerprints"""
        scores = []

        # Function selector overlap (most important)
        if fp1.function_selectors and fp2.function_selectors:
            overlap = len(set(fp1.function_selectors) & set(fp2.function_selectors))
            total = max(len(fp1.function_selectors), len(fp2.function_selectors))
            scores.append(overlap / total if total > 0 else 0)

        # Size similarity
        if fp1.size > 0 and fp2.size > 0:
            size_ratio = min(fp1.size, fp2.size) / max(fp1.size, fp2.size)
            scores.append(size_ratio)

        # Weighted average
        if scores:
            return (scores[0] * 0.7 + scores[1] * 0.3) if len(scores) > 1 else scores[0]
        return 0

    def _compare_functions(self, fp1: BytecodeFingerprint, fp2: BytecodeFingerprint) -> bool:
        """Check if function signatures match"""
        if not fp1.function_selectors or not fp2.function_selectors:
            return False

        # Check if core DeFi functions match
        core_functions = {
            '0xa9059cbb',  # transfer(address,uint256)
            '0x23b872dd',  # transferFrom(address,address,uint256)
            '0x095ea7b3',  # approve(address,uint256)
            '0x18160ddd',  # totalSupply()
        }

        fp1_core = set(fp1.function_selectors) & core_functions
        fp2_core = set(fp2.function_selectors) & core_functions

        return fp1_core == fp2_core and len(fp1_core) > 0

    def _get_matched_functions(self, fp1: BytecodeFingerprint, fp2: BytecodeFingerprint) -> List[str]:
        """Get list of matched function selectors"""
        return list(set(fp1.function_selectors) & set(fp2.function_selectors))

    async def _get_bytecode(self, address: str, chain_id: int) -> str:
        """Get contract bytecode"""
        w3 = self.w3_providers.get(chain_id)
        if not w3:
            return ""

        try:
            code = w3.eth.get_code(Web3.to_checksum_address(address)).hex()
            return code if code != '0x' else ""
        except Exception as e:
            print(f"   ⚠️  Bytecode fetch error: {e}")
            return ""

    def get_clones(self, protocol_name: str = None) -> List[ProtocolClone]:
        """Get detected clones"""
        if protocol_name:
            return [c for c in self._detected_clones if c.protocol_name == protocol_name]
        return self._detected_clones

    def get_cross_chain_deployments(self, protocol_name: str) -> Dict[int, str]:
        """Get all chain deployments for a protocol"""
        deployments = {}

        # From known protocols
        if protocol_name in self._known_protocols:
            for chain_id, data in self._known_protocols[protocol_name].items():
                deployments[chain_id] = data['address']

        # From detected clones
        for clone in self._detected_clones:
            if clone.protocol_name == protocol_name:
                deployments[clone.clone_chain] = clone.clone_address

        return deployments

    def on_clone_detected(self, callback: Callable[[ProtocolClone], Awaitable[None]]):
        """Register clone detection callback"""
        self._clone_callbacks.append(callback)

    async def _emit_clone(self, clone: ProtocolClone):
        """Emit clone detection to callbacks"""
        for callback in self._clone_callbacks:
            try:
                await callback(clone)
            except Exception as e:
                print(f"   ⚠️  Clone callback error: {e}")

    def get_stats(self) -> Dict:
        """Get statistics"""
        return {
            'protocols_indexed': self.protocols_indexed,
            'clones_detected': self.clones_detected,
            'fingerprints_cached': len(self._bytecode_fingerprints),
        }


# Common DeFi protocol function selectors
DEFI_FUNCTION_SELECTORS = {
    # ERC20
    '0xa9059cbb': 'transfer(address,uint256)',
    '0x23b872dd': 'transferFrom(address,address,uint256)',
    '0x095ea7b3': 'approve(address,uint256)',
    '0x18160ddd': 'totalSupply()',
    '0x70a08231': 'balanceOf(address)',

    # Lending
    '0xa0712d68': 'mint(uint256)',
    '0x69328dec': 'redeem(uint256)',
    '0xc5ebeccd': 'redeemUnderlying(uint256)',
    '0xe5a32283': 'borrowBalanceCurrent(address)',
    '0x70e90269': 'exchangeRateCurrent()',

    # DEX
    '0x022c0d9f': 'swap(uint256,uint256,address,bytes)',
    '0x38ed1739': 'swapExactTokensForTokens(uint256,uint256,address[],address,uint256)',
    '0xe8e33700': 'addLiquidity(address,address,uint256,uint256,uint256,uint256,address,uint256)',
    '0x441a3e70': 'removeLiquidityETH(address,uint256,uint256,uint256,address,uint256)',
}
