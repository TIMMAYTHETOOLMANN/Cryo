// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/liquidation/LiquidationExecutorV2.sol";

/**
 * @title DeployLiquidationExecutorV2
 * @notice Deploys enhanced executor with surplus utilization
 */
contract DeployLiquidationExecutorV2 is Script {
    address constant AAVE_V3_ETHEREUM = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    
    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        address treasury = vm.envOr("TREASURY_ADDRESS", deployer);

        console.log("=== LiquidationExecutorV2 Deployment ===");
        console.log("Deployer:", deployer);
        console.log("Treasury:", treasury);
        console.log("Chain ID:", block.chainid);
        console.log("");
        
        vm.startBroadcast(deployerKey);
        
        LiquidationExecutorV2 executor = new LiquidationExecutorV2(
            AAVE_V3_ETHEREUM,
            treasury
        );
        
        // Major debt assets
        executor.addSupportedDebtAsset(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48); // USDC
        executor.addSupportedDebtAsset(0xdAC17F958D2ee523a2206206994597C13D831ec7); // USDT
        executor.addSupportedDebtAsset(0x6B175474E89094C44Da98b954EedeAC495271d0F); // DAI
        executor.addSupportedDebtAsset(0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2); // WETH
        executor.addSupportedDebtAsset(0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599); // WBTC
        executor.addSupportedDebtAsset(0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0); // wstETH

        vm.stopBroadcast();
        
        console.log("=== Deployed ===");
        console.log("Executor V2:", address(executor));
        console.log("Treasury:", treasury);
        console.log("Surplus Utilization: ENABLED");
        console.log("Assets: USDC, USDT, DAI, WETH, WBTC, wstETH");
    }
}
