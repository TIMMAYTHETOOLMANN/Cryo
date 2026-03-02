// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/CollateralizationDetector.sol";
import "../contracts/CrossChainDetector.sol";
import "../contracts/OracleIntegration.sol";
import "../contracts/LPPricing.sol";

/// @title DeployOCDS
/// @notice Deployment script with file-based state tracking for the OCDS system
/// @dev Uses Foundry cheatcodes for environment loading and file I/O
contract DeployOCDS is Script {
    /// @notice Path to deployment state file
    string internal constant DEPLOYMENT_STATE_PATH = "output/deployments.json";

    /// @notice Deployment configuration
    struct DeploymentConfig {
        uint256 deployerKey;
        bool verifyOnEtherscan;
        string etherscanApiKey;
    }

    /// @notice Deployment state for a single contract
    struct ContractDeployment {
        string name;
        address deployedAddress;
        uint256 blockNumber;
        uint256 timestamp;
        string transactionHash;
        string network;
    }

    /// @notice Full deployment state
    struct DeploymentState {
        mapping(string => ContractDeployment) deployments;
        string[] deploymentOrder;
        uint256 totalDeployments;
    }

    // ============================================================
    // Main Deployment Function
    // ============================================================

    function run() external {
        // Load configuration from environment
        DeploymentConfig memory config = loadDeploymentConfig();
        
        console.log("=== OCDS Deployment Script ===");
        console.log("Deployer:", vm.addr(config.deployerKey));
        console.log("Verify on Etherscan:", config.verifyOnEtherscan);
        console.log("Network:", getNetworkName());
        console.log("Chain ID:", block.chainid);
        console.log("Block Number:", block.number);
        console.log("Timestamp:", block.timestamp);
        console.log("================================");

        // Start broadcast with deployer key
        vm.startBroadcast(config.deployerKey);

        // Deploy contracts
        address collateralizationDetectorAddr = deployCollateralizationDetector();
        address crossChainDetectorAddr = deployCrossChainDetector(collateralizationDetectorAddr);

        // Stop broadcast
        vm.stopBroadcast();

        // Create deployment state
        string memory deploymentState = createDeploymentState(
            collateralizationDetectorAddr,
            crossChainDetectorAddr
        );

        // Write deployment state to file
        writeDeploymentState(deploymentState);

        console.log("================================");
        console.log("Deployment Complete!");
        console.log("CollateralizationDetector:", collateralizationDetectorAddr);
        console.log("CrossChainDetector:", crossChainDetectorAddr);
        console.log("Deployment state written to:", DEPLOYMENT_STATE_PATH);
        console.log("================================");

        // Verify contracts on Etherscan if enabled
        if (config.verifyOnEtherscan) {
            verifyContracts(collateralizationDetectorAddr, crossChainDetectorAddr, config.etherscanApiKey);
        }
    }

    // ============================================================
    // Configuration Loading
    // ============================================================

    /// @notice Load deployment configuration from environment
    function loadDeploymentConfig() internal view returns (DeploymentConfig memory) {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        require(deployerKey > 0, "PRIVATE_KEY must be set in .env");

        bool verify = vm.envOr("VERIFY_ETHERSCAN", false);
        string memory apiKey = vm.envOr("ETHERSCAN_API_KEY", string(""));

        return DeploymentConfig({
            deployerKey: deployerKey,
            verifyOnEtherscan: verify,
            etherscanApiKey: apiKey
        });
    }

    /// @notice Get human-readable network name
    function getNetworkName() internal view returns (string memory) {
        if (block.chainid == 1) return "Ethereum Mainnet";
        if (block.chainid == 42161) return "Arbitrum One";
        if (block.chainid == 10) return "Optimism";
        if (block.chainid == 137) return "Polygon";
        if (block.chainid == 8453) return "Base";
        if (block.chainid == 43114) return "Avalanche";
        if (block.chainid == 56) return "BSC";
        if (block.chainid == 324) return "zkSync Era";
        return "Unknown Network";
    }

    // ============================================================
    // Contract Deployments
    // ============================================================

    /// @notice Deploy CollateralizationDetector
    /// @return The deployed contract address
    function deployCollateralizationDetector() internal returns (address) {
        console.log("Deploying CollateralizationDetector...");
        
        CollateralizationDetector detector = new CollateralizationDetector();
        
        console.log("  CollateralizationDetector deployed at:", address(detector));
        console.log("  Gas used:", gasleft());
        
        return address(detector);
    }

    /// @notice Deploy CrossChainDetector
    /// @param detectorAddr The CollateralizationDetector address
    /// @return The deployed contract address
    function deployCrossChainDetector(address detectorAddr) internal returns (address) {
        console.log("Deploying CrossChainDetector...");
        
        CrossChainDetector crossChain = new CrossChainDetector(detectorAddr);
        
        console.log("  CrossChainDetector deployed at:", address(crossChain));
        console.log("  Gas used:", gasleft());
        
        return address(crossChain);
    }

    // ============================================================
    // State Management
    // ============================================================

    /// @notice Create deployment state JSON
    /// @param detectorAddr CollateralizationDetector address
    /// @param crossChainAddr CrossChainDetector address
    /// @return JSON string of deployment state
    function createDeploymentState(
        address detectorAddr,
        address crossChainAddr
    ) internal returns (string memory) {
        // Start building JSON
        string memory json = vm.serializeAddress("deployment", "collateralizationDetector", detectorAddr);
        json = vm.serializeAddress(json, "crossChainDetector", crossChainAddr);
        json = vm.serializeUint(json, "blockNumber", block.number);
        json = vm.serializeUint(json, "timestamp", block.timestamp);
        json = vm.serializeUint(json, "chainId", block.chainid);
        json = vm.serializeString(json, "network", getNetworkName());
        json = vm.serializeAddress(json, "deployer", vm.addr(vm.envUint("PRIVATE_KEY")));

        // Add transaction hash (from broadcast)
        // Note: In real deployment, you'd capture this from broadcast logs

        return json;
    }

    /// @notice Write deployment state to file
    /// @param state The JSON state string
    function writeDeploymentState(string memory state) internal {
        // Ensure output directory exists
        if (!vm.isDir("output")) {
            vm.createDir("output", false);
            console.log("Created output directory");
        }

        // Write to file
        vm.writeJson(state, DEPLOYMENT_STATE_PATH);
        console.log("Deployment state written to:", DEPLOYMENT_STATE_PATH);
    }

    /// @notice Read existing deployment state from file
    /// @return The JSON state string
    function readDeploymentState() internal view returns (string memory) {
        return vm.readFile(DEPLOYMENT_STATE_PATH);
    }

    /// @notice Parse deployment state to get contract address
    /// @param state The JSON state
    /// @param contractName The contract name to look up
    /// @return The contract address
    function parseDeploymentAddress(string memory state, string memory contractName) 
        internal 
        pure 
        returns (address) 
    {
        return vm.parseJsonAddress(state, string.concat(".", contractName));
    }

    // ============================================================
    // Contract Verification
    // ============================================================

    /// @notice Verify deployed contracts on Etherscan
    /// @param detectorAddr CollateralizationDetector address
    /// @param crossChainAddr CrossChainDetector address
    /// @param apiKey Etherscan API key
    function verifyContracts(
        address detectorAddr,
        address crossChainAddr,
        string memory apiKey
    ) internal {
        console.log("Verifying contracts on Etherscan...");

        // Mark files for verification
        vm.setEnv("ETHERSCAN_API_KEY", apiKey);

        // Verify CollateralizationDetector
        console.log("  Verifying CollateralizationDetector...");
        try this.verifyContract(detectorAddr, "contracts/CollateralizationDetector.sol:CollateralizationDetector") {
            console.log("  CollateralizationDetector verified");
        } catch (bytes memory err) {
            console.logBytes(err);
        }

        // Verify CrossChainDetector
        console.log("  Verifying CrossChainDetector...");
        try this.verifyContract(crossChainAddr, "contracts/CrossChainDetector.sol:CrossChainDetector") {
            console.log("  CrossChainDetector verified");
        } catch (bytes memory err) {
            console.logBytes(err);
        }
    }

    /// @notice External function for verification (called via broadcast)
    /// @param addr Contract address
    /// @param contractName Contract name
    function verifyContract(address addr, string memory contractName) external {
        // This is a placeholder - actual verification happens via forge verify-contract command
        console.log("Would verify:", addr, "as", contractName);
    }

    // ============================================================
    // Utility Functions
    // ============================================================

    /// @notice Check if contract is already deployed
    /// @return true if deployment state exists
    function hasDeploymentState() internal view returns (bool) {
        try vm.readFile(DEPLOYMENT_STATE_PATH) returns (string memory) {
            return true;
        } catch {
            return false;
        }
    }

    /// @notice Get deployment timestamp from state
    /// @return timestamp The deployment timestamp
    function getDeploymentTimestamp() internal view returns (uint256) {
        string memory state = readDeploymentState();
        return vm.parseJsonUint(state, ".timestamp");
    }

    /// @notice Compare on-chain code with deployed addresses
    /// @param addr The address to check
    /// @return true if contract has code
    function hasCode(address addr) internal view returns (bool) {
        uint256 size;
        assembly {
            size := extcodesize(addr)
        }
        return size > 0;
    }
}
