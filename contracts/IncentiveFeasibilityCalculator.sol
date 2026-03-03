// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

// ============================================================================
//  IncentiveFeasibilityCalculator — Module 2
//  Computes whether the available reward (liquidation bonus) covers operational
//  costs such as temporary liquidity fees and gas. Used to filter opportunities
//  before submitting risk-mitigation transactions.
//
//  Inputs:
//    - Debt and collateral amounts (on-chain)
//    - Oracle prices (Chainlink, with staleness validation)
//    - Protocol liquidation bonus
//    - Flash loan provider fee
//    - Gas cost estimation
//
//  Output:
//    - FeasibilityResult with net incentive and go/no-go decision
// ============================================================================

import "./interfaces/IChainlinkOracle.sol";

/// @title IncentiveFeasibilityCalculator
/// @notice Module 2: Determines if a liquidation opportunity is economically
///         viable after accounting for flash loan fees, gas costs, and slippage.
contract IncentiveFeasibilityCalculator {

    // ── Structs ────────────────────────────────────────────────────────────

    /// @notice Parameters for a feasibility calculation.
    struct FeasibilityParams {
        uint256 debtAmount;           // Debt to repay (in debt asset units)
        uint8   debtDecimals;         // Decimals of debt asset
        uint256 collateralAmount;     // Collateral available to seize
        uint8   collateralDecimals;   // Decimals of collateral asset
        address debtPriceFeed;        // Chainlink feed for debt asset
        address collateralPriceFeed;  // Chainlink feed for collateral asset
        uint256 liquidationBonusBps;  // Protocol bonus in BPS (e.g., 500 = 5%)
        uint256 flashLoanFeeBps;      // Flash loan fee in BPS (e.g., 5 = 0.05%)
        uint256 estimatedGasUnits;    // Estimated gas for the liquidation tx
        uint256 gasPrice;             // Current gas price in wei
        uint256 nativeTokenPriceUsd8; // Native token (ETH) price in USD, 8 decimals
        uint256 maxOracleStaleness;   // Max oracle age in seconds
    }

    /// @notice Result of the feasibility calculation.
    struct FeasibilityResult {
        bool    isFeasible;           // True if net incentive exceeds minimum threshold
        uint256 collateralSeizedUsd;  // USD value of seized collateral (1e18)
        uint256 debtRepayUsd;         // USD value of debt to repay (1e18)
        uint256 grossIncentiveUsd;    // Collateral seized minus debt repay (1e18)
        uint256 flashLoanFeeUsd;      // Flash loan fee in USD (1e18)
        uint256 gasCostUsd;           // Gas cost in USD (1e18)
        uint256 netIncentiveUsd;      // Gross minus fees and gas (1e18)
    }

    // ── Constants ──────────────────────────────────────────────────────────

    /// @notice Minimum net incentive in USD (1e18-scaled) to consider feasible.
    /// @dev Set to $10 as a baseline; configurable per deployment.
    uint256 public constant MIN_INCENTIVE_USD = 10e18;

    /// @notice Gas cost buffer (120% = 20% extra for price volatility).
    uint256 public constant GAS_BUFFER_BPS = 12000; // 120% in basis points (100% + 20%)

    // ── Core Calculation ───────────────────────────────────────────────────

    /// @notice Calculate whether a liquidation opportunity is economically feasible.
    /// @param params Feasibility parameters
    /// @return result Detailed feasibility result
    function calculateFeasibility(
        FeasibilityParams calldata params
    ) external view returns (FeasibilityResult memory result) {
        // 1. Get oracle prices (with staleness validation)
        uint256 debtPriceUsd8 = _getValidatedPrice(params.debtPriceFeed, params.maxOracleStaleness);
        uint256 collPriceUsd8 = _getValidatedPrice(params.collateralPriceFeed, params.maxOracleStaleness);

        // 2. Calculate USD values (normalized to 1e18)
        result.debtRepayUsd = _tokenValueUsd18(
            params.debtAmount, params.debtDecimals, debtPriceUsd8
        );

        // Collateral seized = debt * (1 + bonus)
        uint256 collateralSeizedAmount = (params.debtAmount * (10000 + params.liquidationBonusBps)) / 10000;
        // Cap at available collateral
        if (collateralSeizedAmount > params.collateralAmount) {
            collateralSeizedAmount = params.collateralAmount;
        }
        result.collateralSeizedUsd = _tokenValueUsd18(
            collateralSeizedAmount, params.collateralDecimals, collPriceUsd8
        );

        // 3. Gross incentive = collateral_seized_usd - debt_repay_usd
        if (result.collateralSeizedUsd > result.debtRepayUsd) {
            result.grossIncentiveUsd = result.collateralSeizedUsd - result.debtRepayUsd;
        }

        // 4. Flash loan fee = debt_amount * fee_bps / 10000 (in USD)
        result.flashLoanFeeUsd = (result.debtRepayUsd * params.flashLoanFeeBps) / 10000;

        // 5. Gas cost = gas_units * gas_price * native_token_price * 1.2 (buffer)
        uint256 gasCostWei = params.estimatedGasUnits * params.gasPrice;
        uint256 gasCostUsdRaw = (gasCostWei * params.nativeTokenPriceUsd8) / 1e8;
        // Apply 20% buffer
        result.gasCostUsd = (gasCostUsdRaw * GAS_BUFFER_BPS) / 10000;

        // 6. Net incentive = gross - flash_fee - gas_cost
        uint256 totalCosts = result.flashLoanFeeUsd + result.gasCostUsd;
        if (result.grossIncentiveUsd > totalCosts) {
            result.netIncentiveUsd = result.grossIncentiveUsd - totalCosts;
        }

        // 7. Feasibility check
        result.isFeasible = result.netIncentiveUsd >= MIN_INCENTIVE_USD;
    }

    /// @notice Quick check: is a liquidation with given parameters likely profitable?
    /// @dev Simplified version without detailed breakdown for gas-efficient pre-screening.
    /// @param debtUsd18 Debt value in USD (1e18-scaled)
    /// @param collateralUsd18 Collateral value in USD (1e18-scaled)
    /// @param liquidationBonusBps Protocol bonus in BPS
    /// @param flashLoanFeeBps Flash loan fee in BPS
    /// @param gasCostUsd18 Estimated gas cost in USD (1e18-scaled)
    /// @return isFeasible True if estimated profit exceeds minimum threshold
    /// @return estimatedNetUsd18 Estimated net incentive in USD (1e18-scaled)
    function quickFeasibilityCheck(
        uint256 debtUsd18,
        uint256 collateralUsd18,
        uint256 liquidationBonusBps,
        uint256 flashLoanFeeBps,
        uint256 gasCostUsd18
    ) external pure returns (bool isFeasible, uint256 estimatedNetUsd18) {
        // Gross = collateral value * bonus / 10000 (simplified)
        uint256 bonusValue = (collateralUsd18 * liquidationBonusBps) / 10000;

        // Flash loan fee on debt
        uint256 flashFee = (debtUsd18 * flashLoanFeeBps) / 10000;

        // Gas cost with 20% buffer
        uint256 bufferedGas = (gasCostUsd18 * GAS_BUFFER_BPS) / 10000;

        uint256 totalCosts = flashFee + bufferedGas;

        if (bonusValue > totalCosts) {
            estimatedNetUsd18 = bonusValue - totalCosts;
        }

        isFeasible = estimatedNetUsd18 >= MIN_INCENTIVE_USD;
    }

    /// @notice Check if collateral value covers debt plus fees (bad debt detection).
    /// @dev Returns false for "bad debt" situations where collateral < debt + fees.
    /// @param collateralUsd18 Total collateral value in USD (1e18-scaled)
    /// @param debtUsd18 Total debt value in USD (1e18-scaled)
    /// @param flashLoanFeeBps Flash loan fee in BPS
    /// @param gasCostUsd18 Gas cost in USD (1e18-scaled)
    /// @return isSolvent True if collateral covers all costs
    function isBadDebt(
        uint256 collateralUsd18,
        uint256 debtUsd18,
        uint256 flashLoanFeeBps,
        uint256 gasCostUsd18
    ) external pure returns (bool isSolvent) {
        uint256 flashFee = (debtUsd18 * flashLoanFeeBps) / 10000;
        uint256 totalCost = debtUsd18 + flashFee + gasCostUsd18;
        isSolvent = collateralUsd18 >= totalCost;
    }

    // ── Internal Helpers ───────────────────────────────────────────────────

    /// @dev Get a validated price from a Chainlink feed.
    ///      Reverts if price is stale or zero.
    function _getValidatedPrice(
        address feed,
        uint256 maxStaleness
    ) internal view returns (uint256) {
        (, int256 answer, , uint256 updatedAt, ) =
            AggregatorV3Interface(feed).latestRoundData();

        require(answer > 0, "Oracle: zero price");
        require(block.timestamp - updatedAt <= maxStaleness, "Oracle: stale price");

        return uint256(answer);
    }

    /// @dev Convert a token amount to USD value (1e18-scaled).
    ///      price is in 8-decimal Chainlink format.
    function _tokenValueUsd18(
        uint256 amount,
        uint8   tokenDecimals,
        uint256 priceUsd8
    ) internal pure returns (uint256) {
        // value = amount * price / 10^tokenDecimals
        // Normalize to 1e18: value * 1e18 / 1e8 = value * 1e10
        return (amount * priceUsd8 * 1e10) / (10 ** tokenDecimals);
    }
}
