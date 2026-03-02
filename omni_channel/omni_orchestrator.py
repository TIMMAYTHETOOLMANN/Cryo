#!/usr/bin/env python3
"""
Omni-Channel Orchestrator
Main entry point that coordinates all detector arrays

Now includes Phase 2 modules:
- Contract Crawler (funding intelligence, social mindshare, KOL tracking)
- Static Analyzer (Manticore, Panoramix, MEV patterns)
- Cross-Chain Monitor (bridge registry, N-hop pathfinding)
- ML Aggregator (quality scoring, competition estimation, dynamic routing)
"""

import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from .data_lake import (
    OpportunitySignal, SignalType, SignalSource, ExecutionModule,
    SignalQueue, SignalRouter, SignalStore, Priority,
    KafkaDataLake, KafkaConfig, KafkaTopic
)
from .mempool_radar import (
    MempoolRadar, ProviderConfig, AdvancedFilter
)

# Phase 2 imports
from .contract_crawler import (
    FundingIntelligence,
    SocialMindshare,
    DeployerMonitor,
    ProtocolClassifier,
)
from .static_analyzer import (
    MEVPatternMatcher,
    VulnerabilityScanner,
    LiquidationFormulaExtractor,
)
from .cross_chain_monitor import (
    BridgeRegistry,
    MultiChainGraph,
    NHopPathfinder,
    LiquidityTracker,
)
from .ml_aggregator import (
    QualityScorer,
    CompetitionEstimator,
    ComplexityAnalyzer,
    DynamicRouter,
)


@dataclass
class OmniChannelConfig:
    """Configuration for Omni-Channel system"""
    # Mempool Radar config
    bloxroute_api_key: Optional[str] = None
    infura_api_key: Optional[str] = None
    blocknative_api_key: Optional[str] = None
    
    # Kafka config
    kafka_bootstrap_servers: List[str] = None
    
    # System config
    enable_kafka: bool = False
    enable_mempool_radar: bool = True
    enable_contract_crawler: bool = True
    enable_static_analyzer: bool = True
    enable_cross_chain_monitor: bool = True
    
    # Performance tuning
    signal_queue_max_size: int = 10000
    processing_interval_ms: int = 100
    
    def __post_init__(self):
        if self.kafka_bootstrap_servers is None:
            self.kafka_bootstrap_servers = ['localhost:9092']


class OmniOrchestrator:
    """
    Main orchestrator for the Omni-Channel Opportunity Triangulation Engine
    Coordinates all detector arrays and manages signal flow
    
    Phase 2 Modules Integrated:
    - Contract Crawler: Funding intelligence, social sentiment, deployer tracking
    - Static Analyzer: MEV pattern matching, vulnerability scanning
    - Cross-Chain Monitor: Bridge registry, N-hop pathfinding
    - ML Aggregator: Quality scoring, competition estimation, dynamic routing
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = self._parse_config(config or {})
        self.is_running = False

        # Core components
        self.signal_queue = SignalQueue(max_size=self.config.signal_queue_max_size)
        self.signal_router = SignalRouter()
        self.signal_store = SignalStore()

        # Detector arrays (initialized on start)
        self.mempool_radar: Optional[MempoolRadar] = None
        self.kafka_lake: Optional[KafkaDataLake] = None
        self.advanced_filter: Optional[AdvancedFilter] = None

        # Phase 2 Modules
        self.contract_crawler: Dict[str, Any] = {}
        self.static_analyzer: Dict[str, Any] = {}
        self.cross_chain_monitor: Dict[str, Any] = {}
        self.ml_aggregator: Dict[str, Any] = {}

        # Execution module queues
        self.execution_queues: Dict[ExecutionModule, asyncio.Queue] = {}

        # Statistics
        self.start_time: Optional[float] = None
        self.signals_processed = 0
        self.signals_routed = 0
        self.errors = 0
        self.phase2_opportunities_found = 0

        print("\n🎯 Omni-Channel Orchestrator Initialized (Phase 2 Enabled)")
        print("=" * 60)
        print(f"   Signal Queue: {self.config.signal_queue_max_size} max")
        print(f"   Mempool Radar: {'Enabled' if self.config.enable_mempool_radar else 'Disabled'}")
        print(f"   Contract Crawler: {'Enabled' if self.config.enable_contract_crawler else 'Disabled'}")
        print(f"   Static Analyzer: {'Enabled' if self.config.enable_static_analyzer else 'Disabled'}")
        print(f"   Cross-Chain Monitor: {'Enabled' if self.config.enable_cross_chain_monitor else 'Disabled'}")
        print(f"   ML Aggregator: {'Enabled' if self.config.get('enable_ml_aggregator', True) else 'Disabled'}")
        print("=" * 60)
    
    def _parse_config(self, config: Dict[str, Any]) -> OmniChannelConfig:
        """Parse configuration dictionary"""
        return OmniChannelConfig(
            bloxroute_api_key=config.get('bloxroute_api_key'),
            infura_api_key=config.get('infura_api_key'),
            blocknative_api_key=config.get('blocknative_api_key'),
            kafka_bootstrap_servers=config.get('kafka_servers', ['localhost:9092']),
            enable_kafka=config.get('enable_kafka', False),
            enable_mempool_radar=config.get('enable_mempool_radar', True),
            enable_contract_crawler=config.get('enable_contract_crawler', True),
            enable_static_analyzer=config.get('enable_static_analyzer', True),
            enable_cross_chain_monitor=config.get('enable_cross_chain_monitor', True),
            signal_queue_max_size=config.get('signal_queue_max_size', 10000),
            processing_interval_ms=config.get('processing_interval_ms', 100)
        )
    
    async def start(self):
        """Start all detector arrays and begin processing"""
        print("\n🚀 Starting Omni-Channel Engine...")
        print("=" * 60)

        self.is_running = True
        self.start_time = time.time()

        # Initialize Kafka if enabled
        if self.config.enable_kafka:
            await self._init_kafka()

        # Initialize Mempool Radar if enabled
        if self.config.enable_mempool_radar:
            await self._init_mempool_radar()

        # Initialize Phase 2 Modules
        if self.config.enable_contract_crawler:
            await self._init_phase2_contract_crawler()
        
        if self.config.enable_static_analyzer:
            await self._init_phase2_static_analyzer()
        
        if self.config.enable_cross_chain_monitor:
            await self._init_phase2_cross_chain_monitor()
        
        if self.config.get('enable_ml_aggregator', True):
            await self._init_phase2_ml_aggregator()

        # Register execution modules
        for module in ExecutionModule:
            self.execution_queues[module] = asyncio.Queue(maxsize=1000)
            self.signal_router.register_module(module)

        # Start processing loops
        asyncio.create_task(self._process_signals_loop())
        asyncio.create_task(self._route_signals_loop())
        asyncio.create_task(self._stats_loop())
        
        # Start Phase 2 monitoring tasks
        if self.config.enable_contract_crawler or self.config.enable_cross_chain_monitor:
            asyncio.create_task(self._phase2_monitoring_loop())

        print("\n✅ Omni-Channel Engine started successfully")
        print("=" * 60)
        
        # Keep running until stopped
        try:
            while self.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        
        await self.stop()
    
    async def stop(self):
        """Stop all detector arrays"""
        print("\n🛑 Stopping Omni-Channel Engine...")
        
        self.is_running = False
        
        # Stop mempool radar
        if self.mempool_radar:
            await self.mempool_radar.stop()
        
        # Stop Kafka
        if self.kafka_lake:
            await self.kafka_lake.stop()
        
        print("   ✅ Omni-Channel Engine stopped")
    
    async def _init_kafka(self):
        """Initialize Kafka Data Lake"""
        print("\n📡 Initializing Kafka Data Lake...")
        
        config = KafkaConfig(
            bootstrap_servers=self.config.kafka_bootstrap_servers,
            consumer_group='omni-channel'
        )
        
        self.kafka_lake = KafkaDataLake(config)
        await self.kafka_lake.start()
    
    async def _init_mempool_radar(self):
        """Initialize Mempool Radar with multiple providers"""
        print("\n📡 Initializing Mempool Radar...")
        
        provider_configs = {}
        
        if self.config.bloxroute_api_key:
            provider_configs['bloxroute'] = ProviderConfig(
                api_key=self.config.bloxroute_api_key,
                endpoint='https://api.bloxroute.com/v2',
                ws_endpoint='wss://api.bloxroute.com/v2/ws'
            )
            print(f"   ✅ bloXroute configured")
        
        if self.config.infura_api_key:
            provider_configs['infura'] = ProviderConfig(
                api_key=self.config.infura_api_key,
                endpoint='https://mainnet.infura.io/v3',
                ws_endpoint=f'wss://mainnet.infura.io/ws/v3/{self.config.infura_api_key}'
            )
            print(f"   ✅ Infura configured")
        
        if self.config.blocknative_api_key:
            provider_configs['blocknative'] = ProviderConfig(
                api_key=self.config.blocknative_api_key,
                endpoint='https://api.blocknative.com/v0',
                ws_endpoint='wss://api.blocknative.com/v0'
            )
            print(f"   ✅ Blocknative configured")
        
        if not provider_configs:
            print("   ⚠️  No mempool providers configured, using mock mode")
            # Could add a mock provider for testing
        
        self.mempool_radar = MempoolRadar(provider_configs)
        self.advanced_filter = AdvancedFilter()
        
        # Wire up signal flow
        self.mempool_radar.on_merged_transaction(self._handle_merged_transaction)
        self.advanced_filter.on_signal(self._handle_opportunity_signal)
        
        await self.mempool_radar.start()

    # ========================================================================
    # PHASE 2 MODULE INITIALIZATION
    # ========================================================================

    async def _init_phase2_contract_crawler(self):
        """Initialize Contract Crawler module"""
        print("\n📡 Initializing Contract Discovery Crawler...")
        
        self.contract_crawler['funding_intelligence'] = FundingIntelligence()
        self.contract_crawler['social_mindshare'] = SocialMindshare()
        self.contract_crawler['deployer_monitor'] = DeployerMonitor()
        self.contract_crawler['protocol_classifier'] = ProtocolClassifier()
        
        print("   ✅ Funding Intelligence initialized")
        print("   ✅ Social Mindshare initialized")
        print("   ✅ Deployer Monitor initialized")
        print("   ✅ Protocol Classifier initialized")
        print("   ✅ Contract Crawler ready")

    async def _init_phase2_static_analyzer(self):
        """Initialize Static Analyzer module"""
        print("\n🔍 Initializing Static Analysis Engine...")
        
        self.static_analyzer['mev_pattern_matcher'] = MEVPatternMatcher()
        self.static_analyzer['vulnerability_scanner'] = VulnerabilityScanner()
        self.static_analyzer['liquidation_formula_extractor'] = LiquidationFormulaExtractor()
        
        print("   ✅ MEV Pattern Matcher initialized")
        print("   ✅ Vulnerability Scanner initialized")
        print("   ✅ Liquidation Formula Extractor initialized")
        print("   ✅ Static Analyzer ready")

    async def _init_phase2_cross_chain_monitor(self):
        """Initialize Cross-Chain Monitor module"""
        print("\n🌉 Initializing Cross-Chain Monitor...")
        
        self.cross_chain_monitor['bridge_registry'] = BridgeRegistry()
        self.cross_chain_monitor['multi_chain_graph'] = MultiChainGraph()
        self.cross_chain_monitor['n_hop_pathfinder'] = NHopPathfinder(
            self.cross_chain_monitor['multi_chain_graph']
        )
        self.cross_chain_monitor['liquidity_tracker'] = LiquidityTracker()
        
        print("   ✅ Bridge Registry initialized")
        print("   ✅ Multi-Chain Graph initialized")
        print("   ✅ N-Hop Pathfinder initialized")
        print("   ✅ Liquidity Tracker initialized")
        print("   ✅ Cross-Chain Monitor ready")

    async def _init_phase2_ml_aggregator(self):
        """Initialize ML Aggregator module"""
        print("\n🤖 Initializing ML Aggregator...")
        
        self.ml_aggregator['quality_scorer'] = QualityScorer()
        self.ml_aggregator['competition_estimator'] = CompetitionEstimator()
        self.ml_aggregator['complexity_analyzer'] = ComplexityAnalyzer()
        self.ml_aggregator['dynamic_router'] = DynamicRouter()
        
        print("   ✅ Quality Scorer initialized")
        print("   ✅ Competition Estimator initialized")
        print("   ✅ Complexity Analyzer initialized")
        print("   ✅ Dynamic Router initialized")
        print("   ✅ ML Aggregator ready")

    async def _phase2_monitoring_loop(self):
        """Phase 2 monitoring activities"""
        print("\n🔍 Starting Phase 2 monitoring...")
        
        while self.is_running:
            try:
                await asyncio.sleep(60)  # Run every minute
                
                # Contract crawler - check for new deployments
                if 'deployer_monitor' in self.contract_crawler:
                    try:
                        deployments = await self.contract_crawler['deployer_monitor'].get_new_deployments(
                            chain_id=1,
                            limit=5
                        )
                        if deployments:
                            print(f"   📝 Found {len(deployments)} new contracts")
                            self.phase2_opportunities_found += len(deployments)
                    except Exception as e:
                        pass  # Silent fail for monitoring
                
                # Cross-chain - find arbitrage paths
                if 'n_hop_pathfinder' in self.cross_chain_monitor:
                    try:
                        paths = await self.cross_chain_monitor['n_hop_pathfinder'].find_all_arbitrage_paths(
                            start_token="USDC",
                            start_chain=1,
                            amount_usd=10000
                        )
                        if paths.paths_found > 0:
                            print(f"   💰 Found {paths.paths_found} arb paths")
                            self.phase2_opportunities_found += paths.paths_found
                    except Exception as e:
                        pass  # Silent fail for monitoring
                
            except Exception as e:
                self.errors += 1
                await asyncio.sleep(30)

    async def _handle_merged_transaction(self, merged):
        """Handle merged transaction from Mempool Radar"""
        if self.advanced_filter:
            await self.advanced_filter.analyze_merged_transaction(merged)
    
    async def _handle_opportunity_signal(self, signal: OpportunitySignal):
        """Handle opportunity signal from any detector"""
        # Add to queue
        priority = self._calculate_priority(signal)
        await self.signal_queue.put(signal, priority=priority)
        
        # Store for analytics
        self.signal_store.add(signal)
        
        # Publish to Kafka if enabled
        if self.kafka_lake and self.is_running:
            await self.kafka_lake.publish_signal(signal.to_dict())
    
    def _calculate_priority(self, signal: OpportunitySignal) -> Priority:
        """Calculate signal priority based on urgency and value"""
        if signal.urgency_score >= 90:
            return Priority.CRITICAL
        elif signal.urgency_score >= 70:
            return Priority.HIGH
        elif signal.urgency_score >= 40:
            return Priority.NORMAL
        else:
            return Priority.LOW
    
    async def _process_signals_loop(self):
        """Main signal processing loop"""
        print("\n⚡ Starting signal processor...")
        
        while self.is_running:
            try:
                # Get batch of signals
                signals = await self.signal_queue.get_batch(max_batch=10, timeout=0.5)
                
                for signal in signals:
                    # Validate signal
                    if not self._validate_signal(signal):
                        continue
                    
                    # Route to execution module
                    await self.signal_router.route(signal)
                    self.signals_routed += 1
                
                self.signals_processed += len(signals)
                
            except Exception as e:
                self.errors += 1
                print(f"   ⚠️  Signal processing error: {e}")
    
    async def _route_signals_loop(self):
        """Route signals to execution modules"""
        while self.is_running:
            try:
                for module, queue in self.execution_queues.items():
                    if not queue.empty():
                        signal = await queue.get()
                        # In production, this would send to actual execution module
                        print(f"   📤 Routed {signal.signal_type.value} to {module.value}")
                
                await asyncio.sleep(self.config.processing_interval_ms / 1000)
                
            except Exception as e:
                self.errors += 1
    
    def _validate_signal(self, signal: OpportunitySignal) -> bool:
        """Validate signal before routing"""
        # Check expiration
        if signal.is_expired(signal.expiry_block):
            return False
        
        # Check minimum confidence
        if signal.confidence < 0.3:
            return False
        
        # Check minimum expected value
        if signal.expected_value_usd < 10:  # $10 minimum
            return False
        
        return True
    
    async def _stats_loop(self):
        """Periodically print statistics"""
        while self.is_running:
            try:
                await asyncio.sleep(60)  # Every minute
                
                stats = self.get_stats()
                print("\n" + "=" * 60)
                print("📊 OMNI-CHANNEL STATISTICS")
                print("=" * 60)
                print(f"   Uptime: {stats['uptime_hours']:.2f} hours")
                print(f"   Signals Processed: {stats['signals_processed']}")
                print(f"   Signals Routed: {stats['signals_routed']}")
                print(f"   Queue Depth: {stats['queue_depth']}")
                print(f"   Errors: {stats['errors']}")
                
                if 'mempool_radar' in stats:
                    print("\n   Mempool Radar:")
                    print(f"     Providers: {stats['mempool_radar'].get('providers', [])}")
                
                print("=" * 60)
                
            except Exception as e:
                print(f"   ⚠️  Stats error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get system statistics"""
        uptime = time.time() - self.start_time if self.start_time else 0

        stats = {
            'is_running': self.is_running,
            'uptime_seconds': uptime,
            'uptime_hours': uptime / 3600,
            'signals_processed': self.signals_processed,
            'signals_routed': self.signals_routed,
            'queue_depth': self.signal_queue.size(),
            'errors': self.errors,
            'phase2_opportunities_found': self.phase2_opportunities_found,
            'store_stats': self.signal_store.get_stats(),
            'router_stats': self.signal_router.get_stats(),
            'phase2_modules': {
                'contract_crawler': len(self.contract_crawler) > 0,
                'static_analyzer': len(self.static_analyzer) > 0,
                'cross_chain_monitor': len(self.cross_chain_monitor) > 0,
                'ml_aggregator': len(self.ml_aggregator) > 0,
            }
        }

        if self.mempool_radar:
            stats['mempool_radar'] = self.mempool_radar.get_stats()

        if self.kafka_lake:
            stats['kafka'] = self.kafka_lake.get_stats()

        return stats


async def main():
    """Example usage"""
    # Configuration
    config = {
        'bloxroute_api_key': 'YOUR_BLOXROUTE_KEY',  # Optional
        'infura_api_key': 'YOUR_INFURA_KEY',  # Optional
        'blocknative_api_key': 'YOUR_BLOCKNATIVE_KEY',  # Optional
        'enable_kafka': False,  # Set to True if Kafka is available
        'enable_mempool_radar': True,
    }
    
    # Create and start orchestrator
    orchestrator = OmniOrchestrator(config)
    
    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        print("\n\n🛑 Received interrupt, shutting down...")
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
