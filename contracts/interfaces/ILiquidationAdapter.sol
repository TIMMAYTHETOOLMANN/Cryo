// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title ILiquidationAdapter
/// @notice Standard adapter interface for protocol-specific liquidations (Module 4 factory pattern).
/// @dev Deploy one adapter per supported protocol; the main executor delegates to the
///      appropriate adapter via the registry. Adapters are stateless – all context is
///      passed in calldata.
interface ILiquidationAdapter {
    /// @notice Execute a protocol-specific liquidation.
    /// @param collateralAsset Token to seize as collateral.
    /// @param debtAsset       Token being repaid (caller must have approved this contract).
    /// @param user            Borrower to liquidate.
    /// @param debtAmount      Amount of debt to repay (in debtAsset units).
    /// @return collateralSeized Actual amount of collateral received (in collateralAsset units).
    function liquidate(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtAmount
    ) external returns (uint256 collateralSeized);

    /// @notice Human-readable protocol name (e.g., "aave-v3", "compound-v2").
    function protocolName() external view returns (string memory);
}
