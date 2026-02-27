// SPDX-License-Identifier: MIT
pragma solidity ^0.8.13;

import "forge-std/Test.sol";
import "forge-std/console.sol";

contract ReserveRecon is Test {

    address constant RSR_TOKEN = 0x320623b8E4fF03373931769A31Fc52A4E78B5d70;
    address constant ETH_PLUS = 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8;
    address constant E_USD = 0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F;

    function setUp() public {
        vm.createSelectFork(vm.rpcUrl("mainnet"));
        console.log("=== RESERVE PROTOCOL RECONNAISSANCE ===");
        console.log("Block:", block.number);
        console.log("Timestamp:", block.timestamp);
        console.log("---");
    }

    function testDiscoverContracts() public view {
        console.log("=== KEY CONTRACT ADDRESSES ===");
        console.log("RSR Token:", RSR_TOKEN);
        console.log("ETH+ Token:", ETH_PLUS);
        console.log("eUSD Token:", E_USD);
        console.log("");

        console.log("=== RTOKEN SUPPLIES ===");
        console.log("ETH+ Total Supply:", IRToken(ETH_PLUS).totalSupply() / 1e18, "tokens");
        console.log("ETH+ Baskets Needed:", IRToken(ETH_PLUS).basketsNeeded() / 1e18, "baskets");
        console.log("eUSD Total Supply:", IRToken(E_USD).totalSupply() / 1e18, "tokens");
        console.log("eUSD Baskets Needed:", IRToken(E_USD).basketsNeeded() / 1e18, "baskets");
        console.log("");

        // Check for over-collateralization
        uint256 ethPlusSupply = IRToken(ETH_PLUS).totalSupply();
        uint256 ethPlusNeeded = IRToken(ETH_PLUS).basketsNeeded();
        uint256 ethPlusExcess = ethPlusNeeded > ethPlusSupply ? ethPlusNeeded - ethPlusSupply : 0;

        uint256 eUsdSupply = IRToken(E_USD).totalSupply();
        uint256 eUsdNeeded = IRToken(E_USD).basketsNeeded();
        uint256 eUsdExcess = eUsdNeeded > eUsdSupply ? eUsdNeeded - eUsdSupply : 0;

        console.log("=== OVER-COLLATERALIZATION ANALYSIS ===");
        console.log("ETH+ Excess Baskets:", ethPlusExcess / 1e18);
        console.log("ETH+ Over-collateralization:", ethPlusSupply > 0 ? (ethPlusExcess * 10000) / ethPlusSupply : 0, "bps");

        console.log("eUSD Excess Baskets:", eUsdExcess / 1e18);
        console.log("eUSD Over-collateralization:", eUsdSupply > 0 ? (eUsdExcess * 10000) / eUsdSupply : 0, "bps");

        console.log("");
        console.log("=== VULNERABILITY STATUS ===");
        if (ethPlusExcess > 0) {
            console.log("[VULNERABLE] ETH+ - Over-collateralized mint/redeem possible");
            console.log("Potential Profit:", (ethPlusExcess * 1800e18) / 1e18, "USD");
        } else {
            console.log("[SECURE] ETH+ - No over-collateralization");
        }

        if (eUsdExcess > 0) {
            console.log("[VULNERABLE] eUSD - Over-collateralized mint/redeem possible");
            console.log("Potential Profit:", (eUsdExcess * 1800e18) / 1e18, "USD");
        } else {
            console.log("[SECURE] eUSD - No over-collateralization");
        }
    }
}

interface IRToken {
    function totalSupply() external view returns (uint256);
    function basketsNeeded() external view returns (uint256);
}