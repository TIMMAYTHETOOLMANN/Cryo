// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title LPPricing
/// @notice Manipulation-resistant LP token pricing using the Alpha Homora formula
/// @dev Naive LP valuation is vulnerable to flash loan manipulation.
///      The Alpha Homora fair pricing formula is manipulation-resistant:
///      fair_price = 2 * sqrt(k) * sqrt(p) / totalSupply
///      where k = reserve0 * reserve1, p = price0 * price1
library LPPricing {
    /// @notice Calculates the fair price of a Uniswap v2 LP token using the Alpha Homora formula
    /// @dev This is manipulation-resistant unlike naive (r0*p0 + r1*p1) / totalSupply
    /// @param reserve0 Reserve of token0
    /// @param reserve1 Reserve of token1
    /// @param price0_1e18 Price of token0 in USD scaled to 18 decimals
    /// @param price1_1e18 Price of token1 in USD scaled to 18 decimals
    /// @param totalSupply Total supply of LP tokens
    /// @return fairPrice_1e18 Fair LP token price in USD scaled to 18 decimals
    function fairLPPrice(
        uint256 reserve0,
        uint256 reserve1,
        uint256 price0_1e18,
        uint256 price1_1e18,
        uint256 totalSupply
    ) internal pure returns (uint256 fairPrice_1e18) {
        if (totalSupply == 0) return 0;

        // k = reserve0 * reserve1
        uint256 k = reserve0 * reserve1;

        // p = price0 * price1 (both in 1e18, so p is in 1e36)
        uint256 p = price0_1e18 * price1_1e18;

        // fair_price = 2 * sqrt(k) * sqrt(p) / totalSupply
        // sqrt(k * p) = sqrt(k) * sqrt(p)
        uint256 sqrtK = sqrt(k);
        uint256 sqrtP = sqrt(p);

        // Result: 2 * sqrtK * sqrtP / totalSupply
        // sqrtP is in sqrt(1e36) = 1e18, so result is in 1e18
        fairPrice_1e18 = (2 * sqrtK * sqrtP) / totalSupply;
    }

    /// @notice Calculates a safe lower bound for Curve LP token value
    /// @dev virtual_price × min(oracle_price_i)
    /// @dev Warning: get_virtual_price() is vulnerable to read-only reentrancy
    ///      during liquidity removal - verify reentrancy lock state before trusting it
    /// @param virtualPrice The virtual price from Curve pool (1e18 scaled)
    /// @param minUnderlyingPrice_1e18 The minimum oracle price among underlying tokens (1e18 scaled)
    /// @return lowerBound_1e18 Safe lower bound of LP token value in USD (1e18 scaled)
    function curveLPLowerBound(uint256 virtualPrice, uint256 minUnderlyingPrice_1e18)
        internal
        pure
        returns (uint256 lowerBound_1e18)
    {
        lowerBound_1e18 = (virtualPrice * minUnderlyingPrice_1e18) / 1e18;
    }

    /// @notice Integer square root using the Babylonian method
    /// @param x The value to take the square root of
    /// @return y The integer square root
    function sqrt(uint256 x) internal pure returns (uint256 y) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }
}
