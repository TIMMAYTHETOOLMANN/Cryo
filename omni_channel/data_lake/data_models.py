#!/usr/bin/env python3
"""
Omni-Channel Data Models
Core data structures for opportunity signals and execution
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
import time
import uuid


class SignalType(Enum):
    """Types of opportunity signals"""
    LIQUIDATION = "liquidation"
    ARBITRAGE = "arbitrage"
    SANDWICH = "sandwich"
    BACKRUN = "backrun"
    FRONT_RUN = "front_run"
    CROSS_CHAIN_ARB = "cross_chain_arb"
    ORACLE_UPDATE = "oracle_update"
    LARGE_SWAP = "large_swap"
    NEW_PROTOCOL = "new_protocol"
    VULNERABILITY = "vulnerability"


class ExecutionModule(Enum):
    """Target execution modules"""
    LIQUIDATION_ENGINE = "liquidation_engine"
    ARBITRAGE_MODULE = "arbitrage_module"
    BACKRUN_BOT = "backrun_bot"
    SANDWICH_BOT = "sandwich_bot"
    CROSS_CHAIN_EXECUTOR = "cross_chain_executor"
    MANUAL_REVIEW = "manual_review"


class SignalSource(Enum):
    """Detector array sources"""
    MEMPOOL_RADAR = "mempool_radar"
    CONTRACT_CRAWLER = "contract_crawler"
    STATIC_ANALYZER = "static_analyzer"
    CROSS_CHAIN_MONITOR = "cross_chain_monitor"
    ENHANCED_DETECTOR = "enhanced_detector"


class ChainId(Enum):
    """Supported chain IDs"""
    ETHEREUM = 1
    ARBITRUM = 42161
    OPTIMISM = 10
    POLYGON = 137
    BASE = 8453
    AVALANCHE = 43114
    BSC = 56
    ZKSYNC = 324


@dataclass
class MempoolTransaction:
    """Raw mempool transaction data"""
    hash: str
    from_address: str
    to_address: Optional[str]
    value: int  # wei
    gas_price: int  # wei
    gas_limit: int
    input_data: str
    nonce: int
    block_number: Optional[int]
    timestamp: float
    provider: str  # Which mempool provider
    raw_tx: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OracleUpdate:
    """Oracle price update event"""
    aggregator: str
    asset: str
    price: int  # scaled price
    round_id: int
    updated_at: int
    block_number: int
    tx_hash: str
    price_usd: float  # calculated USD price


@dataclass
class LargeSwap:
    """Large DEX swap detection"""
    dex: str  # uniswap_v3, curve, etc.
    token_in: str
    token_out: str
    amount_in: int
    amount_out: int
    sender: str
    receiver: str
    tx_hash: str
    block_number: int
    price_impact_estimate: float  # 0.01 = 1%
    value_usd: float


@dataclass
class ProtocolInfo:
    """Discovered protocol information"""
    name: str
    address: str
    chain_id: int
    protocol_type: str  # lending, dex, yield, bridge
    deployer: str
    deployment_block: int
    deployment_timestamp: int
    is_verified: bool
    bytecode_hash: str
    social_mentions: int = 0
    kol_interactions: List[str] = field(default_factory=list)
    funding_round: Optional[Dict] = None


@dataclass
class CrossChainPath:
    """Cross-chain arbitrage path"""
    start_chain: int
    end_chain: int
    token: str
    start_amount: int
    end_amount: int
    bridge_name: str
    bridge_fee: int
    estimated_time_seconds: int
    hops: List[str] = field(default_factory=list)
    is_atomic: bool = False


@dataclass
class OpportunitySignal:
    """
    Primary opportunity signal structure
    Output from all detector arrays, input to ML Aggregator
    """
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    signal_type: SignalType = SignalType.LIQUIDATION
    source_module: SignalSource = SignalSource.MEMPOOL_RADAR
    chain_id: int = 1
    target_contract: Optional[str] = None
    user_address: Optional[str] = None  # For liquidations
    
    # Financial metrics
    expected_value_usd: float = 0.0
    gross_profit_usd: float = 0.0
    estimated_cost_usd: float = 0.0  # Gas + fees
    net_profit_usd: float = 0.0
    
    # Confidence & competition
    confidence: float = 0.0  # 0-1, signal reliability
    competition_estimate: float = 0.0  # 0-1, estimated competing bots
    urgency_score: float = 0.0  # 0-100
    
    # Execution parameters
    execution_complexity: int = 1  # 1-10
    gas_estimate: int = 0
    gas_price_gwei: int = 0
    latency_requirement_ms: int = 1000
    expiry_block: int = 0
    slippage_tolerance_bps: int = 50  # 0.5%
    
    # Transaction data (for mempool-based signals)
    trigger_tx_hash: Optional[str] = None
    trigger_tx_data: Optional[MempoolTransaction] = None
    
    # Protocol/position data
    protocol_info: Optional[ProtocolInfo] = None
    debt_asset: Optional[str] = None
    collateral_asset: Optional[str] = None
    debt_amount: int = 0
    collateral_amount: int = 0
    health_factor: float = 0.0
    
    # Cross-chain data
    cross_chain_path: Optional[CrossChainPath] = None
    
    # Additional metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: int = field(default_factory=lambda: int(time.time()))
    
    # Execution status
    status: str = "pending"  # pending, routed, executing, completed, failed, expired
    routed_to: Optional[ExecutionModule] = None
    execution_result: Optional[Dict[str, Any]] = None
    
    def quality_score(self) -> float:
        """
        Calculate raw quality score before ML adjustment
        Quality = (EV × Confidence) / (Complexity × Cost × Competition)
        """
        ev = self.expected_value_usd * self.confidence
        complexity_factor = self.execution_complexity / 10.0
        cost_factor = self.estimated_cost_usd / max(self.expected_value_usd, 1)
        competition_factor = self.competition_estimate + 0.1  # Avoid div by zero
        
        return ev / (complexity_factor * cost_factor * competition_factor + 1e-6)
    
    def is_expired(self, current_block: int) -> bool:
        """Check if signal has expired"""
        return current_block > self.expiry_block and self.expiry_block > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            'signal_id': self.signal_id,
            'signal_type': self.signal_type.value,
            'source_module': self.source_module.value,
            'chain_id': self.chain_id,
            'target_contract': self.target_contract,
            'expected_value_usd': self.expected_value_usd,
            'confidence': self.confidence,
            'urgency_score': self.urgency_score,
            'status': self.status,
            'timestamp': self.timestamp,
            **self.metadata
        }


@dataclass
class DetectorStats:
    """Statistics for a detector module"""
    module_name: str
    signals_detected: int = 0
    signals_routed: int = 0
    avg_confidence: float = 0.0
    avg_expected_value: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    uptime_seconds: float = 0.0
    last_update: int = field(default_factory=lambda: int(time.time()))


@dataclass
class SystemStatus:
    """Overall system status"""
    is_running: bool = False
    active_detectors: List[str] = field(default_factory=list)
    signals_per_second: float = 0.0
    queue_depth: int = 0
    execution_rate: float = 0.0  # signals executed / signals detected
    success_rate: float = 0.0  # successful executions / total executions
    total_profit_usd: float = 0.0
    uptime_seconds: float = 0.0
    last_update: int = field(default_factory=lambda: int(time.time()))


# ABI fragments for common events
ORACLE_UPDATE_TOPIC = "0x0559e1d74e6f1096eab3d8f5a5c86e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e"
SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
LIQUIDATION_TOPIC = "0xe41e63c81509396f443591faef4a20cfd6b61190447881959c478221fdf9c446"

# Common contract addresses by chain
AAVE_V3_POOLS = {
    ChainId.ETHEREUM.value: "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    ChainId.ARBITRUM.value: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    ChainId.OPTIMISM.value: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    ChainId.POLYGON.value: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    ChainId.BASE.value: "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
}

# Major DEX routers
DEX_ROUTERS = {
    ChainId.ETHEREUM.value: {
        'uniswap_v3': "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        'uniswap_v2': "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
        'curve': "0x8e764bE4288B842791989DB5b8ec06727903744F",
        'balancer': "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    }
}
