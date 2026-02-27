// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IAaveV3Pool
/// @notice Interface for Aave v3 Pool - returns account data in base currency USD (8 decimals)
/// @dev Mainnet PoolAddressesProvider: 0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e
/// @dev Also used by Radiant Capital (Arbitrum/BNB) and Benqi (Avalanche) forks
interface IAaveV3Pool {
    /// @notice Returns the user account data across all the reserves
    /// @param user The address of the user
    /// @return totalCollateralBase Total collateral in base currency USD (8 decimals)
    /// @return totalDebtBase Total debt in base currency USD (8 decimals)
    /// @return availableBorrowsBase Available borrows in base currency
    /// @return currentLiquidationThreshold Weighted liquidation threshold
    /// @return ltv Weighted loan-to-value
    /// @return healthFactor Health factor (< 1e18 means liquidatable)
    function getUserAccountData(address user)
        external
        view
        returns (
            uint256 totalCollateralBase,
            uint256 totalDebtBase,
            uint256 availableBorrowsBase,
            uint256 currentLiquidationThreshold,
            uint256 ltv,
            uint256 healthFactor
        );
}

/// @title IAaveV3AddressesProvider
/// @notice Interface for Aave v3 PoolAddressesProvider
/// @dev Mainnet: 0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e
interface IAaveV3AddressesProvider {
    function getPool() external view returns (address);
    function getAddress(bytes32 id) external view returns (address);
}

/// @title IAaveV3AddressesProviderRegistry
/// @notice Interface for Aave v3 PoolAddressesProviderRegistry
/// @dev Mainnet: 0xbaA999AC55EAce41CcAE355c77809e68Bb345170
interface IAaveV3AddressesProviderRegistry {
    function getAddressesProvidersList() external view returns (address[] memory);
}
