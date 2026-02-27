// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IBalancerV2Vault
/// @notice Interface for Balancer v2 Vault
/// @dev Mainnet: 0xBA12222222228d8Ba445958a75a0704d566BF2C8
interface IBalancerV2Vault {
    /// @notice Returns the tokens and balances for a pool
    /// @param poolId The pool identifier
    /// @return tokens Array of token addresses
    /// @return balances Array of token balances
    /// @return lastChangeBlock Block number of last balance change
    function getPoolTokens(bytes32 poolId)
        external
        view
        returns (address[] memory tokens, uint256[] memory balances, uint256 lastChangeBlock);
}

/// @title IBalancerV2Pool
/// @notice Interface for Balancer v2 Pool
interface IBalancerV2Pool {
    function getPoolId() external view returns (bytes32);
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function getRate() external view returns (uint256);
}
