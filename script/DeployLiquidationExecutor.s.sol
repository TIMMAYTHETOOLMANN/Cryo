// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/liquidation/LiquidationExecutor.sol";

/**
 * @title DeployLiquidationExecutor
 * @notice Deploys the flash loan liquidation executor contract
 */
contract DeployLiquidationExecutor is Script {
    // Aave V3 Pool addresses by chain
    address constant AAVE_V3_ETHEREUM = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant AAVE_V3_ARBITRUM = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant AAVE_V3_OPTIMISM = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant AAVE_V3_BASE     = 0xA238Dd80C259a72e81d7e4664a9801593F98d1c5;
    address constant AAVE_V3_POLYGON  = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant AAVE_V3_AVALANCHE = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;

    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        address treasury = vm.envOr("TREASURY_ADDRESS", deployer);

        console.log("=== LiquidationExecutor Deployment ===");
        console.log("Deployer:", deployer);
        console.log("Treasury:", treasury);
        console.log("Chain ID:", block.chainid);
        console.log("Block:", block.number);
        console.log("");

        // Determine Aave pool address based on chain
        address aavePool = getAavePool(block.chainid);
        require(aavePool != address(0), "Unsupported chain");

        console.log("Aave V3 Pool:", aavePool);
        console.log("Treasury (profit recipient):", treasury);
        console.log("");

        // Deploy executor
        vm.startBroadcast(deployerKey);

        LiquidationExecutor executor = new LiquidationExecutor(
            aavePool,
            treasury
        );

        vm.stopBroadcast();

        console.log("LiquidationExecutor deployed:", address(executor));
        console.log("");

        // Configure supported debt assets
        console.log("Configuring supported debt assets...");

        vm.startBroadcast(deployerKey);

        // Add common debt assets
        if (block.chainid == 1) {
            executor.addSupportedDebtAsset(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48); // USDC
            executor.addSupportedDebtAsset(0xdAC17F958D2ee523a2206206994597C13D831ec7); // USDT
            executor.addSupportedDebtAsset(0x6B175474E89094C44Da98b954EedeAC495271d0F); // DAI
            executor.addSupportedDebtAsset(0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2); // WETH
            executor.addSupportedDebtAsset(0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599); // WBTC
            executor.addSupportedDebtAsset(0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0); // wstETH
        } else if (block.chainid == 42161) {
            executor.addSupportedDebtAsset(0xaf88d065e77c8cC2239327C5EDb3A432268e5831); // USDC (native)
            executor.addSupportedDebtAsset(0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8); // USDC.e
            executor.addSupportedDebtAsset(0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9); // USDT
            executor.addSupportedDebtAsset(0x82aF49447D8a07e3bd95BD0d56f35241523fBab1); // WETH
        } else if (block.chainid == 8453) {
            executor.addSupportedDebtAsset(0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913); // USDC
            executor.addSupportedDebtAsset(0x4200000000000000000000000000000000000006); // WETH
        }

        vm.stopBroadcast();

        console.log("Configuration complete!");
        console.log("");
        console.log("=== Deployment Summary ===");
        console.log("Executor:", address(executor));
        console.log("Treasury:", treasury);
        console.log("Aave Pool:", aavePool);
        console.log("");
        console.log("Next steps:");
        console.log("1. Fund wallet with ETH for gas");
        console.log("2. Run detector.py to find liquidatable positions");
        console.log("3. Execute liquidations via executor contract");
    }

    function getAavePool(uint256 chainId) internal pure returns (address) {
        if (chainId == 1) return AAVE_V3_ETHEREUM;
        if (chainId == 42161) return AAVE_V3_ARBITRUM;
        if (chainId == 10) return AAVE_V3_OPTIMISM;
        if (chainId == 8453) return AAVE_V3_BASE;
        if (chainId == 137) return AAVE_V3_POLYGON;
        if (chainId == 43114) return AAVE_V3_AVALANCHE;
        return address(0);
    }
}
