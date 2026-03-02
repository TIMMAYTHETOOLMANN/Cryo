// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/liquidation/FlashLoanArbitrageExecutor.sol";

/**
 * @title DeployFlashLoanArbitrage
 * @notice Deploys the Flash Loan Arbitrage Executor for Reserve Protocol
 */
contract DeployFlashLoanArbitrage is Script {
    // Aave V3 Pool addresses by chain
    address constant AAVE_V3_ETHEREUM = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant WETH_ETHEREUM    = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address constant UNISWAP_V3_ROUTER = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    
    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        
        console.log("=== Flash Loan Arbitrage Executor Deployment ===");
        console.log("Deployer:", deployer);
        console.log("Chain ID:", block.chainid);
        
        require(block.chainid == 1, "Only Ethereum Mainnet supported in this script");
        
        vm.startBroadcast(deployerKey);
        
        FlashLoanArbitrageExecutor executor = new FlashLoanArbitrageExecutor(
            deployer,
            AAVE_V3_ETHEREUM,
            WETH_ETHEREUM,
            UNISWAP_V3_ROUTER
        );
        
        vm.stopBroadcast();
        
        console.log("FlashLoanArbitrageExecutor deployed:", address(executor));
        console.log("Set FLASH_EXECUTOR environment variable to this address.");
    }
}
