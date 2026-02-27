// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title ICurvePool
/// @notice Interface for Curve pools (Vyper-based)
/// @dev Detection: selector 0x07a2d13a (get_virtual_price)
/// @dev Warning: get_virtual_price() is vulnerable to read-only reentrancy during liquidity removal
interface ICurvePool {
    /// @notice Returns the balance of a coin at index i
    function balances(uint256 i) external view returns (uint256);

    /// @notice Returns the address of a coin at index i
    function coins(uint256 i) external view returns (address);

    /// @notice Returns the virtual price of the LP token (1e18 scaled)
    /// @dev Represents LP token's value relative to underlying pegged assets
    function get_virtual_price() external view returns (uint256);

    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
}
