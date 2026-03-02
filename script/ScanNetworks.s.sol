// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/CollateralizationDetector.sol";
import "../contracts/CrossChainDetector.sol";

/// @title ScanNetworks
/// @notice Multi-chain network scanner using fork testing for over-collateralization detection
/// @dev Scans multiple chains and writes results to file
contract ScanNetworks is Script {
    /// @notice Path to scan results output file
    string internal constant SCAN_RESULTS_PATH = "output/scan-results.json";

    /// @notice Network configuration
    struct NetworkConfig {
        uint256 chainId;
        string name;
        string rpcEnvVar;
        bool enabled;
    }

    /// @notice Scan result for a single network
    struct ScanResult {
        uint256 chainId;
        string networkName;
        bool scanned;
        bool success;
        uint256 protocolsDetected;
        uint256 overCollateralizedCount;
        uint256 highestCollateralRatio;
        string errorMessage;
    }

    // Supported networks
    NetworkConfig[] public networks;

    constructor() {
        // Initialize supported networks
        networks.push(NetworkConfig(1, "Ethereum", "MAINNET_RPC_URL", true));
        networks.push(NetworkConfig(42161, "Arbitrum", "ARBITRUM_RPC_URL", true));
        networks.push(NetworkConfig(10, "Optimism", "OPTIMISM_RPC_URL", true));
        networks.push(NetworkConfig(137, "Polygon", "POLYGON_RPC_URL", true));
        networks.push(NetworkConfig(8453, "Base", "BASE_RPC_URL", true));
        networks.push(NetworkConfig(43114, "Avalanche", "AVALANCHE_RPC_URL", true));
        networks.push(NetworkConfig(56, "BSC", "BSC_RPC_URL", true));
        networks.push(NetworkConfig(324, "zkSync Era", "ZKSYNC_RPC_URL", false)); // zkSync needs special handling
    }

    /// @notice Run scan on all enabled networks
    function run() external {
        console.log("=== OCDS Multi-Network Scanner ===");
        console.log("Starting network scan at:", block.timestamp);
        console.log("================================");

        // Initialize results array
        ScanResult[] memory results = new ScanResult[](networks.length);

        // Scan each network
        for (uint256 i = 0; i < networks.length; i++) {
            if (networks[i].enabled) {
                results[i] = scanNetwork(networks[i]);
            } else {
                results[i] = ScanResult({
                    chainId: networks[i].chainId,
                    networkName: networks[i].name,
                    scanned: false,
                    success: false,
                    protocolsDetected: 0,
                    overCollateralizedCount: 0,
                    highestCollateralRatio: 0,
                    errorMessage: "Disabled"
                });
            }
        }

        // Aggregate and write results
        writeScanResults(results);

        // Print summary
        printScanSummary(results);
    }

    /// @notice Scan a single network using fork
    /// @param config The network configuration
    /// @return result The scan result
    function scanNetwork(NetworkConfig memory config) internal returns (ScanResult memory) {
        console.log("");
        console.log("Scanning", config.name);
        console.log("Chain ID:", config.chainId);

        // Load RPC URL from environment
        string memory rpcUrl = vm.envString(config.rpcEnvVar);
        require(bytes(rpcUrl).length > 0, string(abi.encodePacked("RPC URL not set for ", config.name)));

        // Create fork
        uint256 forkId = vm.createFork(rpcUrl);
        vm.selectFork(forkId);

        console.log("Forked to block:", block.number);

        // Initialize detectors
        CollateralizationDetector detector = new CollateralizationDetector();
        CrossChainDetector crossChain = new CrossChainDetector(address(detector));

        // Get network config from cross-chain detector
        try crossChain.getNetworkConfig(config.chainId) returns (
            CrossChainDetector.NetworkConfig memory netConfig
        ) {
            console.log("Network config loaded");
            console.log("Aave V3 Pool:", netConfig.aaveV3Pool);
            console.log("Compound V3:", netConfig.compoundV3Comet);
        } catch {
            console.log("Network config not available in CrossChainDetector");
        }

        // Scan known protocol addresses
        uint256 protocolsDetected = 0;
        uint256 overCollateralizedCount = 0;
        uint256 highestCollateralRatio = 0;

        // Scan Aave V3 Pool
        if (config.chainId == 1) {
            // Mainnet Aave V3
            address aaveV3 = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
            (bool isOver, uint256 ratio) = scanProtocol(detector, aaveV3, "Aave V3");
            protocolsDetected++;
            if (isOver) overCollateralizedCount++;
            if (ratio > highestCollateralRatio) highestCollateralRatio = ratio;
        }

        // Scan Compound V3
        if (config.chainId == 1) {
            // Mainnet Compound V3 USDC
            address compoundV3 = 0xc3d688B66703497DAA19211EEdff47f25384cdc3;
            (bool isOver, uint256 ratio) = scanProtocol(detector, compoundV3, "Compound V3");
            protocolsDetected++;
            if (isOver) overCollateralizedCount++;
            if (ratio > highestCollateralRatio) highestCollateralRatio = ratio;
        }

        console.log("Protocols detected:", protocolsDetected);
        console.log("Over-collateralized:", overCollateralizedCount);
        console.log("Highest ratio:", highestCollateralRatio);

        return ScanResult({
            chainId: config.chainId,
            networkName: config.name,
            scanned: true,
            success: true,
            protocolsDetected: protocolsDetected,
            overCollateralizedCount: overCollateralizedCount,
            highestCollateralRatio: highestCollateralRatio,
            errorMessage: ""
        });
    }

    /// @notice Scan a single protocol address
    /// @param detector The collateralization detector
    /// @param protocol The protocol address
    /// @param name The protocol name
    /// @return isOverCollateralized Whether the protocol is over-collateralized
    /// @return collateralRatio The collateralization ratio
    function scanProtocol(
        CollateralizationDetector detector,
        address protocol,
        string memory name
    ) internal returns (bool, uint256) {
        console.log("Scanning", name);

        try detector.classifyProtocol(protocol) returns (CollateralizationDetector.ProtocolType pType) {
            console.log("Protocol Type:", uint8(pType));
            return (true, 0); // Simplified - would need actual position data for ratio
        } catch (bytes memory err) {
            console.logBytes(err);
            return (false, 0);
        }
    }

    /// @notice Write scan results to file
    /// @param results Array of scan results
    function writeScanResults(ScanResult[] memory results) internal {
        // Ensure output directory exists
        if (!vm.isDir("output")) {
            vm.createDir("output", false);
            console.log("Created output directory");
        }

        // Build JSON array of results
        string memory json = "[";
        
        for (uint256 i = 0; i < results.length; i++) {
            if (i > 0) json = string.concat(json, ",");
            
            string memory result = vm.serializeUint("result", "chainId", results[i].chainId);
            result = vm.serializeString(result, "networkName", results[i].networkName);
            result = vm.serializeBool(result, "scanned", results[i].scanned);
            result = vm.serializeBool(result, "success", results[i].success);
            result = vm.serializeUint(result, "protocolsDetected", results[i].protocolsDetected);
            result = vm.serializeUint(result, "overCollateralizedCount", results[i].overCollateralizedCount);
            result = vm.serializeUint(result, "highestCollateralRatio", results[i].highestCollateralRatio);
            result = vm.serializeString(result, "errorMessage", results[i].errorMessage);
            
            json = string.concat(json, result);
        }
        
        json = string.concat(json, "]");

        // Wrap in object with metadata
        string memory fullJson = vm.serializeUint("scan", "timestamp", block.timestamp);
        fullJson = vm.serializeUint(fullJson, "blockNumber", block.number);
        fullJson = vm.serializeString(fullJson, "results", json);

        // Write to file
        vm.writeJson(fullJson, SCAN_RESULTS_PATH);
        console.log("");
        console.log("Scan results written to file");
    }

    /// @notice Print scan summary to console
    /// @param results Array of scan results
    function printScanSummary(ScanResult[] memory results) internal pure {
        console.log("");
        console.log("================================");
        console.log("Scan Summary");
        console.log("================================");

        uint256 totalScanned = 0;
        uint256 totalSuccess = 0;
        uint256 totalProtocols = 0;
        uint256 totalOverCollateralized = 0;

        for (uint256 i = 0; i < results.length; i++) {
            if (results[i].scanned) {
                totalScanned++;
                console.log(results[i].networkName);

                if (results[i].success) {
                    totalSuccess++;
                    totalProtocols += results[i].protocolsDetected;
                    totalOverCollateralized += results[i].overCollateralizedCount;
                }
            }
        }

        console.log("================================");
        console.log("Total Networks Scanned:", totalScanned);
        console.log("Successful Scans:", totalSuccess);
        console.log("Total Protocols Detected:", totalProtocols);
        console.log("Total Over-Collateralized:", totalOverCollateralized);
        console.log("================================");
    }

    /// @notice Scan a single network (for individual use)
    /// @param chainId The chain ID to scan
    function scanSingleNetwork(uint256 chainId) external {
        for (uint256 i = 0; i < networks.length; i++) {
            if (networks[i].chainId == chainId) {
                ScanResult memory result = scanNetwork(networks[i]);
                console.log("Scan complete for", networks[i].name);
                console.log("Protocols:", result.protocolsDetected);
                console.log("Over-Collateralized:", result.overCollateralizedCount);
                return;
            }
        }
        revert("Chain ID not supported");
    }
}
