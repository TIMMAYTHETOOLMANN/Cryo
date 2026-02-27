// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "./interfaces/IChainlinkOracle.sol";

/// @title OracleIntegration
/// @notice Chainlink oracle integration with staleness validation and safe price reads
/// @dev Implements the staleness checks described in the detection system spec:
///      Always validate (block.timestamp - updatedAt) < heartbeat and answer > 0
library OracleIntegration {
    /// @notice Thrown when oracle price data is stale
    error StaleOracleData(address feed, uint256 updatedAt, uint256 heartbeat);

    /// @notice Thrown when oracle returns zero or negative price
    error InvalidOraclePrice(address feed, int256 answer);

    /// @notice Common heartbeat intervals (in seconds) for major feeds
    /// @dev ETH/USD and BTC/USD: 3600s, stablecoins: 86400s
    uint256 public constant HEARTBEAT_ETH_USD = 3600;
    uint256 public constant HEARTBEAT_BTC_USD = 3600;
    uint256 public constant HEARTBEAT_STABLECOIN = 86400;

    /// @notice Reads a price from a Chainlink feed with staleness validation
    /// @param feed The Chainlink aggregator address
    /// @param maxStaleness Maximum acceptable seconds since last update
    /// @return price The price as uint256 (always positive after validation)
    /// @return decimals The number of decimals in the price
    function getValidatedPrice(address feed, uint256 maxStaleness)
        internal
        view
        returns (uint256 price, uint8 decimals)
    {
        AggregatorV3Interface oracle = AggregatorV3Interface(feed);

        (, int256 answer,, uint256 updatedAt,) = oracle.latestRoundData();

        if (answer <= 0) {
            revert InvalidOraclePrice(feed, answer);
        }

        if (block.timestamp - updatedAt > maxStaleness) {
            revert StaleOracleData(feed, updatedAt, maxStaleness);
        }

        decimals = oracle.decimals();
        price = uint256(answer);
    }

    /// @notice Reads a price without staleness validation (for testing/non-critical paths)
    /// @param feed The Chainlink aggregator address
    /// @return price The price as uint256
    /// @return feedDecimals The number of decimals
    function getPrice(address feed) internal view returns (uint256 price, uint8 feedDecimals) {
        AggregatorV3Interface oracle = AggregatorV3Interface(feed);

        (, int256 answer,,, ) = oracle.latestRoundData();

        if (answer <= 0) {
            revert InvalidOraclePrice(feed, answer);
        }

        feedDecimals = oracle.decimals();
        price = uint256(answer);
    }

    /// @notice Calculates the USD value of a token amount using a Chainlink feed
    /// @dev Uses 18-decimal fixed-point output to avoid precision loss
    /// @param tokenAmountRaw Raw token balance (in token's native decimals)
    /// @param tokenDecimals Number of decimals for the token (e.g., 6 for USDC, 18 for ETH)
    /// @param chainlinkPrice Raw price from Chainlink
    /// @param feedDecimals Number of decimals in the Chainlink feed (typically 8 for USD)
    /// @return valueUsd18 USD value scaled to 18 decimals
    function tokenValueUsd18(
        uint256 tokenAmountRaw,
        uint8 tokenDecimals,
        uint256 chainlinkPrice,
        uint8 feedDecimals
    ) internal pure returns (uint256 valueUsd18) {
        // Normalize: (amount / 10^tokenDec) * (price / 10^feedDec) * 1e18
        // = amount * price * 1e18 / (10^tokenDec * 10^feedDec)
        valueUsd18 = (tokenAmountRaw * chainlinkPrice * 1e18) / (10 ** tokenDecimals * 10 ** feedDecimals);
    }
}
