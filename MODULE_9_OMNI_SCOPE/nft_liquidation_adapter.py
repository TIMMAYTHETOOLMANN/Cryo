"""
NFT Liquidation Adapter - High-Bonus Illiquid Asset Capture
Integrated with existing liquidation pipeline
"""

class NFTLiquidationAdapter:
    def __init__(self):
        self.nft_protocols = {
            'benddao': BendDAOAdapter(),
            'nftfi': NFTfiAdapter(),
            'jpegd': JPEGdAdapter(),
            'paraspace': ParaSpaceAdapter()
        }
        self.nft_price_oracle = NFTFloorPriceOracle()
        self.nft_marketplace = NFTMarketplaceAggregator()
    
    async def liquidate_nft_position(self, position_data):
        """Execute NFT liquidation with flash loan integration"""
        
        # Verify NFT valuation
        floor_price = await self.nft_price_oracle.get_floor_price(
            position_data.nft_contract, position_data.token_id
        )
        
        if floor_price < position_data.debt_amount * 1.1:  # 10% buffer
            return {"error": "NFT value insufficient for liquidation"}
        
        # Get optimal flash loan
        flash_loan = await self.flash_loan_router.get_optimal_flash_loan(
            position_data.debt_asset, position_data.debt_amount, "nft_liquidation"
        )
        
        # Execute liquidation and immediate sale
        liquidation_result = await self._execute_atomic_nft_liquidation(
            position_data, flash_loan
        )
        
        return liquidation_result
    
    async def _execute_atomic_nft_liquidation(self, position, flash_loan):
        """Atomic NFT liquidation + sale in single transaction"""
        
        # 1. Flash loan debt amount
        # 2. Liquidate NFT position
        # 3. Receive NFT as collateral
        # 4. Sell NFT via marketplace aggregator
        # 5. Repay flash loan
        # 6. Keep profit
        
        return await self.nft_marketplace.execute_atomic_sale(
            position.nft_contract, position.token_id, flash_loan
        )
