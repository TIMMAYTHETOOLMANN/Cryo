// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IMulticall3
/// @notice Interface for the Multicall3 contract deployed on 250+ chains
/// @dev Standard address: 0xcA11bde05977b3631167028862bE2a173976CA11
/// @dev zkSync address: 0xF9cda624FBC7e059355ce98a31693d299FACd963
/// @dev Conservative batch sizes: 50 calls on mainnet, 200-500 on L2s
interface IMulticall3 {
    struct Call3 {
        address target;
        bool allowFailure;
        bytes callData;
    }

    struct Result {
        bool success;
        bytes returnData;
    }

    /// @notice Aggregates calls, allowing failures
    /// @param calls Array of Call3 structs
    /// @return returnData Array of Result structs
    function aggregate3(Call3[] calldata calls) external payable returns (Result[] memory returnData);

    /// @notice Aggregates calls without allowing failures
    /// @param calls Array of target/calldata tuples
    /// @return blockNumber The block number of the aggregation
    /// @return returnData Array of return data bytes
    function aggregate(Call2[] calldata calls) external payable returns (uint256 blockNumber, bytes[] memory returnData);

    struct Call2 {
        address target;
        bytes callData;
    }

    /// @notice Returns the block number
    function getBlockNumber() external view returns (uint256 blockNumber);

    /// @notice Returns the current block timestamp
    function getCurrentBlockTimestamp() external view returns (uint256 timestamp);

    /// @notice Returns the ETH balance of an address
    function getEthBalance(address addr) external view returns (uint256 balance);
}
