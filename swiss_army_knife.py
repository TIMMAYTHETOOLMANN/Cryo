#!/usr/bin/env python3
"""
Swiss Army Knife: Unified OCDS Entry Point
Coordinates all discovery and execution modules dynamically.
"""

import asyncio
import os
import sys
import argparse
from typing import Dict, Any

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from omni_channel.omni_orchestrator import OmniOrchestrator
from omni_channel.data_lake.data_models import SignalType, ExecutionModule
from omni_channel.execution_router.execution_manager import ExecutionManager, create_execution_request

class SwissArmyKnife:
    def __init__(self, config: Dict[str, Any]):
        self.orchestrator = OmniOrchestrator(config)
        self.execution_manager = ExecutionManager(config)
        self.config = config

    async def start(self):
        print("\n🛠️  SWISS ARMY KNIFE: SYSTEM UNIFICATION ACTIVE")
        print("=" * 60)
        
        # Link execution manager to orchestrator
        self.orchestrator.signal_router.add_routing_rule(
            condition={'signal_type': SignalType.LIQUIDATION},
            target_module=ExecutionModule.LIQUIDATION_ENGINE
        )
        self.orchestrator.signal_router.add_routing_rule(
            condition={'signal_type': SignalType.ORACLE_UPDATE},
            target_module=ExecutionModule.LIQUIDATION_ENGINE
        )
        
        # Register custom handler to bridge signal -> execution
        async def execution_handler(signal):
            print(f"🎯 Opportunity Triangulated: {signal.signal_type.value} on chain {signal.chain_id}")
            request = create_execution_request(signal)
            
            # Enrich metadata for execution module
            if signal.signal_type == SignalType.LIQUIDATION:
                request.metadata['protocol'] = signal.metadata.get('protocol', 'Unknown')
            
            await self.execution_manager.submit(request, priority=9)

        # Start execution manager
        await self.execution_manager.start()
        
        # Start orchestrator with real execution bridge
        # In a real implementation, we'd monkeypatch or properly wire the router
        # For this consolidation, we use the internal signal handler
        self.orchestrator._handle_opportunity_signal = execution_handler
        
        await self.orchestrator.start()

async def main():
    parser = argparse.ArgumentParser(description="OCDS Swiss Army Knife")
    parser.add_argument("--scan-only", action="store_true", help="Monitor only mode")
    args = parser.parse_args()

    config = {
        'bloxroute_api_key': os.getenv('BLOXROUTE_API_KEY'),
        'infura_api_key': os.getenv('INFURA_API_KEY'),
        'blocknative_api_key': os.getenv('BLOCKNATIVE_API_KEY'),
        'enable_mempool_radar': True,
        'enable_contract_crawler': True,
        'enable_static_analyzer': True,
        'enable_cross_chain_monitor': True,
        'private_key': os.getenv('PRIVATE_KEY'),
        'flash_executor': os.getenv('FLASH_EXECUTOR'),
        'liquidation_executor_v1': os.getenv('LIQUIDATION_EXECUTOR_V1') or '0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f'
    }

    sak = SwissArmyKnife(config)
    try:
        await sak.start()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down Swiss Army Knife...")

if __name__ == "__main__":
    asyncio.run(main())
