// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "../interfaces/ILiquidationAdapter.sol";

// ─── Minimal IERC20 ──────────────────────────────────────────────────────────
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function approve(address, uint256) external returns (bool);
    function transferFrom(address, address, uint256) external returns (bool);
}

// ─── Aave V3 liquidation interface ───────────────────────────────────────────
interface IAaveV3Pool {
    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;
}

/// @title AaveV3LiquidationAdapter
/// @notice ILiquidationAdapter implementation for Aave V3.
contract AaveV3LiquidationAdapter is ILiquidationAdapter {
    IAaveV3Pool public immutable POOL;

    constructor(address _pool) {
        require(_pool != address(0), "Zero pool");
        POOL = IAaveV3Pool(_pool);
    }

    /// @inheritdoc ILiquidationAdapter
    function liquidate(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtAmount
    ) external override returns (uint256 collateralSeized) {
        uint256 before = IERC20(collateralAsset).balanceOf(address(this));

        IERC20(debtAsset).approve(address(POOL), debtAmount);
        POOL.liquidationCall(collateralAsset, debtAsset, user, debtAmount, false);

        uint256 after_ = IERC20(collateralAsset).balanceOf(address(this));
        collateralSeized = after_ - before;

        // Forward seized collateral back to the caller (executor)
        if (collateralSeized > 0) {
            IERC20(collateralAsset).transfer(msg.sender, collateralSeized);
        }
    }

    /// @inheritdoc ILiquidationAdapter
    function protocolName() external pure override returns (string memory) {
        return "aave-v3";
    }
}
