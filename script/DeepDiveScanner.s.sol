// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/CollateralizationDetector.sol";
import "../contracts/OracleIntegration.sol";

/**
 * @title DeepDiveScanner
 * @notice Comprehensive over-collateralization analysis with full intelligence
 * @dev Provides complete exploit intelligence including:
 *   - Real-time collateral valuation
 *   - Gas-optimized profit calculations
 *   - Execution path analysis
 *   - Risk metrics
 *   - Optimal position sizing
 */
contract DeepDiveScanner is Script {
    // Known Reserve Protocol RToken Addresses
    address constant ETH_PLUS = 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8;
    address constant E_USD    = 0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F;

    // Infrastructure
    address constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;

    // Chainlink Oracles
    address constant ETH_USD_FEED = 0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419;
    address constant BTC_USD_FEED = 0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c;

    CollateralizationDetector public detector;

    struct DeepDiveReport {
        address rToken;
        string name;
        uint256 totalSupply;
        uint256 basketsNeeded;
        uint256 excessBaskets;
        uint256 collateralRatio;
        uint256 profitBps;
        uint256 estimatedProfitUsd;
        uint256 estimatedGasCost;
        uint256 netProfitUsd;
        uint256 roi;
        bool isOpportunity;
        RiskLevel risk;
        CollateralBreakdown[] collateral;
    }

    struct CollateralBreakdown {
        address token;
        string symbol;
        uint256 amount;
        uint256 valueUsd;
        uint256 weight;
        address oracle;
    }

    enum RiskLevel {
        LOW,
        MEDIUM,
        HIGH,
        EXTREME
    }

    function run() external {
        detector = new CollateralizationDetector();

        console.log("=================================================================");
        console.log("     OCDS DEEP DIVE INTELLIGENCE REPORT");
        console.log("=================================================================");
        console.log("");
        console.log("Block:", block.number);
        console.log("Timestamp:", block.timestamp);
        console.log("Gas Price:", tx.gasprice / 1e9, " gwei");
        console.log("");

        _deepDive("ETH+", ETH_PLUS);
        _deepDive("eUSD", E_USD);

        console.log("");
        console.log("=================================================================");
        console.log("     END OF INTELLIGENCE REPORT");
        console.log("=================================================================");
    }

    function _deepDive(string memory name, address rToken) internal {
        console.log("=================================================================");
        console.log("  DEEP DIVE:", name);
        console.log("  Address:", rToken);
        console.log("=================================================================");
        console.log("");

        // Phase 1: Get position data
        console.log("--- PHASE 1: POSITION ANALYSIS ---");
        CollateralizationDetector.RTokenPositionData memory position = 
            _getPositionData(rToken);
        _printPosition(position);
        console.log("");

        // Phase 2: Collateral breakdown
        console.log("--- PHASE 2: COLLATERAL COMPOSITION ---");
        CollateralBreakdown[] memory breakdown = _getCollateralBreakdown(rToken, position);
        _printCollateral(breakdown);
        console.log("");

        // Phase 3: Profitability analysis
        console.log("--- PHASE 3: PROFITABILITY ANALYSIS ---");
        (uint256 grossProfit, uint256 gasCost, uint256 netProfit) = 
            _analyzeProfitability(position, breakdown);
        _printProfitability(grossProfit, gasCost, netProfit);
        console.log("");

        // Phase 4: Risk assessment
        console.log("--- PHASE 4: RISK ASSESSMENT ---");
        RiskLevel risk = _assessRisk(position, breakdown);
        _printRisk(risk);
        console.log("");

        // Phase 5: Execution recommendation
        console.log("--- PHASE 5: EXECUTION RECOMMENDATION ---");
        _printRecommendation(position, breakdown, grossProfit, netProfit, risk);
        console.log("");
    }

    function _getPositionData(address rToken) 
        internal 
        view 
        returns (CollateralizationDetector.RTokenPositionData memory) 
    {
        return detector.getRTokenPosition(rToken);
    }

    function _printPosition(CollateralizationDetector.RTokenPositionData memory pos) internal pure {
        console.log("Total Supply:        ", pos.totalSupply / 1e18, " tokens");
        console.log("Baskets Needed:      ", pos.basketsNeeded / 1e18, " baskets");
        
        if (pos.isOverCollateralized) {
            console.log("Status:              [!] OVER-COLLATERALIZED");
            console.log("Excess Baskets:      ", pos.excessBaskets / 1e18);
        } else {
            console.log("Status:              [OK] ADEQUATELY COLLATERALIZED");
        }
        
        console.log("Collateral Ratio:    ", (pos.collateralRatio * 100) / 1e18, "%");
        console.log("Profit Per Token:    ", (pos.profitPerToken * 10000) / 1e18, " bps");
    }

    function _getCollateralBreakdown(address rToken, CollateralizationDetector.RTokenPositionData memory pos)
        internal
        view
        returns (CollateralBreakdown[] memory)
    {
        CollateralBreakdown[] memory breakdown = new CollateralBreakdown[](pos.collateralTokens.length);

        for (uint256 i = 0; i < pos.collateralTokens.length; i++) {
            breakdown[i].token = pos.collateralTokens[i];
            // Scale amounts by total supply to get total collateral, not per-token
            breakdown[i].amount = (pos.collateralAmounts[i] * pos.totalSupply) / 1e18;
            
            // Get symbol (simplified - would need ERC20 interface in production)
            breakdown[i].symbol = _getTokenSymbol(pos.collateralTokens[i]);
            
            // Get oracle price
            breakdown[i].oracle = _getOracleForToken(pos.collateralTokens[i]);
            if (breakdown[i].oracle != address(0)) {
                (uint256 price, uint8 decimals) = OracleIntegration.getPrice(breakdown[i].oracle);
                // Normalize to 1e18
                breakdown[i].valueUsd = (breakdown[i].amount * price) / (10 ** decimals);
            }

            // Calculate weight
            if (pos.collateralTokens.length > 0) {
                breakdown[i].weight = (1e18) / pos.collateralTokens.length;
            }
        }

        return breakdown;
    }

    function _getTokenSymbol(address token) internal pure returns (string memory) {
        if (token == 0xCd5fE23C85820F7B72D0926FC9b05b43E359b7ee) return "weETH";   // Ether.fi Wrapped ETH
        if (token == 0xA35b1B31Ce002FBF2058D22F30f95D405200A15b) return "ETHx";    // Stader ETHx
        if (token == 0xac3E018457B222d93114458476f3E3416Abbe38F) return "sfrxETH"; // Frax Staked ETH
        if (token == 0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0) return "wstETH";  // Lido Wrapped Staked ETH
        if (token == 0xae78736Cd615f374D3085123A210448E74Fc6393) return "rETH";    // Rocket Pool ETH
        if (token == 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2) return "WETH";
        if (token == 0x27F2f159Fe990Ba83D57f39Fd69661764BEbf37a) return "cUSDC";   // Reserve collateral
        if (token == 0xEB74EC1d4C1DAB412D5d6674F6833FD19d3118Ce) return "cUSDT";   // Reserve collateral
        if (token == 0x0aDc69041a2B086f8772aCcE2A754f410F211bed) return "cDAI";    // Reserve collateral
        return "UNKNOWN";
    }

    function _getOracleForToken(address token) internal pure returns (address) {
        if (token == 0xCd5fE23C85820F7B72D0926FC9b05b43E359b7ee) return ETH_USD_FEED; // weETH (ETH-denominated)
        if (token == 0xA35b1B31Ce002FBF2058D22F30f95D405200A15b) return ETH_USD_FEED; // ETHx (ETH-denominated)
        if (token == 0xac3E018457B222d93114458476f3E3416Abbe38F) return ETH_USD_FEED; // sfrxETH (ETH-denominated)
        if (token == 0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0) return ETH_USD_FEED; // wstETH (ETH-denominated)
        if (token == 0xae78736Cd615f374D3085123A210448E74Fc6393) return ETH_USD_FEED; // rETH (ETH-denominated)
        if (token == 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2) return ETH_USD_FEED; // WETH
        return address(0);
    }

    function _printCollateral(CollateralBreakdown[] memory breakdown) internal pure {
        console.log("Collateral Breakdown:");
        console.log("---------------------------------------------------------");
        
        for (uint256 i = 0; i < breakdown.length; i++) {
            console.log("Token:", breakdown[i].symbol);
            console.log("  Amount:", breakdown[i].amount / 1e18);
            console.log("  Value USD:", breakdown[i].valueUsd / 1e18);
            console.log("  Weight:", (breakdown[i].weight * 100) / 1e16, "%");
        }
    }

    function _analyzeProfitability(
        CollateralizationDetector.RTokenPositionData memory pos,
        CollateralBreakdown[] memory breakdown
    )
        internal
        view
        returns (uint256 grossProfit, uint256 gasCost, uint256 netProfit)
    {
        if (!pos.isOverCollateralized) {
            return (0, 0, 0);
        }

        // Calculate total collateral value from breakdown (already in 1e18 scale)
        uint256 totalCollateralValue = 0;
        for (uint256 i = 0; i < breakdown.length; i++) {
            totalCollateralValue += breakdown[i].valueUsd;
        }

        // Method 1: Use oracle-priced collateral value
        if (totalCollateralValue > 0) {
            // Average basket value = total collateral / baskets needed
            // Both are in 1e18 scale, so result is in 1e18 (dollars scaled)
            uint256 avgBasketValue = (totalCollateralValue * 1e18) / pos.basketsNeeded;
            // Profit = excess baskets * avg basket value (both 1e18 scaled)
            grossProfit = (pos.excessBaskets * avgBasketValue) / 1e18;
        } else {
            // Method 2: Estimate from excess baskets directly
            // RToken baskets are designed to be ~$1 each
            // Excess baskets = directly extractable value
            // Scale from internal units to actual basket count
            grossProfit = pos.excessBaskets / 1e18; // Convert from 1e18 to actual count
        }

        // Estimate gas cost (conservative estimate for full cycle)
        // Mint + Redeem cycle: ~300k-500k gas depending on collateral count
        uint256 estimatedGas = 500000;
        uint256 gasPriceGwei = tx.gasprice / 1e9;
        uint256 ethPrice = _getEthPrice();
        
        // Gas cost in USD = gas * gasPrice * ETH price
        gasCost = (estimatedGas * gasPriceGwei * ethPrice) / 1e9;

        // Net profit
        if (grossProfit > gasCost) {
            netProfit = grossProfit - gasCost;
        }
    }

    function _getEthPrice() internal view returns (uint256) {
        (uint256 price, uint8 decimals) = OracleIntegration.getPrice(ETH_USD_FEED);
        return (price * 1e18) / (10 ** decimals);
    }

    function _printProfitability(uint256 gross, uint256 gas, uint256 net) internal pure {
        console.log("Gross Profit:        $", gross / 1e18);
        console.log("Estimated Gas Cost:  $", gas / 1e18);
        console.log("Net Profit:          $", net / 1e18);

        if (net > 0) {
            console.log("");
            console.log("[PROFITABLE OPPORTUNITY DETECTED]");
        } else {
            console.log("");
            console.log("[Not profitable after gas costs]");
        }
    }

    function _assessRisk(
        CollateralizationDetector.RTokenPositionData memory pos,
        CollateralBreakdown[] memory breakdown
    ) internal pure returns (RiskLevel) {
        if (!pos.isOverCollateralized) {
            return RiskLevel.LOW;
        }

        // Risk factors:
        // 1. Collateral ratio (higher = safer)
        // 2. Collateral diversity (more diverse = safer)
        // 3. Excess basket percentage (higher = more profit but may indicate issues)

        uint256 ratio = pos.collateralRatio;
        uint256 diversity = breakdown.length;
        uint256 excessPct = (pos.excessBaskets * 10000) / pos.basketsNeeded;

        // Scoring
        uint256 score = 0;

        // Ratio score (0-3)
        if (ratio >= 1.5e18) score += 3;
        else if (ratio >= 1.2e18) score += 2;
        else if (ratio >= 1.05e18) score += 1;

        // Diversity score (0-2)
        if (diversity >= 5) score += 2;
        else if (diversity >= 3) score += 1;

        // Excess percentage score (0-2) - too high might indicate problems
        if (excessPct <= 1000) score += 2; // <= 10%
        else if (excessPct <= 2000) score += 1; // <= 20%

        // Map score to risk level
        if (score >= 6) return RiskLevel.LOW;
        if (score >= 4) return RiskLevel.MEDIUM;
        if (score >= 2) return RiskLevel.HIGH;
        return RiskLevel.EXTREME;
    }

    function _printRisk(RiskLevel risk) internal pure {
        string memory riskStr;
        if (risk == RiskLevel.LOW) riskStr = "LOW [SAFE]";
        else if (risk == RiskLevel.MEDIUM) riskStr = "MEDIUM [CAUTION]";
        else if (risk == RiskLevel.HIGH) riskStr = "HIGH [RISKY]";
        else riskStr = "EXTREME [DANGEROUS]";

        console.log("Risk Level:          ", riskStr);
        console.log("");
        console.log("Risk Factors:");
        console.log("  - Smart contract risk (Reserve Protocol)");
        console.log("  - Liquidation delay risk");
        console.log("  - Gas price volatility");
        console.log("  - MEV competition");
    }

    function _printRecommendation(
        CollateralizationDetector.RTokenPositionData memory pos,
        CollateralBreakdown[] memory breakdown,
        uint256 grossProfit,
        uint256 netProfit,
        RiskLevel risk
    ) internal pure {
        if (!pos.isOverCollateralized) {
            console.log("Recommendation:      HOLD - No opportunity");
            console.log("");
            console.log("This RToken is adequately collateralized.");
            console.log("Continue monitoring for future opportunities.");
            return;
        }

        if (netProfit == 0 || risk == RiskLevel.EXTREME) {
            console.log("Recommendation:      AVOID");
            console.log("");
            console.log("Reasons:");
            if (netProfit == 0) console.log("  - Not profitable after gas costs");
            if (risk == RiskLevel.EXTREME) console.log("  - Risk level too high");
            return;
        }

        console.log("Recommendation:      EXECUTE");
        console.log("");
        console.log("Execution Strategy:");
        console.log("  1. Acquire collateral tokens via DEX");
        console.log("  2. Mint RToken with collateral basket");
        console.log("  3. Immediately redeem for underlying");
        console.log("  4. Capture excess collateral as profit");
        console.log("");
        console.log("Optimal Position Size:");
        console.log("  - Conservative: 10% of excess (", pos.excessBaskets / 10, " baskets)");
        console.log("  - Moderate: 25% of excess (", (pos.excessBaskets * 25) / 100, " baskets)");
        console.log("  - Aggressive: 50% of excess (", pos.excessBaskets / 2, " baskets)");
        console.log("");
        console.log("Expected Returns:");
        console.log("  - Gross Profit: $", grossProfit / 1e18);
        console.log("  - Net Profit:   $", netProfit / 1e18);
        console.log("  - ROI:          ", (netProfit * 100) / grossProfit, "%");
        console.log("");
        console.log("[WARNING: Act fast - opportunities may be arbitraged away]");
    }
}
