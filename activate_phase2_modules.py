#!/usr/bin/env python3
"""
PHASE 2 MODULES ACTIVATION SCRIPT
Activates and integrates all Phase 2 modules into the Omni-Channel system:
- Contract Crawler (funding intelligence, social mindshare, KOL tracking)
- Static Analyzer (Manticore, Panoramix, MEV patterns)
- Cross-Chain Monitor (bridge registry, N-hop pathfinding)
- ML Aggregator (quality scoring, competition estimation, dynamic routing)

Usage:
    python activate_phase2_modules.py
"""

import asyncio
import os
import sys
from typing import Dict, Any
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

# Import all Phase 2 modules
from omni_channel.contract_crawler import (
    FundingIntelligence,
    SocialMindshare,
    KOLWalletTracker,
    DeployerMonitor,
    CloneDetector,
    ProtocolClassifier,
)

from omni_channel.static_analyzer import (
    ManticoreEngine,
    PanoramixDecompiler,
    MEVPatternMatcher,
    LiquidationFormulaExtractor,
    VulnerabilityScanner,
)

from omni_channel.cross_chain_monitor import (
    BridgeRegistry,
    MultiChainGraph,
    NHopPathfinder,
    AtomicArbitrageDetector,
    LiquidityTracker,
)

from omni_channel.ml_aggregator import (
    QualityScorer,
    CompetitionEstimator,
    ComplexityAnalyzer,
    DynamicRouter,
    ModelTrainer,
)

from omni_channel.data_lake.data_models import OpportunitySignal, SignalType, ExecutionModule


class Phase2ModulesActivator:
    """
    Activates and integrates all Phase 2 modules
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False
        
        # Contract Crawler modules
        self.funding_intelligence: FundingIntelligence = None
        self.social_mindshare: SocialMindshare = None
        self.kol_wallet_tracker: KOLWalletTracker = None
        self.deployer_monitor: DeployerMonitor = None
        self.clone_detector: CloneDetector = None
        self.protocol_classifier: ProtocolClassifier = None
        
        # Static Analyzer modules
        self.manticore_engine: ManticoreEngine = None
        self.panoramix_decompiler: PanoramixDecompiler = None
        self.mev_pattern_matcher: MEVPatternMatcher = None
        self.liquidation_formula_extractor: LiquidationFormulaExtractor = None
        self.vulnerability_scanner: VulnerabilityScanner = None
        
        # Cross-Chain Monitor modules
        self.bridge_registry: BridgeRegistry = None
        self.multi_chain_graph: MultiChainGraph = None
        self.n_hop_pathfinder: NHopPathfinder = None
        self.atomic_arbitrage_detector: AtomicArbitrageDetector = None
        self.liquidity_tracker: LiquidityTracker = None
        
        # ML Aggregator modules
        self.quality_scorer: QualityScorer = None
        self.competition_estimator: CompetitionEstimator = None
        self.complexity_analyzer: ComplexityAnalyzer = None
        self.dynamic_router: DynamicRouter = None
        self.model_trainer: ModelTrainer = None
        
        # Signal integration
        self.discovered_opportunities = []
        
        print("\n🔧 PHASE 2 MODULES ACTIVATOR INITIALIZED")
        print("=" * 60)
    
    async def initialize(self):
        """Initialize all Phase 2 modules"""
        print("\n🚀 Initializing Phase 2 Modules...")
        
        # 1. Contract Crawler
        print("\n📡 Initializing Contract Discovery Crawler...")
        self.funding_intelligence = FundingIntelligence()
        self.social_mindshare = SocialMindshare()
        self.kol_wallet_tracker = KOLWalletTracker()
        self.deployer_monitor = DeployerMonitor()
        self.clone_detector = CloneDetector()
        self.protocol_classifier = ProtocolClassifier()
        print("   ✅ Contract Crawler ready")
        
        # 2. Static Analyzer
        print("\n🔍 Initializing Static Analysis Engine...")
        self.manticore_engine = ManticoreEngine()
        self.panoramix_decompiler = PanoramixDecompiler()
        self.mev_pattern_matcher = MEVPatternMatcher()
        self.liquidation_formula_extractor = LiquidationFormulaExtractor()
        self.vulnerability_scanner = VulnerabilityScanner()
        print("   ✅ Static Analyzer ready")
        
        # 3. Cross-Chain Monitor
        print("\n🌉 Initializing Cross-Chain Monitor...")
        self.bridge_registry = BridgeRegistry()
        self.multi_chain_graph = MultiChainGraph()
        self.n_hop_pathfinder = NHopPathfinder(self.multi_chain_graph)
        self.atomic_arbitrage_detector = AtomicArbitrageDetector()
        self.liquidity_tracker = LiquidityTracker()
        print("   ✅ Cross-Chain Monitor ready")
        
        # 4. ML Aggregator
        print("\n🤖 Initializing ML Aggregator...")
        self.quality_scorer = QualityScorer()
        self.competition_estimator = CompetitionEstimator()
        self.complexity_analyzer = ComplexityAnalyzer()
        self.dynamic_router = DynamicRouter()
        self.model_trainer = ModelTrainer()
        print("   ✅ ML Aggregator ready")
        
        print("\n✅ All Phase 2 Modules initialized")
        self.is_running = True
    
    async def start_monitoring(self):
        """Start all monitoring activities"""
        print("\n🔍 Starting Phase 2 monitoring activities...")
        
        # Start background tasks
        tasks = []
        
        # Contract crawler tasks
        if self.config.get('enable_funding_monitoring', True):
            tasks.append(asyncio.create_task(self._monitor_funding_rounds()))
        
        if self.config.get('enable_social_monitoring', True):
            tasks.append(asyncio.create_task(self._monitor_social_sentiment()))
        
        if self.config.get('enable_deployer_monitoring', True):
            tasks.append(asyncio.create_task(self._monitor_new_deployments()))
        
        # Cross-chain monitoring
        if self.config.get('enable_cross_chain_monitoring', True):
            tasks.append(asyncio.create_task(self._monitor_bridge_liquidity()))
            tasks.append(asyncio.create_task(self._find_arbitrage_paths()))
        
        # Static analysis on new contracts
        if self.config.get('enable_contract_analysis', True):
            tasks.append(asyncio.create_task(self._analyze_new_contracts()))
        
        print(f"   ✅ Started {len(tasks)} monitoring tasks")
        
        # Wait for all tasks
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _monitor_funding_rounds(self):
        """Monitor funding rounds for new protocol discovery"""
        print("   📊 Monitoring funding rounds...")
        while self.is_running:
            try:
                # Get recent funding rounds
                rounds = await self.funding_intelligence.get_recent_rounds(limit=10)
                
                for round_data in rounds:
                    # Check if we already processed this
                    if round_data.get('protocol_id') in self.discovered_opportunities:
                        continue
                    
                    # Create opportunity signal
                    signal = OpportunitySignal(
                        signal_id=f"funding_{round_data.get('protocol_id', 'unknown')}",
                        signal_type=SignalType.NEW_PROTOCOL,
                        source_module=ExecutionModule.MANUAL_REVIEW,
                        chain_id=1,
                        target_contract=round_data.get('contract_address', ''),
                        expected_value_usd=round_data.get('raised_amount_usd', 0) * 0.01,  # Estimate 1% of raise
                        confidence=0.7,
                        urgency_score=60,
                        execution_complexity=5,
                        gas_estimate=500000,
                        gas_price_gwei=30,
                        latency_requirement_ms=1000,
                        expiry_block=0,
                        metadata={
                            'protocol_name': round_data.get('protocol_name'),
                            'funding_round': round_data.get('round_type'),
                            'raised_amount': round_data.get('raised_amount_usd'),
                            'investors': round_data.get('investors', []),
                            'source': 'funding_intelligence',
                        }
                    )
                    
                    self.discovered_opportunities.append(signal.signal_id)
                    print(f"   🆕 New funded protocol: {round_data.get('protocol_name')} (${round_data.get('raised_amount_usd'):,.0f})")
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                print(f"   ⚠️  Funding monitoring error: {e}")
                await asyncio.sleep(60)
    
    async def _monitor_social_sentiment(self):
        """Monitor social sentiment for alpha discovery"""
        print("   💬 Monitoring social sentiment...")
        while self.is_running:
            try:
                # Get trending protocols
                trends = await self.social_mindshare.get_trending_protocols(limit=5)
                
                for trend in trends:
                    if trend.get('sentiment_score', 0) > 0.7:
                        print(f"   📈 High sentiment: {trend.get('protocol_name')} (score: {trend.get('sentiment_score'):.2f})")
                
                await asyncio.sleep(120)  # Check every 2 minutes
                
            except Exception as e:
                print(f"   ⚠️  Social monitoring error: {e}")
                await asyncio.sleep(60)
    
    async def _monitor_new_deployments(self):
        """Monitor new contract deployments"""
        print("   📝 Monitoring new deployments...")
        while self.is_running:
            try:
                # Get new deployments on Ethereum
                deployments = await self.deployer_monitor.get_new_deployments(
                    chain_id=1,
                    limit=20
                )
                
                for deployment in deployments:
                    address = deployment.get('address')
                    
                    # Classify the protocol
                    classification = await self.protocol_classifier.classify(
                        address,
                        chain_id=1
                    )
                    
                    print(f"   🆕 New {classification.protocol_type.value} deployed: {address[:10]}...")
                    
                    # Scan for vulnerabilities
                    vuln_scan = await self.vulnerability_scanner.scan(address)
                    
                    if vuln_scan.vulnerability_count > 0:
                        print(f"   ⚠️  Found {vuln_scan.vulnerability_count} vulnerabilities")
                    
                    # Check for MEV patterns
                    mev_patterns = self.mev_pattern_matcher.analyze_bytecode(
                        deployment.get('bytecode', '0x'),
                        address
                    )
                    
                    if mev_patterns:
                        print(f"   🎯 Found {len(mev_patterns)} MEV patterns")
                
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                print(f"   ⚠️  Deployment monitoring error: {e}")
                await asyncio.sleep(30)
    
    async def _monitor_bridge_liquidity(self):
        """Monitor bridge liquidity for arbitrage opportunities"""
        print("   🌉 Monitoring bridge liquidity...")
        while self.is_running:
            try:
                # Get bridge liquidity data
                liquidity_data = await self.liquidity_tracker.get_all_bridge_liquidity()
                
                for bridge_name, data in liquidity_data.items():
                    if data.get('utilization', 0) > 0.8:
                        print(f"   📊 {bridge_name} high utilization: {data.get('utilization'):.1%}")
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                print(f"   ⚠️  Liquidity monitoring error: {e}")
                await asyncio.sleep(60)
    
    async def _find_arbitrage_paths(self):
        """Find cross-chain arbitrage paths"""
        print("   🔍 Finding arbitrage paths...")
        while self.is_running:
            try:
                # Find arbitrage opportunities
                paths = await self.n_hop_pathfinder.find_all_arbitrage_paths(
                    start_token="USDC",
                    start_chain=1,
                    amount_usd=10000
                )
                
                if paths.paths_found > 0:
                    print(f"   💰 Found {paths.paths_found} arbitrage paths")
                    
                    # Create opportunity signals for profitable paths
                    for path in paths.profitable_paths[:3]:  # Top 3
                        signal = OpportunitySignal(
                            signal_id=f"arb_{path.path_id}",
                            signal_type=SignalType.CROSS_CHAIN_ARB,
                            source_module=ExecutionModule.CROSS_CHAIN_EXECUTOR,
                            chain_id=path.source_chain,
                            target_contract=path.path[0] if path.path else '',
                            expected_value_usd=path.expected_profit_usd,
                            confidence=0.6,
                            urgency_score=70,
                            execution_complexity=8,
                            gas_estimate=1000000,
                            gas_price_gwei=30,
                            latency_requirement_ms=5000,
                            expiry_block=0,
                            metadata={
                                'path': path.path,
                                'expected_profit': path.expected_profit_usd,
                                'bridges_used': path.bridges_used,
                                'source': 'cross_chain_monitor',
                            }
                        )
                        
                        # Score the opportunity
                        scored = self.quality_scorer.score(signal)
                        
                        if scored.tier.value in ['excellent', 'good']:
                            print(f"   🎯 High-quality arb: ${path.expected_profit_usd:.2f} (tier: {scored.tier.value})")
                
                await asyncio.sleep(600)  # Check every 10 minutes
                
            except Exception as e:
                print(f"   ⚠️  Pathfinding error: {e}")
                await asyncio.sleep(120)
    
    async def _analyze_new_contracts(self):
        """Analyze new contracts for MEV opportunities"""
        print("   🔬 Analyzing contracts...")
        while self.is_running:
            try:
                # This would integrate with the deployer monitor
                # For now, just demonstrate the analysis capability
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                print(f"   ⚠️  Analysis error: {e}")
                await asyncio.sleep(60)
    
    def get_status(self) -> Dict[str, Any]:
        """Get Phase 2 modules status"""
        return {
            'is_running': self.is_running,
            'contract_crawler': {
                'funding_intelligence': self.funding_intelligence is not None,
                'social_mindshare': self.social_mindshare is not None,
                'kol_wallet_tracker': self.kol_wallet_tracker is not None,
                'deployer_monitor': self.deployer_monitor is not None,
                'clone_detector': self.clone_detector is not None,
                'protocol_classifier': self.protocol_classifier is not None,
            },
            'static_analyzer': {
                'manticore_engine': self.manticore_engine is not None,
                'panoramix_decompiler': self.panoramix_decompiler is not None,
                'mev_pattern_matcher': self.mev_pattern_matcher is not None,
                'liquidation_formula_extractor': self.liquidation_formula_extractor is not None,
                'vulnerability_scanner': self.vulnerability_scanner is not None,
            },
            'cross_chain_monitor': {
                'bridge_registry': self.bridge_registry is not None,
                'multi_chain_graph': self.multi_chain_graph is not None,
                'n_hop_pathfinder': self.n_hop_pathfinder is not None,
                'atomic_arbitrage_detector': self.atomic_arbitrage_detector is not None,
                'liquidity_tracker': self.liquidity_tracker is not None,
            },
            'ml_aggregator': {
                'quality_scorer': self.quality_scorer is not None,
                'competition_estimator': self.competition_estimator is not None,
                'complexity_analyzer': self.complexity_analyzer is not None,
                'dynamic_router': self.dynamic_router is not None,
                'model_trainer': self.model_trainer is not None,
            },
            'discovered_opportunities': len(self.discovered_opportunities),
        }
    
    async def shutdown(self):
        """Shutdown all Phase 2 modules"""
        print("\n🛑 Shutting down Phase 2 modules...")
        self.is_running = False
        print("   ✅ Phase 2 modules stopped")


async def main():
    """Main entry point"""
    print("\n" + "=" * 60)
    print("  PHASE 2 MODULES ACTIVATION")
    print("=" * 60)
    
    # Configuration
    config = {
        'enable_funding_monitoring': True,
        'enable_social_monitoring': True,
        'enable_deployer_monitoring': True,
        'enable_cross_chain_monitoring': True,
        'enable_contract_analysis': True,
    }
    
    # Create activator
    activator = Phase2ModulesActivator(config)
    
    try:
        # Initialize
        await activator.initialize()
        
        # Print status
        status = activator.get_status()
        print("\n📊 Phase 2 Modules Status:")
        print(f"   Contract Crawler: {sum(status['contract_crawler'].values())}/6 active")
        print(f"   Static Analyzer: {sum(status['static_analyzer'].values())}/5 active")
        print(f"   Cross-Chain Monitor: {sum(status['cross_chain_monitor'].values())}/5 active")
        print(f"   ML Aggregator: {sum(status['ml_aggregator'].values())}/5 active")
        
        # Start monitoring
        print("\n🔍 Starting monitoring activities...")
        print("   (Press Ctrl+C to stop)")
        
        await activator.start_monitoring()
        
    except KeyboardInterrupt:
        print("\n\n🛑 Received interrupt")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await activator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
