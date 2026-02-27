// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IUniswapV2Pair
/// @notice Interface for Uniswap v2 pairs (also used by SushiSwap forks)
/// @dev Detection: selector 0x0902f1ac (getReserves)
interface IUniswapV2Pair {
    /// @notice Returns the reserves and last block timestamp
    function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    /// @notice Returns the total supply of LP tokens
    function totalSupply() external view returns (uint256);

    /// @notice Returns the address of token0
    function token0() external view returns (address);

    /// @notice Returns the address of token1
    function token1() external view returns (address);

    function balanceOf(address owner) external view returns (uint256);
}

/// @title IUniswapV2Factory
/// @notice Interface for Uniswap v2 Factory
/// @dev Mainnet: 0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f
/// @dev PairCreated event topic0: 0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9
interface IUniswapV2Factory {
    /// @notice Returns the total number of pairs
    function allPairsLength() external view returns (uint256);

    /// @notice Returns the pair address at a given index
    function allPairs(uint256 index) external view returns (address);

    /// @notice Returns the pair address for two tokens
    function getPair(address tokenA, address tokenB) external view returns (address pair);
}
