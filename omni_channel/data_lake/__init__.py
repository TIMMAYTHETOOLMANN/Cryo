"""
Omni-Channel Data Lake
Real-time streaming infrastructure for opportunity data
"""

from .data_models import (
    OpportunitySignal,
    SignalType,
    ExecutionModule,
    SignalSource,
    ChainId,
    MempoolTransaction,
    OracleUpdate,
    LargeSwap,
    ProtocolInfo,
    CrossChainPath,
    DetectorStats,
    SystemStatus,
)

from .signal_queue import (
    SignalQueue,
    SignalRouter,
    SignalStore,
    Priority,
)

from .kafka_client import (
    KafkaDataLake,
    KafkaConfig,
    KafkaTopic,
    get_data_lake,
    publish_to_topic,
)

__all__ = [
    # Data models
    'OpportunitySignal',
    'SignalType',
    'ExecutionModule',
    'SignalSource',
    'ChainId',
    'MempoolTransaction',
    'OracleUpdate',
    'LargeSwap',
    'ProtocolInfo',
    'CrossChainPath',
    'DetectorStats',
    'SystemStatus',
    
    # Queue management
    'SignalQueue',
    'SignalRouter',
    'SignalStore',
    'Priority',
    
    # Kafka integration
    'KafkaDataLake',
    'KafkaConfig',
    'KafkaTopic',
    'get_data_lake',
    'publish_to_topic',
]
