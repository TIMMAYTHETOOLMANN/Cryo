// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title ICompoundV2Comptroller
/// @notice Interface for Compound v2 Comptroller
/// @dev Mainnet: 0x3d9819210A31b4961b30EF54bE2aeD79B9c9Cd3B
interface ICompoundV2Comptroller {
    /// @notice Returns the account liquidity
    /// @param account The address of the account
    /// @return error Error code (0 = no error)
    /// @return liquidity Account liquidity in excess of collateral requirements (> 0 means safe)
    /// @return shortfall Account shortfall below collateral requirements (> 0 means liquidatable)
    function getAccountLiquidity(address account)
        external
        view
        returns (uint256 error, uint256 liquidity, uint256 shortfall);

    /// @notice Returns the list of all markets (cTokens)
    function getAllMarkets() external view returns (address[] memory);

    /// @notice Returns the market data for a cToken
    /// @param cToken The cToken address
    /// @return isListed Whether the market is listed
    /// @return collateralFactorMantissa The collateral factor (scaled by 1e18)
    /// @return isComped Whether COMP is distributed
    function markets(address cToken)
        external
        view
        returns (bool isListed, uint256 collateralFactorMantissa, bool isComped);
}

/// @title ICToken
/// @notice Interface for Compound v2 cToken contracts
interface ICToken {
    /// @notice Returns the underlying balance of an account
    function balanceOfUnderlying(address owner) external returns (uint256);

    /// @notice Returns the borrow balance of an account (stored, not accrued)
    function borrowBalanceStored(address account) external view returns (uint256);

    /// @notice Returns the current borrow balance (with accrued interest)
    function borrowBalanceCurrent(address account) external returns (uint256);

    /// @notice Returns the address of the underlying asset
    function underlying() external view returns (address);

    /// @notice Returns the exchange rate from cToken to underlying
    function exchangeRateStored() external view returns (uint256);

    function balanceOf(address owner) external view returns (uint256);
    function decimals() external view returns (uint8);
}
