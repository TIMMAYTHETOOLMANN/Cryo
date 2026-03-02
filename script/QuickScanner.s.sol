// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/CollateralizationDetector.sol";

/**
 * @title QuickScanner
 * @notice Fast over-collateralization scanner for known RTokens
 * @dev Lightweight scanner that avoids deep discovery to prevent gas issues
 */
contract QuickScanner is Script {
    // Known Reserve Protocol RToken Addresses
    address constant ETH_PLUS = 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8;
    address constant E_USD    = 0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F;

    CollateralizationDetector public detector;

    function run() external {
        detector = new CollateralizationDetector();

        console.log("=== OCDS Quick Scanner ===");
        console.log("Block:", block.number);
        console.log("Timestamp:", block.timestamp);
        console.log("");

        // Scan known RTokens
        _scanRToken("ETH+", ETH_PLUS);
        _scanRToken("eUSD", E_USD);

        console.log("");
        console.log("=== Scan Complete ===");
    }

    function _scanRToken(string memory name, address rToken) internal {
        console.log("---", name, "---");
        console.log("Address:", rToken);

        try detector.getRTokenPosition(rToken) returns (
            CollateralizationDetector.RTokenPositionData memory data
        ) {
            console.log("Total Supply:", data.totalSupply / 1e18, "tokens");
            console.log("Baskets Needed:", data.basketsNeeded / 1e18, "baskets");

            if (data.isOverCollateralized) {
                console.log("[OVER-COLLATERALIZED DETECTED]");
                console.log("Excess Baskets:", data.excessBaskets / 1e18);
                console.log("Collateral Ratio:", (data.collateralRatio * 100) / 1e18, "%");
                console.log("Profit Per Token:", (data.profitPerToken * 10000) / 1e18, "bps");

                // Analyze profitability
                (
                    uint256 excessBaskets,
                    uint256 profitBps,
                    uint256 totalProfitUsd,
                    bool isOpportunity
                ) = detector.analyzeRTokenProfitability(rToken, 1800e18);

                if (isOpportunity) {
                    console.log("");
                    console.log("*** PROFITABLE OPPORTUNITY ***");
                    console.log("Excess Baskets:", excessBaskets / 1e18);
                    console.log("Profit:", profitBps, "bps");
                    console.log("Est. Total Profit: $", totalProfitUsd / 1e18);
                }
            } else {
                console.log("[ADEQUATELY COLLATERALIZED]");
                console.log("Collateral Ratio:", (data.collateralRatio * 100) / 1e18, "%");
            }

            // Show collateral composition
            if (data.collateralTokens.length > 0) {
                console.log("Collateral composition:");
                for (uint256 i = 0; i < data.collateralTokens.length; i++) {
                    console.log("  Token:", data.collateralTokens[i]);
                    console.log("  Amount:", data.collateralAmounts[i] / 1e18);
                }
            }
        } catch (bytes memory err) {
            console.logBytes(err);
        }
        console.log("");
    }
}
