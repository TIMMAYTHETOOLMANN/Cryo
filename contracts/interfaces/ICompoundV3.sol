// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title ICompoundV3Comet
/// @notice Interface for Compound v3 (Comet) markets
/// @dev Mainnet USDC market: 0xc3d688B66703497DAA19211EEdff47f25384cdc3
interface ICompoundV3Comet {
    struct AssetInfo {
        uint8 offset;
        address asset;
        address priceFeed;
        uint64 scale;
        uint64 borrowCollateralFactor;
        uint64 liquidateCollateralFactor;
        uint64 liquidationFactor;
        uint128 supplyCap;
    }

    /// @notice Returns the borrow balance for an account
    function borrowBalanceOf(address account) external view returns (uint256);

    /// @notice Returns the collateral balance for an account for a specific asset
    function collateralBalanceOf(address account, address asset) external view returns (uint128);

    /// @notice Returns asset configuration by index
    function getAssetInfo(uint8 i) external view returns (AssetInfo memory);

    /// @notice Returns the number of assets
    function numAssets() external view returns (uint8);

    /// @notice Returns the base token address
    function baseToken() external view returns (address);

    /// @notice Returns the base token price feed
    function baseTokenPriceFeed() external view returns (address);
}
