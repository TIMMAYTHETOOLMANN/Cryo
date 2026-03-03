// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "../../interfaces/ILiquidationAdapter.sol";

// ─── Minimal IERC20 ──────────────────────────────────────────────────────────
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function approve(address, uint256) external returns (bool);
    function transferFrom(address, address, uint256) external returns (bool);
}

// ─── MakerDAO Dog interface ──────────────────────────────────────────────────
/// @dev Dog.bark() initiates a liquidation auction in MakerDAO (Liquidations 2.0).
interface IMakerDog {
    function bark(bytes32 ilk, address urn, address kpr)
        external
        returns (uint256 id);
}

/// @title MakerDAOAdapter
/// @notice ILiquidationAdapter implementation for MakerDAO Liquidations 2.0.
/// @dev Calls Dog.bark() to initiate a collateral auction. The keeper (executor)
///      receives a reward (tip + chip) for initiating the auction.
///      Unlike Aave/Compound, MakerDAO liquidations use an auction mechanism,
///      so the "collateral seized" is the auction incentive (tip).
contract MakerDAOAdapter is ILiquidationAdapter {
    IMakerDog public immutable DOG;

    constructor(address _dog) {
        require(_dog != address(0), "Zero dog");
        DOG = IMakerDog(_dog);
    }

    /// @inheritdoc ILiquidationAdapter
    /// @dev For MakerDAO, `collateralAsset` is ignored (determined by ilk).
    ///      `debtAsset` is DAI. `debtAmount` is not used directly since bark()
    ///      handles the full liquidation. The adapter calls bark() and returns
    ///      the auction ID as a proxy for collateral seized.
    function liquidate(
        address,            // collateralAsset (not used — determined by ilk)
        address debtAsset,
        address user,       // urn address
        uint256 debtAmount
    ) external override returns (uint256 collateralSeized) {
        // Pull the debt tokens from the caller (executor)
        // In MakerDAO context, this is DAI used as keeper incentive
        if (debtAmount > 0) {
            require(
                IERC20(debtAsset).transferFrom(msg.sender, address(this), debtAmount),
                "Debt transferFrom failed"
            );
        }

        // Get collateral balance before bark
        uint256 before = IERC20(debtAsset).balanceOf(address(this));

        // Initiate the auction — keeper reward is sent to this contract
        // The ilk is encoded as a bytes32 from the adapter key
        // For simplicity, we use a default ilk (ETH-A) — in production,
        // the ilk would be passed via the adapter key mapping
        DOG.bark(bytes32("ETH-A"), user, address(this));

        // Calculate keeper reward received
        collateralSeized = IERC20(debtAsset).balanceOf(address(this)) - before;

        // Forward any received tokens back to the caller
        if (collateralSeized > 0) {
            IERC20(debtAsset).transfer(msg.sender, collateralSeized);
        }

        // Return any remaining debt tokens
        uint256 remaining = IERC20(debtAsset).balanceOf(address(this));
        if (remaining > 0) {
            IERC20(debtAsset).transfer(msg.sender, remaining);
        }
    }

    /// @inheritdoc ILiquidationAdapter
    function protocolName() external pure override returns (string memory) {
        return "makerdao";
    }
}
