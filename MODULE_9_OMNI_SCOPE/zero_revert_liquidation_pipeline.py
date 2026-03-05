# ... existing code ...

class ZeroRevertLiquidationPipeline:
    """Enhanced pipeline integrating zero-revert strategies"""
    
    def __init__(self):
        self.oracle_listener = OracleEventListener(self.rpc_gateway, self.position_index)
        self.mempool_sniffer = ZeroRevertMempoolSniffer(
            self.mempool_feeds, self.dex_pairs, self.position_index, self.flashbots
        )
        self.jit_executor = EnhancedJITExecutor(
            self.rpc_gateway, self.flashbots, self.gas_oracle
        )
        self.predictive_model = PredictiveHealthModel(self.feature_store)
    
    async def start_zero_revert_pipeline(self):
        """Start all zero-revert monitoring systems"""
        
        zero_revert_tasks = [
            asyncio.create_task(self.oracle_listener.start_oracle_monitoring()),
            asyncio.create_task(self.mempool_sniffer.start_mempool_monitoring()),
            asyncio.create_task(self.jit_executor.start_block_monitoring()),
            asyncio.create_task(self.predictive_model.continuously_monitor())
        ]
        
        await asyncio.gather(*zero_revert_tasks)

# ... existing code ...
