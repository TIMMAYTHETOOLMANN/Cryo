// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title AggregatorV3Interface
/// @notice Standard Chainlink price feed interface
/// @dev ETH/USD mainnet: 0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419 (8 decimals)
/// @dev BTC/USD mainnet: 0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c (8 decimals)
interface AggregatorV3Interface {
    /// @notice Returns the latest round data
    /// @return roundId The round ID
    /// @return answer The price answer (check decimals())
    /// @return startedAt Timestamp when the round started
    /// @return updatedAt Timestamp when the answer was last updated
    /// @return answeredInRound The round ID in which the answer was computed
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);

    /// @notice Returns the number of decimals in the response
    function decimals() external view returns (uint8);

    /// @notice Returns the description of the feed
    function description() external view returns (string memory);

    /// @notice Returns the version of the aggregator
    function version() external view returns (uint256);
}

/// @title IChainlinkFeedRegistry
/// @notice Interface for Chainlink Feed Registry
/// @dev Mainnet: 0x47Fb2585D2C56Fe188D0E6ec628a38b74fCeeeDf
interface IChainlinkFeedRegistry {
    /// @notice Returns the latest round data for a base/quote pair
    function latestRoundData(address base, address quote)
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);

    /// @notice Returns the feed address for a base/quote pair
    function getFeed(address base, address quote) external view returns (address);

    /// @notice Returns the number of decimals for a base/quote pair
    function decimals(address base, address quote) external view returns (uint8);
}
