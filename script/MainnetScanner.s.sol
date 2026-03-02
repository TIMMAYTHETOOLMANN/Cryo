// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../contracts/CollateralizationDetector.sol";
import "../contracts/interfaces/IReserveProtocol.sol";

/**
 * @title MainnetScanner
 * @notice Production mainnet scanner for over-collateralization detection and execution.
 *         Replaces all POC scripts with a single production-grade system that can:
 *         1. Scan Reserve Protocol RTokens for over-collateralization
 *         2. Calculate profitability (gas cost vs profit)
 *         3. Acquire collateral via Uniswap V3
 *         4. Execute mint/redeem arbitrage cycles
 *         5. Log detailed profit analysis
 *
 * @dev Run modes:
 *   - Scan only:  forge script script/MainnetScanner.s.sol:MainnetScanner --rpc-url $MAINNET_RPC_URL -vvv
 *   - Execute:    forge script script/MainnetScanner.s.sol:MainnetScanner --rpc-url $MAINNET_RPC_URL --private-key $PRIVATE_KEY --broadcast -vvv
 */
contract MainnetScanner is Script {
    // --- Known Reserve Protocol RToken Addresses ---
    address constant ETH_PLUS = 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8;
    address constant E_USD    = 0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F;

    // --- Infrastructure ---
    address constant WETH              = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address constant UNISWAP_V3_ROUTER = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    uint24  constant POOL_FEE          = 3000; // 0.3%

    // --- Execution Constants ---
    uint256 constant GAS_ESTIMATE          = 500000;   // Conservative gas estimate for full cycle
    uint256 constant SWAP_BUFFER_BPS       = 500;      // 5% buffer for collateral acquisition
    uint256 constant SLIPPAGE_TOLERANCE_BPS = 500;     // 5% slippage tolerance on DEX swaps
    uint256 constant SWAP_DEADLINE_SECONDS = 300;      // 5 minute deadline for swaps

    // --- Flash Loan Arbitrage Executor ---
    address public flashExecutor;

    // --- Configurable Thresholds ---
    uint256 public minProfitWei;
    uint256 public gasPriceCapGwei;

    address[] public rTokens;
    mapping(address => bool) public isDiscovered;

    CollateralizationDetector public detector;

    function run() external {
        // --- Load Configuration ---
        minProfitWei    = vm.envOr("MIN_PROFIT_WEI", uint256(10000000000000000)); // 0.01 ETH default
        gasPriceCapGwei = vm.envOr("GAS_PRICE_CAP_GWEI", uint256(50));
        flashExecutor   = vm.envOr("FLASH_EXECUTOR", address(0));

        // --- Deploy detector for analysis ---
        detector = new CollateralizationDetector();

        console.log("=== OCDS Production Mainnet Scanner ===");
        console.log("Block:", block.number);
        console.log("Timestamp:", block.timestamp);
        console.log("Min profit threshold:", minProfitWei, "wei");
        console.log("Gas price cap:", gasPriceCapGwei, "gwei");
        console.log("");

        // --- Phase 1: Reconnaissance (scan all known RTokens) ---
        console.log("=== Phase 1: Reserve Protocol Reconnaissance ===");
        
        // Initial seeds
        _addRToken(ETH_PLUS);
        _addRToken(E_USD);
        _addRToken(0xac3E018457B222d93114458476f3E3416Abbe38F); // sfrxETH
        _addRToken(0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0); // wstETH
        _addRToken(0xae78736Cd615f374D3085123A210448E74Fc6393); // rETH

        // Adaptive discovery
        // _discoverMore();

        for (uint256 i = 0; i < rTokens.length; i++) {
            _scanRToken("RToken", rTokens[i]);
        }
        console.log("");

        // --- Phase 2: Profitability Analysis ---
        console.log("=== Phase 2: Profitability Analysis ===");
        bool[] memory opportunities = new bool[](rTokens.length);
        bool anyOpportunity = false;

        for (uint256 i = 0; i < rTokens.length; i++) {
            // Use generic price of 1.0 for discovery, actual production should use oracles
            opportunities[i] = _analyzeProfitability("RToken", rTokens[i], 1e18);
            if (opportunities[i]) anyOpportunity = true;
        }
        console.log("");

        // --- Phase 3: Execution (only if profitable and private key is available) ---
        if (anyOpportunity) {
            console.log("=== Phase 3: Execution ===");

            // Check if we have a private key for broadcast
            try vm.envUint("PRIVATE_KEY") returns (uint256 deployerKey) {
                address executor = vm.addr(deployerKey);
                console.log("Executor address:", executor);

                for (uint256 i = 0; i < rTokens.length; i++) {
                    if (opportunities[i]) {
                        _executeArbitrage(rTokens[i], deployerKey, executor);
                    }
                }
            } catch {
                console.log("No PRIVATE_KEY set - scan-only mode. Skipping execution.");
            }
        } else {
            console.log("=== No profitable opportunities found ===");
        }

        console.log("");
        console.log("=== Scan Complete ===");
    }

    function _addRToken(address rToken) internal {
        if (rToken != address(0) && !isDiscovered[rToken]) {
            rTokens.push(rToken);
            isDiscovered[rToken] = true;
        }
    }

    function _discoverMore() internal {
        console.log("  Running adaptive discovery...");
        uint256 initialCount = rTokens.length;
        
        // Dynamic discovery from known seeds
        for (uint256 i = 0; i < initialCount; i++) {
            address[] memory discovered = detector.discoverRTokens(rTokens[i], 3); // Deeper discovery
            for (uint256 j = 0; j < discovered.length; j++) {
                _addRToken(discovered[j]);
            }
        }
        
        if (rTokens.length > initialCount) {
            console.log("    Discovered", rTokens.length - initialCount, "new RTokens.");
        } else {
            console.log("    No new RTokens discovered.");
        }
    }

    /// @notice Scans an RToken for over-collateralization status
    function _scanRToken(string memory name, address rToken) internal view {
        CollateralizationDetector.RTokenPositionData memory data = detector.getRTokenPosition(rToken);
        CollateralizationDetector.RTokenConfiguration memory config = detector.getRTokenConfiguration(rToken);

        console.log("---", name, "---");
        console.log("  Address:", rToken);
        console.log("  Total Supply:", data.totalSupply / 1e18, "tokens");
        console.log("  Baskets Needed:", data.basketsNeeded / 1e18, "baskets");

        if (config.isAdaptiveReconEnabled) {
            console.log("  [ADAPTIVE RECONNAISSANCE DATA]");
            console.log("    Main:", config.main);
            console.log("    Basket Status:", config.basketStatus == 0 ? "SOUND" : "DANGER/DISABLED");
            console.log("    Backing Status:", config.backingStatus == 0 ? "SOUND" : "DANGER/DISABLED");
            console.log("    Registered Assets:", config.allRegisteredAssets.length);
        }

        if (data.isOverCollateralized) {
            console.log("  [OVER-COLLATERALIZED]");
            console.log("  Excess Baskets:", data.excessBaskets / 1e18);
            console.log("  Collateral Ratio:", data.collateralRatio * 100 / 1e18, "%");
            console.log("  Profit Per Token:", data.profitPerToken * 10000 / 1e18, "bps");

            if (data.collateralTokens.length > 0) {
                console.log("  Collateral composition (%s tokens):", data.collateralTokens.length);
                for (uint256 i = 0; i < data.collateralTokens.length; i++) {
                    console.log("    Token:", data.collateralTokens[i]);
                    console.log("    Amount:", data.collateralAmounts[i]);
                }
            }
        } else {
            console.log("  [ADEQUATELY COLLATERALIZED]");
        }
    }

    /// @notice Analyzes the profitability of exploiting an over-collateralized RToken
    function _analyzeProfitability(
        string memory name,
        address rToken,
        uint256 basketValueUsd
    ) internal view returns (bool isProfitable) {
        (
            uint256 excessBaskets,
            uint256 profitBps,
            uint256 totalProfitUsd,
            bool isOpportunity
        ) = detector.analyzeRTokenProfitability(rToken, basketValueUsd);

        console.log("---", name, "---");

        if (!isOpportunity) {
            console.log("  No opportunity: not over-collateralized");
            return false;
        }

        console.log("  Excess baskets:", excessBaskets / 1e18);
        console.log("  Profit:", profitBps, "bps");
        console.log("  Total profit estimate: $", totalProfitUsd / 1e18);

        // Gas cost analysis
        uint256 gasCostWei = GAS_ESTIMATE * gasPriceCapGwei * 1e9;

        // Get collateral amounts for rough profit estimate in ETH terms
        CollateralizationDetector.RTokenPositionData memory data = detector.getRTokenPosition(rToken);
        uint256 estimatedProfitWei = 0;
        for (uint256 i = 0; i < data.collateralAmounts.length; i++) {
            estimatedProfitWei += (data.collateralAmounts[i] * profitBps) / 10000;
        }

        console.log("  Estimated profit (wei):", estimatedProfitWei);
        console.log("  Gas cost estimate (wei):", gasCostWei);

        isProfitable = estimatedProfitWei > gasCostWei + minProfitWei;
        if (isProfitable) {
            console.log("  [PROFITABLE] Executing...");
        } else {
            console.log("  [NOT PROFITABLE] Profit does not exceed gas + minimum threshold.");
        }
    }

    /// @notice Executes the mint/redeem arbitrage cycle on an over-collateralized RToken
    function _executeArbitrage(address rToken, uint256 deployerKey, address executor) internal {
        if (flashExecutor != address(0)) {
            console.log("  Using Flash Loan Arbitrage Executor:", flashExecutor);
            vm.startBroadcast(deployerKey);
            // Calculate required flash loan amount (WETH)
            // Increased to 2 ETH to be safe for collateral acquisition
            uint256 wethNeeded = 2e18;

            (bool success,) = flashExecutor.call{gas: 2000000}(
                abi.encodeWithSignature("executeArbitrage(address,uint256)", rToken, wethNeeded)
            );
            require(success, "Flash loan arbitrage failed");
            vm.stopBroadcast();
            return;
        }

        IRToken token = IRToken(rToken);

        // Get collateral requirements
        address mainAddr = token.main();
        address basketHandler = IMain(mainAddr).basketHandler();
        (address[] memory tokens, uint256[] memory amounts) =
            IBasketHandler(basketHandler).quote(uint192(1e18), 0);

        vm.startBroadcast(deployerKey);

        // Swap ETH → collateral tokens via Uniswap V3
        uint256 totalEthNeeded = 0;
        for (uint256 i = 0; i < amounts.length; i++) {
            totalEthNeeded += amounts[i];
        }
        totalEthNeeded = (totalEthNeeded * (10000 + SWAP_BUFFER_BPS)) / 10000;

        IWETH(WETH).deposit{value: totalEthNeeded}();
        IERC20Minimal(WETH).approve(UNISWAP_V3_ROUTER, totalEthNeeded);

        for (uint256 i = 0; i < tokens.length; i++) {
            if (tokens[i] == WETH) continue;

            ISwapRouter.ExactInputSingleParams memory params = ISwapRouter.ExactInputSingleParams({
                tokenIn: WETH,
                tokenOut: tokens[i],
                fee: POOL_FEE,
                recipient: executor,
                deadline: block.timestamp + SWAP_DEADLINE_SECONDS,
                amountIn: amounts[i],
                amountOutMinimum: (amounts[i] * (10000 - SLIPPAGE_TOLERANCE_BPS)) / 10000,
                sqrtPriceLimitX96: 0
            });
            ISwapRouter(UNISWAP_V3_ROUTER).exactInputSingle(params);
        }

        // Approve collateral to RToken
        for (uint256 i = 0; i < tokens.length; i++) {
            IERC20Minimal(tokens[i]).approve(rToken, amounts[i]);
        }

        // Record pre-mint balances
        uint256[] memory preMintBalances = new uint256[](tokens.length);
        for (uint256 i = 0; i < tokens.length; i++) {
            preMintBalances[i] = IERC20Minimal(tokens[i]).balanceOf(executor);
        }

        // Mint
        token.issue(1e18);
        uint256 minted = token.balanceOf(executor);
        console.log("  Minted:", minted / 1e18, "RTokens");

        // Redeem
        token.redeem(minted);

        // Calculate profit
        console.log("  === Profit Summary ===");
        for (uint256 i = 0; i < tokens.length; i++) {
            uint256 postBal = IERC20Minimal(tokens[i]).balanceOf(executor);
            uint256 gained = postBal > preMintBalances[i] ? postBal - preMintBalances[i] : 0;
            console.log("  Token:", tokens[i]);
            console.log("    Gained:", gained);
        }
        console.log("  === Execution Complete ===");

        vm.stopBroadcast();
    }
}
