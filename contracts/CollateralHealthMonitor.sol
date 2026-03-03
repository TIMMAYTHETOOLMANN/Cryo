// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

// ============================================================================
//  CollateralHealthMonitor — Module 1
//  Continuously scan tracked positions and flag those with healthFactor < 1.05
//  (or protocol-specific thresholds) as candidates for automated risk mitigation.
//
//  Integrates with:
//    - Aave v2/v3 (getUserAccountData → healthFactor)
//    - Compound v2 (getAccountLiquidity → shortfall > 0)
//    - MakerDAO (Vat.urns + Spotter.ilks → collateral ratio vs liquidation ratio)
//    - Chainlink oracles (staleness + zero-price validation)
// ============================================================================

import "./interfaces/IAaveV2.sol";
import "./interfaces/IAaveV3.sol";
import "./interfaces/ICompoundV2.sol";
import "./interfaces/IMakerDAO.sol";
import "./interfaces/IChainlinkOracle.sol";

/// @title CollateralHealthMonitor
/// @notice Module 1: Scans DeFi positions and identifies at-risk candidates for
///         automated risk mitigation. Returns structured AtRiskPosition data for
///         downstream modules (Incentive Calculator, Risk Mitigation Executor).
contract CollateralHealthMonitor {

    // ── Structs ────────────────────────────────────────────────────────────

    /// @notice Represents a position flagged as at-risk.
    struct AtRiskPosition {
        uint256 chainId;
        uint8   protocol;         // 0=Aave v2, 1=Aave v3, 2=Compound v2, 3=MakerDAO
        address user;
        address debtAsset;
        address collateralAsset;
        uint256 debtAmount;       // In protocol-native units (wad for Aave/Maker, wei for Compound)
        uint256 collateralAmount;
        uint256 healthFactor;     // 1e18-scaled (< 1e18 means liquidatable)
        uint256 liquidationBonusBps; // Protocol liquidation bonus in basis points
        uint256 maxGasPrice;      // Maximum gas price in wei for this chain
    }

    /// @notice Result of a health check for a single position.
    struct HealthCheckResult {
        bool    isAtRisk;
        uint256 healthFactor;     // 1e18-scaled
        uint256 collateralUsd;    // 1e18-scaled USD value
        uint256 debtUsd;          // 1e18-scaled USD value
        uint256 shortfall;        // Compound-specific: excess debt in USD
    }

    // ── Configuration ──────────────────────────────────────────────────────

    /// @notice Health factor threshold below which a position is considered at-risk.
    /// @dev 1.05e18 = health factor of 1.05 (5% margin before liquidation).
    uint256 public constant DEFAULT_HF_THRESHOLD = 1.05e18;

    /// @notice Minimum debt value in USD (1e18-scaled) to consider for mitigation.
    /// @dev Prevents processing positions where gas costs exceed potential reward.
    uint256 public constant MIN_DEBT_USD = 100e18; // $100

    // ── Aave v2 Checks ────────────────────────────────────────────────────

    /// @notice Check if an Aave v2 position is at-risk (healthFactor < threshold).
    /// @param pool Aave v2 LendingPool address
    /// @param user Borrower address
    /// @param hfThreshold Health factor threshold (1e18-scaled, e.g. 1.05e18)
    /// @return result Health check result with risk assessment
    function checkAaveV2Health(
        address pool,
        address user,
        uint256 hfThreshold
    ) external view returns (HealthCheckResult memory result) {
        (
            uint256 totalCollateralETH,
            uint256 totalDebtETH,
            ,
            ,
            ,
            uint256 healthFactor
        ) = IAaveV2LendingPool(pool).getUserAccountData(user);

        result.healthFactor = healthFactor;
        result.collateralUsd = totalCollateralETH; // ETH-denominated in v2
        result.debtUsd = totalDebtETH;

        // At-risk if HF is below threshold AND there is meaningful debt
        result.isAtRisk = (healthFactor < hfThreshold) && (totalDebtETH > 0);
    }

    /// @notice Batch check multiple Aave v2 positions.
    /// @param pool Aave v2 LendingPool address
    /// @param users Array of borrower addresses
    /// @param hfThreshold Health factor threshold (1e18-scaled)
    /// @return results Array of health check results
    function batchCheckAaveV2(
        address pool,
        address[] calldata users,
        uint256 hfThreshold
    ) external view returns (HealthCheckResult[] memory results) {
        uint256 n = users.length;
        results = new HealthCheckResult[](n);
        for (uint256 i = 0; i < n; ) {
            (
                uint256 totalCollateralETH,
                uint256 totalDebtETH,
                ,
                ,
                ,
                uint256 healthFactor
            ) = IAaveV2LendingPool(pool).getUserAccountData(users[i]);

            results[i].healthFactor = healthFactor;
            results[i].collateralUsd = totalCollateralETH;
            results[i].debtUsd = totalDebtETH;
            results[i].isAtRisk = (healthFactor < hfThreshold) && (totalDebtETH > 0);

            unchecked { ++i; }
        }
    }

    // ── Aave v3 Checks ────────────────────────────────────────────────────

    /// @notice Check if an Aave v3 position is at-risk.
    /// @dev Aave v3 returns values in base currency (USD, 8 decimals).
    /// @param pool Aave v3 Pool address
    /// @param user Borrower address
    /// @param hfThreshold Health factor threshold (1e18-scaled)
    /// @return result Health check result
    function checkAaveV3Health(
        address pool,
        address user,
        uint256 hfThreshold
    ) external view returns (HealthCheckResult memory result) {
        (
            uint256 totalCollateralBase,
            uint256 totalDebtBase,
            ,
            ,
            ,
            uint256 healthFactor
        ) = IAaveV3Pool(pool).getUserAccountData(user);

        result.healthFactor = healthFactor;
        // Convert 8-decimal USD to 18-decimal for consistency
        result.collateralUsd = totalCollateralBase * 1e10;
        result.debtUsd = totalDebtBase * 1e10;

        result.isAtRisk = (healthFactor < hfThreshold) && (totalDebtBase > 0);
    }

    /// @notice Batch check multiple Aave v3 positions.
    function batchCheckAaveV3(
        address pool,
        address[] calldata users,
        uint256 hfThreshold
    ) external view returns (HealthCheckResult[] memory results) {
        uint256 n = users.length;
        results = new HealthCheckResult[](n);
        for (uint256 i = 0; i < n; ) {
            (
                uint256 totalCollateralBase,
                uint256 totalDebtBase,
                ,
                ,
                ,
                uint256 healthFactor
            ) = IAaveV3Pool(pool).getUserAccountData(users[i]);

            results[i].healthFactor = healthFactor;
            results[i].collateralUsd = totalCollateralBase * 1e10;
            results[i].debtUsd = totalDebtBase * 1e10;
            results[i].isAtRisk = (healthFactor < hfThreshold) && (totalDebtBase > 0);

            unchecked { ++i; }
        }
    }

    // ── Compound v2 Checks ─────────────────────────────────────────────────

    /// @notice Check if a Compound v2 position is at-risk (shortfall > 0).
    /// @param comptroller Compound v2 Comptroller address
    /// @param account Borrower address
    /// @return result Health check result (shortfall > 0 means liquidatable)
    function checkCompoundV2Health(
        address comptroller,
        address account
    ) external view returns (HealthCheckResult memory result) {
        (uint256 err, uint256 liquidity, uint256 shortfall) =
            ICompoundV2Comptroller(comptroller).getAccountLiquidity(account);

        require(err == 0, "Comptroller error");

        result.shortfall = shortfall;
        result.collateralUsd = liquidity;

        if (shortfall > 0) {
            // Under-collateralized: HF < 1.0
            result.isAtRisk = true;
            result.healthFactor = 0; // Liquidatable
        } else if (liquidity > 0) {
            // Over-collateralized: estimate HF as (liquidity / total) + 1
            // For Compound, HF > 1 when liquidity > 0
            result.healthFactor = type(uint256).max;
            result.isAtRisk = false;
        }
    }

    /// @notice Batch check multiple Compound v2 positions.
    function batchCheckCompoundV2(
        address comptroller,
        address[] calldata accounts
    ) external view returns (HealthCheckResult[] memory results) {
        uint256 n = accounts.length;
        results = new HealthCheckResult[](n);
        for (uint256 i = 0; i < n; ) {
            (uint256 err, uint256 liquidity, uint256 shortfall) =
                ICompoundV2Comptroller(comptroller).getAccountLiquidity(accounts[i]);

            if (err == 0) {
                results[i].shortfall = shortfall;
                results[i].collateralUsd = liquidity;
                if (shortfall > 0) {
                    results[i].isAtRisk = true;
                    results[i].healthFactor = 0;
                } else {
                    results[i].healthFactor = type(uint256).max;
                }
            }

            unchecked { ++i; }
        }
    }

    // ── MakerDAO Checks ────────────────────────────────────────────────────

    /// @notice Check if a MakerDAO vault is at-risk.
    /// @dev At-risk when: (ink * spot) < (art * rate), i.e., collateral value < debt.
    ///      The `spot` field from Vat.ilks already incorporates the liquidation ratio.
    /// @param vat MakerDAO Vat address
    /// @param ilk Collateral type identifier
    /// @param urn Urn (vault) address
    /// @return result Health check result
    function checkMakerHealth(
        address vat,
        bytes32 ilk,
        address urn
    ) external view returns (HealthCheckResult memory result) {
        (uint256 ink, uint256 art) = IMakerVat(vat).urns(ilk, urn);
        (, uint256 rate, uint256 spot, , ) = IMakerVat(vat).ilks(ilk);

        if (art == 0) {
            result.healthFactor = type(uint256).max;
            result.collateralUsd = ink;
            return result;
        }

        // collateral_value = ink * spot (ray arithmetic: wad * ray = rad)
        uint256 collateralValue = ink * spot; // rad (1e45)
        // debt = art * rate (rad)
        uint256 debtValue = art * rate; // rad (1e45)

        result.collateralUsd = ink;
        result.debtUsd = (art * rate) / 1e27; // Convert rad to wad

        if (debtValue > 0) {
            // Health factor = collateral_value / debt_value (1e18-scaled)
            result.healthFactor = (collateralValue * 1e18) / debtValue;
        } else {
            result.healthFactor = type(uint256).max;
        }

        // At-risk if collateral_value < debt_value (HF < 1.0)
        result.isAtRisk = collateralValue < debtValue;
    }

    // ── Oracle Validation ──────────────────────────────────────────────────

    /// @notice Validate a Chainlink oracle price feed.
    /// @param feed Chainlink AggregatorV3 address
    /// @param maxStalenessSeconds Maximum allowed age of the price
    /// @return price The validated price (in feed decimals)
    /// @return decimals The number of decimals in the price
    /// @return isValid True if price is fresh and positive
    function validateOracle(
        address feed,
        uint256 maxStalenessSeconds
    ) external view returns (int256 price, uint8 decimals, bool isValid) {
        AggregatorV3Interface oracle = AggregatorV3Interface(feed);

        (, int256 answer, , uint256 updatedAt, ) = oracle.latestRoundData();
        decimals = oracle.decimals();
        price = answer;

        // Validate: price must be positive and not stale
        isValid = (answer > 0) && (block.timestamp - updatedAt <= maxStalenessSeconds);
    }

    // ── Utility ────────────────────────────────────────────────────────────

    /// @notice Classify risk level from health factor.
    /// @param healthFactor Health factor (1e18-scaled)
    /// @return level 0=LIQUIDATABLE, 1=CRITICAL, 2=AT_RISK, 3=SAFE
    function classifyRisk(uint256 healthFactor) external pure returns (uint8 level) {
        if (healthFactor < 1e18)     return 0; // LIQUIDATABLE (HF < 1.0)
        if (healthFactor < 1.05e18)  return 1; // CRITICAL (1.0 - 1.05)
        if (healthFactor < 1.2e18)   return 2; // AT_RISK (1.05 - 1.2)
        return 3;                               // SAFE (HF >= 1.2)
    }
}
