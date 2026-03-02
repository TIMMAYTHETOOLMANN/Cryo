#!/usr/bin/env python3
"""
Deployer Monitor
Track new contract deployments from funded projects and known deployers

Monitors:
- Contract creation transactions
- Deployer addresses from funding rounds
- VC and team wallet deployments
- Unverified contract interactions
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Callable, Awaitable, Set
from dataclasses import dataclass, field
from web3 import Web3
import os

from ..data_lake.data_models import ChainId, OpportunitySignal, SignalType, SignalSource


@dataclass
class ContractDeployment:
    """New contract deployment data"""
    address: str
    deployer: str
    chain_id: int
    block_number: int
    timestamp: int
    tx_hash: str
    bytecode_hash: str  # keccak256 of bytecode
    is_verified: bool = False
    contract_name: str = ""
    protocol_name: str = ""  # If associated with known protocol
    funding_round: str = ""  # Associated funding round
    is_proxy: bool = False
    proxy_admin: str = ""
    implementation: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class DeployerProfile:
    """Contract deployer profile"""
    address: str
    label: str  # Team, VC, known deployer
    type: str  # team, vc, individual, factory
    total_deployments: int = 0
    successful_protocols: List[str] = field(default_factory=list)
    failed_protocols: List[str] = field(default_factory=list)
    total_value_locked: float = 0  # Across all deployments
    last_deployment: int = 0
    chains_active: List[int] = field(default_factory=list)
    associated_protocols: List[str] = field(default_factory=list)


class DeployerMonitor:
    """
    Monitor new contract deployments from tracked deployers
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Web3 providers for different chains
        self.w3_providers: Dict[int, Web3] = {}
        self._init_providers()

        # Data storage
        self._tracked_deployers: Dict[str, DeployerProfile] = {}
        self._deployments: List[ContractDeployment] = []
        self._bytecode_index: Dict[str, List[ContractDeployment]] = {}  # Hash -> deployments

        # Callbacks
        self._deployment_callbacks: List[Callable[[ContractDeployment], Awaitable[None]]] = []

        # Statistics
        self.deployments_detected = 0
        self.deployers_tracked = 0

        print("📦 Deployer Monitor initialized")

    def _init_providers(self):
        """Initialize Web3 providers for supported chains"""
        rpc_endpoints = {
            ChainId.ETHEREUM.value: os.getenv('ETH_RPC_URL', 'https://eth.llamarpc.com'),
            ChainId.ARBITRUM.value: os.getenv('ARBITRUM_RPC_URL', 'https://arb1.arbitrum.io/rpc'),
            ChainId.OPTIMISM.value: os.getenv('OPTIMISM_RPC_URL', 'https://mainnet.optimism.io'),
            ChainId.POLYGON.value: os.getenv('POLYGON_RPC_URL', 'https://polygon-rpc.com'),
            ChainId.BASE.value: os.getenv('BASE_RPC_URL', 'https://mainnet.base.org'),
            ChainId.AVALANCHE.value: os.getenv('AVALANCHE_RPC_URL', 'https://api.avax.network/ext/bc/C/rpc'),
            ChainId.BSC.value: os.getenv('BSC_RPC_URL', 'https://bsc-dataseed.binance.org'),
        }

        for chain_id, rpc_url in rpc_endpoints.items():
            try:
                self.w3_providers[chain_id] = Web3(Web3.HTTPProvider(rpc_url))
            except Exception as e:
                print(f"   ⚠️  Failed to init RPC for chain {chain_id}: {e}")

    async def start(self):
        """Start deployer monitoring"""
        print("\n📦 Starting Deployer Monitor...")
        self.is_running = True

        # Start background monitoring
        asyncio.create_task(self._monitor_loop())

        print("   ✅ Deployer Monitor started")

    async def stop(self):
        """Stop monitoring"""
        self.is_running = False
        print("   📦 Deployer Monitor stopped")

    async def _monitor_loop(self):
        """Background monitoring loop"""
        while self.is_running:
            try:
                # Check for new deployments every 30 seconds
                await self._check_new_deployments()
                await asyncio.sleep(30)

            except Exception as e:
                print(f"   ⚠️  Deployer Monitor error: {e}")
                await asyncio.sleep(15)

    async def _check_new_deployments(self):
        """Check for new contract deployments on tracked chains"""
        # This would use eth_getLogs with contract creation topic
        # For now, we'll simulate with a placeholder
        pass

    def add_deployer(self, address: str, label: str = "", type: str = "individual"):
        """Add deployer to tracking list"""
        if address not in self._tracked_deployers:
            profile = DeployerProfile(
                address=address,
                label=label,
                type=type,
            )
            self._tracked_deployers[address] = profile
            self.deployers_tracked += 1
            print(f"   👁️  Added deployer {label or address[:10]}... to tracking")

    def add_deployers_from_funding(self, funding_rounds: List[Any]):
        """Add deployers from funding round data"""
        for round in funding_rounds:
            # Add team wallets
            if hasattr(round, 'team_wallets'):
                for wallet in round.team_wallets:
                    self.add_deployer(wallet, f"{round.project_name} Team", "team")

            # Add investor wallets
            if hasattr(round, 'investor_wallets'):
                for wallet in round.investor_wallets:
                    self.add_deployer(wallet, f"{round.project_name} Investor", "vc")

    async def track_deployment(self, deployment: ContractDeployment):
        """Track new deployment"""
        self._deployments.append(deployment)
        self.deployments_detected += 1

        # Index by bytecode hash
        if deployment.bytecode_hash not in self._bytecode_index:
            self._bytecode_index[deployment.bytecode_hash] = []
        self._bytecode_index[deployment.bytecode_hash].append(deployment)

        # Update deployer profile
        if deployment.deployer in self._tracked_deployers:
            profile = self._tracked_deployers[deployment.deployer]
            profile.total_deployments += 1
            profile.last_deployment = deployment.timestamp

            if deployment.chain_id not in profile.chains_active:
                profile.chains_active.append(deployment.chain_id)

        # Emit callback
        await self._emit_deployment(deployment)

        # Create opportunity signal for new protocol
        await self._create_protocol_signal(deployment)

        print(f"   📦 New deployment: {deployment.address[:10]}... on chain {deployment.chain_id}")

    def get_deployments(self, hours: int = 24, chain_id: int = None) -> List[ContractDeployment]:
        """Get recent deployments"""
        cutoff = time.time() - (hours * 3600)

        filtered = [d for d in self._deployments if d.timestamp >= cutoff]

        if chain_id:
            filtered = [d for d in filtered if d.chain_id == chain_id]

        return filtered

    def get_deployer_activity(self, deployer_address: str) -> List[ContractDeployment]:
        """Get deployment history for specific deployer"""
        return [d for d in self._deployments if d.deployer == deployer_address]

    def find_similar_contracts(self, bytecode_hash: str) -> List[ContractDeployment]:
        """Find contracts with similar bytecode"""
        return self._bytecode_index.get(bytecode_hash, [])

    def on_deployment(self, callback: Callable[[ContractDeployment], Awaitable[None]]):
        """Register deployment callback"""
        self._deployment_callbacks.append(callback)

    async def _emit_deployment(self, deployment: ContractDeployment):
        """Emit deployment to callbacks"""
        for callback in self._deployment_callbacks:
            try:
                await callback(deployment)
            except Exception as e:
                print(f"   ⚠️  Deployment callback error: {e}")

    async def _create_protocol_signal(self, deployment: ContractDeployment):
        """Create opportunity signal for new protocol"""
        # Check if this is from a funded project
        is_funded = any(
            deployment.deployer == d.address
            for d in self._tracked_deployers.values()
            if d.type in ['team', 'vc']
        )

        if is_funded:
            signal = OpportunitySignal(
                signal_type=SignalType.NEW_PROTOCOL,
                source_module=SignalSource.CONTRACT_CRAWLER,
                chain_id=deployment.chain_id,
                target_contract=deployment.address,
                expected_value_usd=0,  # Would calculate based on funding size
                confidence=0.9 if deployment.protocol_name else 0.6,
                urgency_score=80,
                execution_complexity=5,
                gas_estimate=500000,
                gas_price_gwei=30,
                latency_requirement_ms=1000,
                expiry_block=deployment.block_number + 100,
                metadata={
                    'deployer': deployment.deployer,
                    'protocol_name': deployment.protocol_name,
                    'is_verified': deployment.is_verified,
                    'funding_round': deployment.funding_round,
                }
            )

            # Would emit to signal queue
            print(f"   🎯 Created signal for new protocol: {deployment.protocol_name or 'Unknown'}")

    def get_stats(self) -> Dict:
        """Get statistics"""
        return {
            'deployments_detected': self.deployments_detected,
            'deployers_tracked': self.deployers_tracked,
            'recent_deployments_24h': len(self.get_deployments(hours=24)),
        }


# Helper function to detect contract creation in transaction
def is_contract_creation(tx: Dict) -> bool:
    """Check if transaction is contract creation"""
    return tx.get('to') is None or tx.get('to') == '0x'


def extract_deployer(tx: Dict) -> str:
    """Extract deployer address from transaction"""
    return tx.get('from', '')


def calculate_bytecode_hash(bytecode: str) -> str:
    """Calculate keccak256 hash of bytecode"""
    return Web3.keccak(hexstr=bytecode).hex()
