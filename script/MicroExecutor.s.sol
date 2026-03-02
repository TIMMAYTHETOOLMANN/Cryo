// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/CollateralizationDetector.sol";
import "../contracts/interfaces/IReserveProtocol.sol";

/**
 * @title MicroExecutor
 * @notice Ultra-gas-optimized execution for micro-position arbitrage
 * @dev Designed for limited capital (~0.01 ETH) - minimal calls, direct execution
 */
contract MicroExecutor is Script {
    address constant ETH_PLUS = 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8;
    
    // Micro-position: 0.001 ETH worth (~$2-3 per basket)
    uint256 constant POSITION_SIZE_BASKETS = 10e18; // 10 baskets
    
    CollateralizationDetector public detector;

    function run() external {
        detector = new CollateralizationDetector();

        console.log("=== OCDS Micro Executor ===");
        console.log("Strategy: Ultra-low capital arbitrage");
        console.log("Block:", block.number);
        console.log("Gas Price:", tx.gasprice / 1e9, "gwei");
        console.log("");

        // Get executor info
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address executor = vm.addr(deployerKey);
        uint256 ethBalance = executor.balance;
        
        console.log("Executor:", executor);
        console.log("ETH Balance:", ethBalance / 1e18, "ETH");
        console.log("");

        // Verify opportunity
        console.log("=== Opportunity Check ===");
        CollateralizationDetector.RTokenPositionData memory position = detector.getRTokenPosition(ETH_PLUS);
        
        if (!position.isOverCollateralized) {
            console.log("ABORT: No over-collateralization detected");
            return;
        }
        
        console.log("Excess Baskets:", position.excessBaskets / 1e18);
        console.log("Collateral Ratio:", (position.collateralRatio * 100) / 1e18, "%");
        console.log("Profit Per Token:", (position.profitPerToken * 10000) / 1e18, "bps");
        console.log("");

        // Calculate position
        console.log("=== Position Calculation ===");
        uint256 positionSize = POSITION_SIZE_BASKETS;
        if (positionSize > position.excessBaskets) {
            positionSize = position.excessBaskets * 9 / 10; // 90% of excess
            console.log("Adjusted position to 90% of excess:", positionSize / 1e18, "baskets");
        }
        
        // Estimate profit: excess % * position
        uint256 excessPct = position.collateralRatio - 1e18; // e.g., 0.07e18 = 7%
        uint256 estimatedProfit = (positionSize * excessPct) / 1e18;

        console.log("Position Size:", positionSize / 1e18, "baskets");
        console.log("Est. Profit:", estimatedProfit / 1e18, "ETH");
        console.log("");

        // Gas estimation
        console.log("=== Gas Analysis ===");
        uint256 estimatedGas = 300000; // Conservative estimate
        uint256 gasCost = (estimatedGas * tx.gasprice);
        uint256 gasCostEth = gasCost / 1e18;
        
        console.log("Estimated Gas:", estimatedGas);
        console.log("Gas Cost:", gasCostEth, "ETH");
        console.log("Net Profit:", (estimatedProfit > gasCost) ? (estimatedProfit - gasCost) / 1e18 : 0, "ETH");
        console.log("");

        // Decision
        console.log("=== Execution Decision ===");
        if (ethBalance < gasCost * 2) {
            console.log("ABORT: Insufficient ETH for gas");
            console.log("Need:", gasCost * 2 / 1e18, "ETH");
            console.log("Have:", ethBalance / 1e18, "ETH");
            return;
        }
        
        if (estimatedProfit <= gasCost) {
            console.log("ABORT: Not profitable after gas");
            return;
        }
        
        console.log("PROCEED: Profitable micro-arbitrage detected");
        console.log("");
        console.log("=== Manual Execution Steps ===");
        console.log("1. Go to: https://app.reserve.org/eth/token/0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8/overview");
        console.log("2. Click 'Mint'");
        console.log("3. Enter amount: ~", positionSize / 1e17, "USD worth of basket tokens");
        console.log("4. Approve and mint ETH+");
        console.log("5. Immediately click 'Redeem' for same amount");
        console.log("6. Keep excess collateral as profit");
        console.log("");
        console.log("Expected Return: ~", estimatedProfit / 1e16, "cents ETH");
        console.log("==========================");
    }
}
