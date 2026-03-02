// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/CollateralizationDetector.sol";
import "../contracts/interfaces/IReserveProtocol.sol";

/**
 * @title DirectExecutor
 * @notice Streamlined arbitrage execution for ETH+ over-collateralization
 * @dev Skips discovery, goes straight to execution for gas efficiency
 */
contract DirectExecutor is Script {
    address constant ETH_PLUS = 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8;
    
    CollateralizationDetector public detector;

    function run() external {
        detector = new CollateralizationDetector();

        console.log("=== OCDS Direct Executor ===");
        console.log("Block:", block.number);
        console.log("Target: ETH+");
        console.log("");

        // Phase 1: Verify opportunity
        console.log("=== Phase 1: Opportunity Verification ===");
        CollateralizationDetector.RTokenPositionData memory position = detector.getRTokenPosition(ETH_PLUS);
        
        console.log("Total Supply:", position.totalSupply / 1e18, "tokens");
        console.log("Baskets Needed:", position.basketsNeeded / 1e18, "baskets");
        console.log("Excess Baskets:", position.excessBaskets / 1e18);
        console.log("Collateral Ratio:", (position.collateralRatio * 100) / 1e18, "%");
        
        if (!position.isOverCollateralized) {
            console.log("ERROR: No over-collateralization detected. Aborting.");
            return;
        }
        
        console.log("STATUS: OVER-COLLATERALIZED CONFIRMED");
        console.log("");

        // Phase 2: Profitability check
        console.log("=== Phase 2: Profitability Analysis ===");
        (
            uint256 excessBaskets,
            uint256 profitBps,
            uint256 totalProfitUsd,
            bool isOpportunity
        ) = detector.analyzeRTokenProfitability(ETH_PLUS, 1800e18);
        
        console.log("Excess Baskets:", excessBaskets / 1e18);
        console.log("Profit:", profitBps, "bps");
        console.log("Est. Total Profit: $", totalProfitUsd / 1e18);
        console.log("Is Opportunity:", isOpportunity);
        console.log("");

        // Phase 3: Check if we should execute
        console.log("=== Phase 3: Execution Decision ===");
        
        try vm.envUint("PRIVATE_KEY") returns (uint256 deployerKey) {
            if (deployerKey == 0) {
                console.log("No private key configured. Scan-only mode.");
                console.log("Add PRIVATE_KEY to .env to enable execution.");
                return;
            }
            
            address executor = vm.addr(deployerKey);
            console.log("Executor:", executor);
            
            // Check ETH balance
            uint256 ethBalance = executor.balance;
            console.log("ETH Balance:", ethBalance / 1e18, "ETH");
            
            if (ethBalance < 0.1 ether) {
                console.log("WARNING: Low ETH balance. May not have enough for gas.");
            }
            
            if (isOpportunity) {
                console.log("");
                console.log("=== EXECUTION READY ===");
                console.log("Opportunity verified and profitable.");
                console.log("To execute manually:");
                console.log("1. Acquire basket tokens: USDC, sUSDe, sfETH, wstETH, rETH");
                console.log("2. Mint ETH+ at:", ETH_PLUS);
                console.log("3. Redeem immediately for underlying + excess");
                console.log("4. Profit: ~$", totalProfitUsd / 1e18, "total");
                console.log("======================");
            } else {
                console.log("Not profitable enough. Skipping execution.");
            }
        } catch {
            console.log("PRIVATE_KEY not set. Scan-only mode.");
        }
        
        console.log("");
        console.log("=== Execution Complete ===");
    }
}
