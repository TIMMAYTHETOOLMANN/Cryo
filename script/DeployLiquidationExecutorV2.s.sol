// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/liquidation/LiquidationExecutorV2.sol";

/**
 * @title DeployLiquidationExecutorV2
 * @notice Deploys enhanced executor with stats, multi-asset sweep, and chain-aware configuration
 * @dev Supports Ethereum, Arbitrum, Optimism, Base, Polygon, and Avalanche
 */
contract DeployLiquidationExecutorV2 is Script {
    // Aave V3 Pool addresses — same for Arbitrum, Optimism, Polygon, and Avalanche
    address constant AAVE_V3_ETHEREUM  = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant AAVE_V3_ARBITRUM  = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant AAVE_V3_OPTIMISM  = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant AAVE_V3_BASE      = 0xA238Dd80C259a72e81d7e4664a9801593F98d1c5;
    address constant AAVE_V3_POLYGON   = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant AAVE_V3_AVALANCHE = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;

    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer    = vm.addr(deployerKey);
        address treasury    = vm.envOr("TREASURY_ADDRESS", deployer);

        address aavePool = getAavePool(block.chainid);
        require(aavePool != address(0), string.concat("Unsupported chain for LiquidationExecutorV2: ", vm.toString(block.chainid)));

        console.log("=== LiquidationExecutorV2 Deployment ===");
        console.log("Deployer:", deployer);
        console.log("Treasury:", treasury);
        console.log("Chain ID:", block.chainid);
        console.log("Aave V3 Pool:", aavePool);
        console.log("");

        vm.startBroadcast(deployerKey);

        LiquidationExecutorV2 executor = new LiquidationExecutorV2(aavePool, treasury);

        // Configure supported debt assets per chain
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
            executor.addSupportedDebtAsset(0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f); // WBTC
        } else if (block.chainid == 10) {
            executor.addSupportedDebtAsset(0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85); // USDC (native)
            executor.addSupportedDebtAsset(0x7F5c764cBc14f9669B88837ca1490cCa17c31607); // USDC.e
            executor.addSupportedDebtAsset(0x94b008aA00579c1307B0EF2c499aD98a8ce58e58); // USDT
            executor.addSupportedDebtAsset(0x4200000000000000000000000000000000000006); // WETH
        } else if (block.chainid == 8453) {
            executor.addSupportedDebtAsset(0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913); // USDC
            executor.addSupportedDebtAsset(0x4200000000000000000000000000000000000006); // WETH
            executor.addSupportedDebtAsset(0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf); // cbBTC
        } else if (block.chainid == 137) {
            executor.addSupportedDebtAsset(0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359); // USDC (native)
            executor.addSupportedDebtAsset(0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174); // USDC.e
            executor.addSupportedDebtAsset(0xc2132D05D31c914a87C6611C10748AEb04B58e8F); // USDT
            executor.addSupportedDebtAsset(0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619); // WETH
        } else if (block.chainid == 43114) {
            executor.addSupportedDebtAsset(0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E); // USDC
            executor.addSupportedDebtAsset(0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7); // USDT
            executor.addSupportedDebtAsset(0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB); // WETH.e
        }

        vm.stopBroadcast();

        console.log("=== Deployed ===");
        console.log("LiquidationExecutorV2:", address(executor));
        console.log("Treasury:", treasury);
        console.log("Surplus Utilization: ENABLED");
        console.log("To set env: LIQUIDATION_EXECUTOR_V2=", address(executor));
    }

    function getAavePool(uint256 chainId) internal pure returns (address) {
        if (chainId == 1)     return AAVE_V3_ETHEREUM;
        if (chainId == 42161) return AAVE_V3_ARBITRUM;
        if (chainId == 10)    return AAVE_V3_OPTIMISM;
        if (chainId == 8453)  return AAVE_V3_BASE;
        if (chainId == 137)   return AAVE_V3_POLYGON;
        if (chainId == 43114) return AAVE_V3_AVALANCHE;
        return address(0);
    }
}
