#!/usr/bin/env python3
"""
Omni-Channel Example Usage
Demonstrates how to use the Omni-Channel Opportunity Triangulation Engine
"""

import asyncio
import time
from typing import Dict, Any

from omni_channel import (
    OmniOrchestrator,
    OpportunitySignal,
    SignalType,
    SignalSource,
    ExecutionModule,
    ChainId,
    ProviderConfig,
    MempoolRadar,
    AdvancedFilter,
)


async def example_basic_usage():
    """
    Basic example: Start the Omni-Channel engine and process signals
    """
    print("\n" + "=" * 60)
    print("OMNI-CHANNEL EXAMPLE: Basic Usage")
    print("=" * 60)
    
    # Configuration (use environment variables in production)
    config = {
        # Optional: API keys for mempool providers
        # 'bloxroute_api_key': 'YOUR_BLOXROUTE_KEY',
        # 'infura_api_key': 'YOUR_INFURA_KEY',
        # 'blocknative_api_key': 'YOUR_BLOCKNATIVE_KEY',
        
        # System configuration
        'enable_mempool_radar': True,
        'enable_contract_crawler': False,  # Not yet implemented
        'enable_static_analyzer': False,  # Not yet implemented
        'enable_cross_chain_monitor': False,  # Not yet implemented
        'enable_kafka': False,  # Set to True if Kafka is available
        
        # Performance tuning
        'signal_queue_max_size': 10000,
        'processing_interval_ms': 100,
    }
    
    # Create orchestrator
    orchestrator = OmniOrchestrator(config)
    
    # Start the engine (runs until interrupted)
    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        print("\n🛑 Stopping...")
        await orchestrator.stop()


async def example_mempool_radar_standalone():
    """
    Example: Use Mempool Radar as a standalone component
    """
    print("\n" + "=" * 60)
    print("OMNI-CHANNEL EXAMPLE: Mempool Radar Standalone")
    print("=" * 60)
    
    # Configure providers
    provider_configs = {
        # 'bloxroute': ProviderConfig(
        #     api_key='YOUR_BLOXROUTE_KEY',
        #     endpoint='https://api.bloxroute.com/v2',
        #     ws_endpoint='wss://api.bloxroute.com/v2/ws'
        # ),
        # 'infura': ProviderConfig(
        #     api_key='YOUR_INFURA_KEY',
        #     endpoint='https://mainnet.infura.io/v3',
        #     ws_endpoint='wss://mainnet.infura.io/ws/v3/YOUR_INFURA_KEY'
        # ),
        # 'blocknative': ProviderConfig(
        #     api_key='YOUR_BLOCKNATIVE_KEY',
        #     endpoint='https://api.blocknative.com/v0',
        #     ws_endpoint='wss://api.blocknative.com/v0'
        # ),
    }
    
    # Create radar
    radar = MempoolRadar(provider_configs)
    
    # Create filter
    filter = AdvancedFilter()
    
    # Register callbacks
    async def on_transaction(merged_tx):
        print(f"\n📝 Transaction: {merged_tx.tx_hash[:20]}...")
        print(f"   Providers: {merged_tx.providers_seen}")
        print(f"   Confidence: {merged_tx.confidence_score:.2f}")
        
        # Analyze for opportunities
        await filter.analyze_merged_transaction(merged_tx)
    
    async def on_signal(signal: OpportunitySignal):
        print(f"\n🎯 Opportunity Signal:")
        print(f"   Type: {signal.signal_type.value}")
        print(f"   Source: {signal.source_module.value}")
        print(f"   Chain: {signal.chain_id}")
        print(f"   Expected Value: ${signal.expected_value_usd:.2f}")
        print(f"   Confidence: {signal.confidence:.2f}")
        print(f"   Urgency: {signal.urgency_score:.0f}/100")
    
    radar.on_merged_transaction(on_transaction)
    filter.on_signal(on_signal)
    
    # Start
    await radar.start()
    
    # Run for 30 seconds
    await asyncio.sleep(30)
    
    # Stop
    await radar.stop()
    
    # Print stats
    print("\n📊 Statistics:")
    print(f"   Radar: {radar.get_stats()}")
    print(f"   Filter: {filter.get_stats()}")


async def example_signal_creation():
    """
    Example: Manually create and process opportunity signals
    """
    print("\n" + "=" * 60)
    print("OMNI-CHANNEL EXAMPLE: Signal Creation")
    print("=" * 60)
    
    # Create a liquidation signal
    liquidation_signal = OpportunitySignal(
        signal_type=SignalType.LIQUIDATION,
        source_module=SignalSource.ENHANCED_DETECTOR,
        chain_id=ChainId.ETHEREUM.value,
        target_contract="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",  # Aave V3
        user_address="0x1234567890123456789012345678901234567890",
        debt_asset="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
        collateral_asset="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
        debt_amount=10000 * 10**6,  # $10,000 USDC
        collateral_amount=5 * 10**18,  # 5 WETH
        health_factor=0.95,
        expected_value_usd=500.0,
        confidence=0.85,
        urgency_score=80,
        execution_complexity=3,
        gas_estimate=300000,
        gas_price_gwei=30,
        latency_requirement_ms=500,
        expiry_block=20000000 + 2,  # Current block + 2
    )
    
    print("\n🎯 Liquidation Signal:")
    print(f"   Protocol: Aave V3")
    print(f"   User: {liquidation_signal.user_address[:20]}...")
    print(f"   Debt: ${liquidation_signal.debt_amount / 10**6:,.2f} USDC")
    print(f"   Collateral: {liquidation_signal.collateral_amount / 10**18:.2f} WETH")
    print(f"   Health Factor: {liquidation_signal.health_factor:.3f}")
    print(f"   Expected Profit: ${liquidation_signal.expected_value_usd:.2f}")
    print(f"   Quality Score: {liquidation_signal.quality_score():.2f}")
    
    # Create an arbitrage signal
    arb_signal = OpportunitySignal(
        signal_type=SignalType.ARBITRAGE,
        source_module=SignalSource.MEMPOOL_RADAR,
        chain_id=ChainId.ETHEREUM.value,
        target_contract="0xE592427A0AEce92De3Edee1F18E0157C05861564",  # Uniswap V3
        expected_value_usd=1200.0,
        confidence=0.75,
        urgency_score=60,
        execution_complexity=5,
        gas_estimate=500000,
        gas_price_gwei=30,
        latency_requirement_ms=200,
        expiry_block=20000000 + 1,
        metadata={
            'dex': 'uniswap_v3',
            'token_in': 'USDC',
            'token_out': 'WETH',
            'price_difference_bps': 50,
        }
    )
    
    print("\n🎯 Arbitrage Signal:")
    print(f"   DEX: Uniswap V3")
    print(f"   Expected Profit: ${arb_signal.expected_value_usd:.2f}")
    print(f"   Quality Score: {arb_signal.quality_score():.2f}")
    print(f"   Recommended Module: {arb_signal.signal_type.value}")


async def example_callback_registration():
    """
    Example: Register custom callbacks for signal processing
    """
    print("\n" + "=" * 60)
    print("OMNI-CHANNEL EXAMPLE: Custom Callbacks")
    print("=" * 60)
    
    orchestrator = OmniOrchestrator({
        'enable_mempool_radar': False,  # Disable for this example
    })
    
    # Register custom signal handler
    async def custom_signal_handler(signal: OpportunitySignal):
        print(f"\n📝 Custom Handler:")
        print(f"   Signal ID: {signal.signal_id}")
        print(f"   Type: {signal.signal_type.value}")
        print(f"   Expected Value: ${signal.expected_value_usd:.2f}")
        
        # Custom logic here
        # e.g., send to Discord webhook, log to database, etc.
    
    # Add to signal router
    orchestrator.signal_router.add_routing_rule(
        lambda signal: ExecutionModule.LIQUIDATION_ENGINE 
        if signal.signal_type == SignalType.LIQUIDATION
        else ExecutionModule.MANUAL_REVIEW
    )
    
    print("\n✅ Custom callbacks registered")
    print("   (In production, these would process real signals)")


async def example_stats_monitoring():
    """
    Example: Monitor system statistics
    """
    print("\n" + "=" * 60)
    print("OMNI-CHANNEL EXAMPLE: Statistics Monitoring")
    print("=" * 60)
    
    orchestrator = OmniOrchestrator({
        'enable_mempool_radar': False,
    })
    
    # Start in background
    task = asyncio.create_task(orchestrator.start())
    
    # Monitor stats for 10 seconds
    for i in range(5):
        await asyncio.sleep(2)
        
        stats = orchestrator.get_stats()
        
        print(f"\n📊 Stats Update #{i+1}:")
        print(f"   Uptime: {stats['uptime_seconds']:.1f}s")
        print(f"   Signals Processed: {stats['signals_processed']}")
        print(f"   Queue Depth: {stats['queue_depth']}")
        print(f"   Errors: {stats['errors']}")
    
    # Stop
    orchestrator.is_running = False
    await task
    
    print("\n✅ Monitoring complete")


async def main():
    """Run all examples"""
    print("\n" + "=" * 60)
    print("OMNI-CHANNEL OPPORTUNITY TRIANGULATION ENGINE")
    print("Example Usage Demonstrations")
    print("=" * 60)
    
    examples = [
        ("Basic Usage", example_basic_usage),
        ("Mempool Radar Standalone", example_mempool_radar_standalone),
        ("Signal Creation", example_signal_creation),
        ("Callback Registration", example_callback_registration),
        ("Statistics Monitoring", example_stats_monitoring),
    ]
    
    for name, example_func in examples:
        try:
            await example_func()
            await asyncio.sleep(2)  # Pause between examples
        except Exception as e:
            print(f"\n❌ Example '{name}' failed: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
