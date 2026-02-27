// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IAaveV2LendingPool
/// @notice Interface for Aave v2 LendingPool - returns account data in ETH (18 decimals)
/// @dev Mainnet LendingPoolAddressesProvider: 0xB53C1a33016B2DC2fF3653530bfF1848a515c8c5
interface IAaveV2LendingPool {
    /// @notice Returns the user account data across all the reserves
    /// @param user The address of the user
    /// @return totalCollateralETH Total collateral in ETH (18 decimals)
    /// @return totalDebtETH Total debt in ETH (18 decimals)
    /// @return availableBorrowsETH Available borrows in ETH
    /// @return currentLiquidationThreshold Weighted liquidation threshold
    /// @return ltv Weighted loan-to-value
    /// @return healthFactor Health factor (< 1e18 means liquidatable)
    function getUserAccountData(address user)
        external
        view
        returns (
            uint256 totalCollateralETH,
            uint256 totalDebtETH,
            uint256 availableBorrowsETH,
            uint256 currentLiquidationThreshold,
            uint256 ltv,
            uint256 healthFactor
        );
}

/// @title IAaveV2AddressesProvider
/// @notice Interface for Aave v2 LendingPoolAddressesProvider
/// @dev Mainnet: 0xB53C1a33016B2DC2fF3653530bfF1848a515c8c5
interface IAaveV2AddressesProvider {
    function getLendingPool() external view returns (address);
}
