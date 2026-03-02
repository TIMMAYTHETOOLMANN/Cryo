"""
Omni-Channel Opportunity Triangulation Engine
Universal MEV & Opportunity Discovery System

This module transforms your liquidation engine from a reactive scanner
into a predictive, multi-vector opportunity discovery system.
"""

__version__ = "1.0.0"
__author__ = "Cryo1 Team"

from .data_lake import (
    OpportunitySignal,
    SignalType,
    ExecutionModule,
    SignalSource,
    ChainId,
    SignalQueue,
    SignalRouter,
    SignalStore,
    KafkaDataLake,
    KafkaConfig,
    KafkaTopic,
)

from .mempool_radar import (
    MempoolRadar,
    BloXrouteProvider,
    InfuraProvider,
    BlocknativeProvider,
    AdvancedFilter,
    ProviderConfig,
)

__all__ = [
    # Version
    '__version__',
    '__author__',
    
    # Data models
    'OpportunitySignal',
    'SignalType',
    'ExecutionModule',
    'SignalSource',
    'ChainId',
    
    # Queue management
    'SignalQueue',
    'SignalRouter',
    'SignalStore',
    
    # Kafka
    'KafkaDataLake',
    'KafkaConfig',
    'KafkaTopic',
    
    # Mempool Radar
    'MempoolRadar',
    'BloXrouteProvider',
    'InfuraProvider',
    'BlocknativeProvider',
    'AdvancedFilter',
    'ProviderConfig',
]


# Convenience function to create a complete system
def create_omni_channel(config: dict = None):
    """
    Create and configure complete Omni-Channel system
    
    Args:
        config: Configuration dictionary with API keys and settings
    
    Returns:
        OmniOrchestrator instance
    """
    from .omni_orchestrator import OmniOrchestrator
    return OmniOrchestrator(config or {})
