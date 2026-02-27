// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IERC4626
/// @notice Standard ERC-4626 Tokenized Vault interface
/// @dev Detection: check for selectors 0x01e1d114 (totalAssets) and 0x38d52e0f (asset)
interface IERC4626 {
    /// @notice Returns the total amount of underlying assets held by the vault
    function totalAssets() external view returns (uint256);

    /// @notice Returns the address of the underlying asset
    function asset() external view returns (address);

    /// @notice Converts a given amount of shares to underlying assets
    function convertToAssets(uint256 shares) external view returns (uint256);

    /// @notice Converts a given amount of underlying assets to shares
    function convertToShares(uint256 assets) external view returns (uint256);

    /// @notice Returns the total number of vault shares
    function totalSupply() external view returns (uint256);

    /// @notice Returns the share balance of an account
    function balanceOf(address account) external view returns (uint256);
}

/// @title IYearnV2Vault
/// @notice Interface for Yearn v2 vaults
/// @dev Detection: selector 0x99530b06 (pricePerShare)
/// @dev pricePerShare is scaled to the underlying token's decimals
interface IYearnV2Vault {
    function pricePerShare() external view returns (uint256);
    function totalAssets() external view returns (uint256);
    function token() external view returns (address);
    function balanceOf(address account) external view returns (uint256);
    function decimals() external view returns (uint256);
}

/// @title IBeefyVault
/// @notice Interface for Beefy Finance vaults
/// @dev getPricePerFullShare is always 1e18 scaled
interface IBeefyVault {
    function getPricePerFullShare() external view returns (uint256);
    function balance() external view returns (uint256);
    function want() external view returns (address);
    function balanceOf(address account) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

/// @title IConvexBooster
/// @notice Interface for Convex Finance Booster
/// @dev Mainnet: 0xF403C135812408BFbE8713b5A23a04b3D48AAE31
interface IConvexBooster {
    struct PoolInfo {
        address lptoken;
        address token;
        address gauge;
        address crvRewards;
        address stash;
        bool shutdown;
    }

    function poolInfo(uint256 pid) external view returns (PoolInfo memory);
    function poolLength() external view returns (uint256);
}

/// @title IConvexBaseRewardPool
/// @notice Interface for Convex BaseRewardPool contracts
interface IConvexBaseRewardPool {
    function balanceOf(address account) external view returns (uint256);
    function earned(address account) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}
