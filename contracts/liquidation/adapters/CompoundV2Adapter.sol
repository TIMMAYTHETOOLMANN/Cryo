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

// ─── Compound V2 cToken interface ────────────────────────────────────────────
/// @dev `liquidateBorrow` is called on the **debt** cToken with the collateral
///      cToken as the third parameter.  See Compound V2 docs / source.
interface ICToken {
    function liquidateBorrow(
        address borrower,
        uint256 repayAmount,
        address cTokenCollateral
    ) external returns (uint256);
}

/// @title CompoundV2LiquidationAdapter
/// @notice ILiquidationAdapter implementation for Compound V2.
/// @dev Caller convention (per ILiquidationAdapter):
///      - `collateralAsset`  = the cToken the liquidator receives (collateral cToken).
///      - `debtAsset`        = the *underlying* ERC-20 of the debt market (e.g. USDC).
///      - `debtCToken`       = the cToken whose market is being repaid (set at deployment).
///
///      Flow:
///        1. Approve debtCToken to pull `repayAmount` of the underlying.
///        2. Call debtCToken.liquidateBorrow(borrower, repayAmount, collateralCToken).
///        3. Receive seized collateral cTokens; forward to caller (executor).
contract CompoundV2LiquidationAdapter is ILiquidationAdapter {
    /// @notice The cToken contract for the debt market this adapter repays.
    ICToken public immutable DEBT_CTOKEN;

    constructor(address _debtCToken) {
        require(_debtCToken != address(0), "Zero cToken");
        DEBT_CTOKEN = ICToken(_debtCToken);
    }

    /// @inheritdoc ILiquidationAdapter
    function liquidate(
        address collateralAsset,  // cToken to seize
        address debtAsset,        // underlying ERC-20 of the debt market
        address user,
        uint256 debtAmount
    ) external override returns (uint256 collateralSeized) {
        // Approve the debt cToken to pull the underlying repayment amount
        IERC20(debtAsset).approve(address(DEBT_CTOKEN), debtAmount);

        uint256 before = IERC20(collateralAsset).balanceOf(address(this));
        // Call liquidateBorrow on the debt cToken, seizing the collateral cToken
        uint256 err = DEBT_CTOKEN.liquidateBorrow(user, debtAmount, collateralAsset);
        require(err == 0, "Compound liquidation failed");

        collateralSeized = IERC20(collateralAsset).balanceOf(address(this)) - before;

        if (collateralSeized > 0) {
            IERC20(collateralAsset).transfer(msg.sender, collateralSeized);
        }
    }

    /// @inheritdoc ILiquidationAdapter
    function protocolName() external pure override returns (string memory) {
        return "compound-v2";
    }
}

