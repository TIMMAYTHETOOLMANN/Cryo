// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "./CollateralizationDetector.sol";
import "./OracleIntegration.sol";
import "./interfaces/IChainlinkOracle.sol";
import "./interfaces/IAaveV3.sol";
import "./interfaces/IMulticall3.sol";

/// @title CrossChainDetector
/// @notice Multi-network over-collateralization detection that multiplies identification
///         capability by aggregating protocol scanning across supported chains
/// @dev Designed to work with Foundry's multi-fork testing (vm.createSelectFork)
///      and production multi-chain RPC endpoints. Uses Multicall3 (deployed on 250+ chains)
///      for efficient batch reads on each network.
///
///      Identification surface = protocolTypes × supportedNetworks
///      Single chain: 13 protocol types
///      Cross-chain:  13 protocol types × 5 networks = 65 identification targets
contract CrossChainDetector {
    using OracleIntegration for address;

    // --- Core reference ---
    CollateralizationDetector public immutable detector;

    // --- Structs ---

    /// @notice Configuration for a specific network's protocol deployment addresses
    struct NetworkConfig {
        uint256 chainId;
        string name;
        address aaveV3Pool;
        address compoundV3Comet;
        address multicall3;
        address sequencerUptimeFeed; // Zero for L1s
        bool isL2;
    }

    /// @notice Per-network scan result for a single target address
    struct NetworkScanResult {
        uint256 chainId;
        string networkName;
        CollateralizationDetector.ProtocolType detectedProtocol;
        uint256 collateralRatio;   // 1e18 scaled
        uint8 collateralLevel;     // 0-4 classification
        bool isOverCollateralized;
        bool sequencerUp;          // Always true for L1s
    }

    /// @notice Aggregated cross-chain position data
    struct CrossChainAggregation {
        uint256 totalNetworksScanned;
        uint256 networksWithOverCollateralization;
        uint256 highestCollateralRatio;  // Maximum ratio found across all networks
        uint256 lowestCollateralRatio;   // Minimum ratio (excluding zero/max)
        uint8 highestLevel;              // Highest classification level found
        uint256 totalTargetsIdentified;  // Sum of protocols identified across all networks
        NetworkScanResult[] results;
    }

    // --- Supported Chain IDs ---
    uint256 public constant CHAIN_ETHEREUM = 1;
    uint256 public constant CHAIN_ARBITRUM = 42161;
    uint256 public constant CHAIN_OPTIMISM = 10;
    uint256 public constant CHAIN_POLYGON = 137;
    uint256 public constant CHAIN_BASE = 8453;

    /// @notice Number of supported networks
    uint256 public constant SUPPORTED_NETWORKS = 5;

    /// @notice Number of protocol types (excluding Unknown)
    uint256 public constant PROTOCOL_TYPES = 13;

    // --- Standard Multicall3 address (same on most chains) ---
    address public constant MULTICALL3 = 0xcA11bde05977b3631167028862bE2a173976CA11;

    // --- L2 Sequencer Safety ---
    uint256 public constant L2_SEQUENCER_GRACE_PERIOD = 3600; // 1 hour

    // --- Errors ---
    error UnsupportedChain(uint256 chainId);
    error SequencerDown(uint256 chainId);
    error SequencerGracePeriod(uint256 chainId, uint256 timeSinceUp);

    constructor(address _detector) {
        detector = CollateralizationDetector(_detector);
    }

    // --- Network Configuration ---

    /// @notice Returns the network configuration for a supported chain
    /// @param chainId The chain ID to get configuration for
    /// @return config The network configuration struct
    function getNetworkConfig(uint256 chainId) public pure returns (NetworkConfig memory config) {
        config.chainId = chainId;
        config.multicall3 = MULTICALL3;

        if (chainId == CHAIN_ETHEREUM) {
            config.name = "Ethereum";
            config.aaveV3Pool = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
            config.compoundV3Comet = 0xc3d688B66703497DAA19211EEdff47f25384cdc3;
            config.isL2 = false;
        } else if (chainId == CHAIN_ARBITRUM) {
            config.name = "Arbitrum";
            config.aaveV3Pool = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
            config.compoundV3Comet = 0xA5EDBDD9646f8dFF606d7448e414884C7d905dCA;
            config.sequencerUptimeFeed = 0xFdB631F5EE196F0ed6FAa767959853A9F217697D;
            config.isL2 = true;
        } else if (chainId == CHAIN_OPTIMISM) {
            config.name = "Optimism";
            config.aaveV3Pool = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
            config.compoundV3Comet = 0x2e44e174f7D53F0212823acC11C01A11d58c5bCB;
            config.sequencerUptimeFeed = 0x371EAD81c9102C9BF4874A9075FFFf170F2Ee389;
            config.isL2 = true;
        } else if (chainId == CHAIN_POLYGON) {
            config.name = "Polygon";
            config.aaveV3Pool = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
            config.compoundV3Comet = 0xF25212E676D1F7F89Cd72fFEe66158f541246445;
            config.isL2 = false; // PoS chain, no sequencer
        } else if (chainId == CHAIN_BASE) {
            config.name = "Base";
            config.aaveV3Pool = 0xA238Dd80C259a72e81d7e4664a9801593F98d1c5;
            config.compoundV3Comet = 0xb125E6687d4313864e53df431d5425969c15Eb2F;
            config.sequencerUptimeFeed = 0xBCF85224fc0756B9Fa45aAb7d157a8263913aEcC;
            config.isL2 = true;
        } else {
            revert UnsupportedChain(chainId);
        }
    }

    /// @notice Returns all supported chain IDs
    /// @return chains Array of supported chain IDs
    function getSupportedChains() public pure returns (uint256[] memory chains) {
        chains = new uint256[](SUPPORTED_NETWORKS);
        chains[0] = CHAIN_ETHEREUM;
        chains[1] = CHAIN_ARBITRUM;
        chains[2] = CHAIN_OPTIMISM;
        chains[3] = CHAIN_POLYGON;
        chains[4] = CHAIN_BASE;
    }

    // --- L2 Sequencer Safety ---

    /// @notice Validates L2 sequencer uptime for safe oracle reads
    /// @dev Uses the Chainlink sequencer uptime feed (answer: 0 = up, 1 = down)
    /// @param sequencerFeed The L2 sequencer uptime feed address
    /// @return isUp Whether the sequencer is currently up
    /// @return timeSinceUp Seconds since the sequencer came back up
    function checkSequencerUptime(address sequencerFeed) public view returns (bool isUp, uint256 timeSinceUp) {
        if (sequencerFeed == address(0)) return (true, type(uint256).max); // L1, always "up"

        (, int256 answer, uint256 startedAt,,) = AggregatorV3Interface(sequencerFeed).latestRoundData();

        isUp = answer == 0; // Chainlink: 0 = up, 1 = down
        if (isUp && startedAt > 0) {
            timeSinceUp = block.timestamp - startedAt;
        }
    }

    /// @notice Validates that L2 sequencer is up and grace period has elapsed
    /// @dev Reverts if sequencer is down or grace period hasn't elapsed
    /// @param config The network configuration to validate
    /// @return safe True if oracle reads are safe on this network
    function validateSequencer(NetworkConfig memory config) public view returns (bool safe) {
        if (!config.isL2 || config.sequencerUptimeFeed == address(0)) return true;

        (bool isUp, uint256 timeSinceUp) = checkSequencerUptime(config.sequencerUptimeFeed);

        if (!isUp) revert SequencerDown(config.chainId);
        if (timeSinceUp < L2_SEQUENCER_GRACE_PERIOD) {
            revert SequencerGracePeriod(config.chainId, timeSinceUp);
        }

        return true;
    }

    // --- Protocol Scanning ---

    /// @notice Scans a single target contract on the current network
    /// @param target The contract address to scan
    /// @param chainId The chain ID of the current network
    /// @return result The scan result with protocol detection and sequencer status
    function scanTarget(address target, uint256 chainId) public view returns (NetworkScanResult memory result) {
        NetworkConfig memory config = getNetworkConfig(chainId);

        result.chainId = chainId;
        result.networkName = config.name;
        result.sequencerUp = true;

        // Check L2 sequencer if applicable
        if (config.isL2 && config.sequencerUptimeFeed != address(0)) {
            (bool isUp, uint256 timeSinceUp) = checkSequencerUptime(config.sequencerUptimeFeed);
            result.sequencerUp = isUp && timeSinceUp >= L2_SEQUENCER_GRACE_PERIOD;
        }

        // Classify the protocol type
        result.detectedProtocol = detector.classifyProtocol(target);
    }

    /// @notice Scans multiple target contracts on the current network
    /// @param targets Array of contract addresses to scan
    /// @param chainId The chain ID of the current network
    /// @return results Array of scan results
    function scanTargets(address[] calldata targets, uint256 chainId)
        external
        view
        returns (NetworkScanResult[] memory results)
    {
        results = new NetworkScanResult[](targets.length);
        for (uint256 i = 0; i < targets.length; i++) {
            results[i] = scanTarget(targets[i], chainId);
        }
    }

    // --- Batch Multicall3 Operations ---

    /// @notice Builds Multicall3 batch for scanning Aave V3 positions across multiple users
    /// @dev Works on any chain with Aave V3 deployment
    /// @param aaveV3Pool The Aave V3 pool address for the target chain
    /// @param users Array of user addresses to check
    /// @return calls Array of Multicall3 Call3 structs
    function buildAaveV3BatchCalls(
        address aaveV3Pool,
        address[] calldata users
    ) external pure returns (IMulticall3.Call3[] memory calls) {
        calls = new IMulticall3.Call3[](users.length);
        for (uint256 i = 0; i < users.length; i++) {
            calls[i] = IMulticall3.Call3({
                target: aaveV3Pool,
                allowFailure: true,
                callData: abi.encodeWithSelector(
                    IAaveV3Pool.getUserAccountData.selector,
                    users[i]
                )
            });
        }
    }

    /// @notice Builds a batch of protocol classification calls via Multicall3
    /// @param targets Array of contract addresses to classify
    /// @return calls Array of Multicall3 Call3 structs
    function buildClassifyBatch(
        address[] calldata targets
    ) external view returns (IMulticall3.Call3[] memory calls) {
        calls = new IMulticall3.Call3[](targets.length);
        for (uint256 i = 0; i < targets.length; i++) {
            calls[i] = IMulticall3.Call3({
                target: address(detector),
                allowFailure: true,
                callData: abi.encodeWithSelector(
                    CollateralizationDetector.classifyProtocol.selector,
                    targets[i]
                )
            });
        }
    }

    // --- Cross-Chain Aggregation ---

    /// @notice Aggregates scan results from multiple networks into a unified view
    /// @dev This is the core cross-chain integration function that combines per-network
    ///      scanning results into a single aggregated picture of over-collateralization
    /// @param results Array of NetworkScanResult from scanning multiple networks
    /// @return agg The aggregated cross-chain data
    function aggregateResults(NetworkScanResult[] memory results)
        public
        pure
        returns (CrossChainAggregation memory agg)
    {
        agg.results = results;
        agg.lowestCollateralRatio = type(uint256).max;

        uint256 networksWithOC;
        uint256 highestRatio;
        uint8 highestLevel;
        uint256 targetsIdentified;

        // Track unique chain IDs for totalNetworksScanned
        uint256[] memory seenChains = new uint256[](results.length);
        uint256 uniqueChains;

        for (uint256 i = 0; i < results.length; i++) {
            NetworkScanResult memory r = results[i];

            // Count unique networks
            bool chainSeen = false;
            for (uint256 j = 0; j < uniqueChains; j++) {
                if (seenChains[j] == r.chainId) {
                    chainSeen = true;
                    break;
                }
            }
            if (!chainSeen) {
                seenChains[uniqueChains] = r.chainId;
                uniqueChains++;
            }

            // Count identified targets (non-Unknown protocols)
            if (r.detectedProtocol != CollateralizationDetector.ProtocolType.Unknown) {
                targetsIdentified++;
            }

            // Track collateral ratios (exclude zero and max)
            if (r.collateralRatio > 0 && r.collateralRatio < type(uint256).max) {
                if (r.collateralRatio > highestRatio) {
                    highestRatio = r.collateralRatio;
                }
                if (r.collateralRatio < agg.lowestCollateralRatio) {
                    agg.lowestCollateralRatio = r.collateralRatio;
                }
            }

            // Track classification levels
            if (r.collateralLevel > highestLevel) {
                highestLevel = r.collateralLevel;
            }

            // Count over-collateralized positions
            if (r.isOverCollateralized) {
                networksWithOC++;
            }
        }

        agg.totalNetworksScanned = uniqueChains;
        agg.networksWithOverCollateralization = networksWithOC;
        agg.highestCollateralRatio = highestRatio;
        agg.highestLevel = highestLevel;
        agg.totalTargetsIdentified = targetsIdentified;

        // Reset lowest if nothing was found
        if (agg.lowestCollateralRatio == type(uint256).max) {
            agg.lowestCollateralRatio = 0;
        }
    }

    // --- Identification Capacity ---

    /// @notice Calculates the total target contract identification capacity
    /// @dev Cross-chain detection multiplies identification surface:
    ///      capacity = protocolTypes × supportedNetworks
    ///      This represents a multiplicative increase in identification capability
    ///      compared to single-chain detection
    /// @return capacity Total identification targets across all networks
    /// @return singleChainCapacity Identification targets on a single chain
    /// @return multiplier The cross-chain multiplication factor
    function identificationCapacity()
        public
        pure
        returns (uint256 capacity, uint256 singleChainCapacity, uint256 multiplier)
    {
        singleChainCapacity = PROTOCOL_TYPES;
        multiplier = SUPPORTED_NETWORKS;
        capacity = singleChainCapacity * multiplier;
    }
}
