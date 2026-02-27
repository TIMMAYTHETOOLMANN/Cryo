// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import "./contracts/CollateralizationDetector.sol";
import "./contracts/LPPricing.sol";
import "./contracts/OracleIntegration.sol";
import "./contracts/interfaces/IReserveProtocol.sol";

/// @title CollateralizationDetectorTest
/// @notice Tests for the multi-chain DeFi over-collateralization detection system
/// @dev Tests are split into unit tests (no fork needed) and fork tests (mainnet data)
contract CollateralizationDetectorTest is Test {
    CollateralizationDetector public detector;

    // --- Known mainnet addresses ---
    address constant AAVE_V2_POOL = 0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9;
    address constant AAVE_V3_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant COMPOUND_V2_COMPTROLLER = 0x3d9819210A31b4961b30EF54bE2aeD79B9c9Cd3B;
    address constant COMPOUND_V3_USDC = 0xc3d688B66703497DAA19211EEdff47f25384cdc3;
    address constant CHAINLINK_ETH_USD = 0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419;
    address constant CHAINLINK_BTC_USD = 0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c;
    address constant UNISWAP_V2_FACTORY = 0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f;
    address constant MAKER_VAT = 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B;

    function setUp() public {
        detector = new CollateralizationDetector();
    }

    // ============================================================
    // Unit Tests (no fork required)
    // ============================================================

    function testCollateralizationThresholds() public view {
        assertEq(detector.AT_RISK(), 1.2e18);
        assertEq(detector.WELL_COLLATERALIZED(), 2e18);
        assertEq(detector.SIGNIFICANTLY_OVER_COLLATERALIZED(), 3e18);
        assertEq(detector.EXTREMELY_CONSERVATIVE(), 5e18);
        assertEq(detector.EXTREME_OUTLIER(), 10e18);
    }

    function testClassifyCollateralization() public view {
        // Under-collateralized (< 100%)
        assertEq(detector.classifyCollateralization(0.5e18), 0);
        assertEq(detector.classifyCollateralization(0), 0);

        // At-risk (100-120%)
        assertEq(detector.classifyCollateralization(1e18), 1);
        assertEq(detector.classifyCollateralization(1.1e18), 1);

        // Normal (120-200%)
        assertEq(detector.classifyCollateralization(1.2e18), 2);
        assertEq(detector.classifyCollateralization(1.5e18), 2);

        // Well-collateralized (200-300%)
        assertEq(detector.classifyCollateralization(2e18), 3);
        assertEq(detector.classifyCollateralization(2.5e18), 3);

        // Significantly over-collateralized (300-500%)
        assertEq(detector.classifyCollateralization(3e18), 4);
        assertEq(detector.classifyCollateralization(4e18), 4);

        // Extremely conservative (500-1000%)
        assertEq(detector.classifyCollateralization(5e18), 5);
        assertEq(detector.classifyCollateralization(8e18), 5);

        // Extreme outlier (> 1000%)
        assertEq(detector.classifyCollateralization(10e18), 6);
        assertEq(detector.classifyCollateralization(50e18), 6);
    }

    function testFuzzClassifyCollateralization(uint256 ratio) public view {
        uint8 level = detector.classifyCollateralization(ratio);
        assertTrue(level <= 6);

        if (ratio < 1e18) assertEq(level, 0);
        else if (ratio < 1.2e18) assertEq(level, 1);
        else if (ratio < 2e18) assertEq(level, 2);
        else if (ratio < 3e18) assertEq(level, 3);
        else if (ratio < 5e18) assertEq(level, 4);
        else if (ratio < 10e18) assertEq(level, 5);
        else assertEq(level, 6);
    }

    // ============================================================
    // LP Pricing Unit Tests
    // ============================================================

    function testFairLPPrice() public pure {
        // Example: ETH-USDC pool
        // reserve0 = 1000 ETH (1e21 in wei)
        // reserve1 = 2,000,000 USDC (2e12 in 6-decimal)
        // price0 = $2000 (2000e18)
        // price1 = $1 (1e18)
        // totalSupply = 1e18 (1 LP token for simplicity)
        uint256 fairPrice = LPPricing.fairLPPrice(
            1000e18,   // reserve0
            2000000e6, // reserve1
            2000e18,   // price0 in 1e18
            1e18,      // price1 in 1e18
            1e18       // totalSupply
        );

        // Fair price should be approximately 2 * sqrt(1000e18 * 2000000e6) * sqrt(2000e18 * 1e18) / 1e18
        // This is a large number, just verify it's > 0 and reasonable
        assertGt(fairPrice, 0, "Fair LP price should be positive");
    }

    function testFairLPPriceZeroSupply() public pure {
        uint256 fairPrice = LPPricing.fairLPPrice(100, 100, 1e18, 1e18, 0);
        assertEq(fairPrice, 0, "Zero supply should return zero price");
    }

    function testSqrt() public pure {
        assertEq(LPPricing.sqrt(0), 0);
        assertEq(LPPricing.sqrt(1), 1);
        assertEq(LPPricing.sqrt(4), 2);
        assertEq(LPPricing.sqrt(9), 3);
        assertEq(LPPricing.sqrt(16), 4);
        assertEq(LPPricing.sqrt(100), 10);
        assertEq(LPPricing.sqrt(1e18), 1e9);
        assertEq(LPPricing.sqrt(1e36), 1e18);
    }

    function testFuzzSqrt(uint128 x) public pure {
        uint256 y = LPPricing.sqrt(uint256(x));
        // y * y <= x < (y+1) * (y+1)
        assertLe(y * y, uint256(x), "sqrt underflow");
        assertLt(uint256(x), (y + 1) * (y + 1), "sqrt overflow");
    }

    function testCurveLPLowerBound() public pure {
        // virtual_price = 1.02e18 (2% above peg)
        // min underlying price = $0.995 (995e15 in 1e18)
        uint256 bound = LPPricing.curveLPLowerBound(1.02e18, 0.995e18);
        // Expected: 1.02 * 0.995 = ~1.0149 in 1e18
        assertGt(bound, 1e18, "Lower bound should be > $1");
        assertLt(bound, 1.02e18, "Lower bound should be < virtual price");
    }

    // ============================================================
    // Oracle Integration Unit Tests
    // ============================================================

    function testTokenValueUsd18() public pure {
        // 1 ETH ($2000) with 18 decimals and 8-decimal feed
        uint256 value = OracleIntegration.tokenValueUsd18(
            1e18,       // 1 ETH
            18,         // ETH has 18 decimals
            200000000000, // $2000 with 8 decimals
            8
        );
        assertEq(value, 2000e18, "1 ETH at $2000 = $2000");

        // 1000 USDC ($1) with 6 decimals and 8-decimal feed
        value = OracleIntegration.tokenValueUsd18(
            1000e6,     // 1000 USDC
            6,          // USDC has 6 decimals
            100000000,  // $1 with 8 decimals
            8
        );
        assertEq(value, 1000e18, "1000 USDC at $1 = $1000");

        // 1 WBTC ($40000) with 8 decimals and 8-decimal feed
        value = OracleIntegration.tokenValueUsd18(
            1e8,           // 1 WBTC
            8,             // WBTC has 8 decimals
            4000000000000, // $40000 with 8 decimals
            8
        );
        assertEq(value, 40000e18, "1 WBTC at $40000 = $40000");
    }

    function testTokenValueUsd18PrecisionEdgeCases() public pure {
        // Very small amount: 1 wei of USDC (0.000001 USDC)
        uint256 value = OracleIntegration.tokenValueUsd18(
            1,          // 1 raw unit (0.000001 USDC)
            6,
            100000000,  // $1
            8
        );
        // 0.000001 * 1 * 1e18 / (1e6 * 1e8) = 1e18 / 1e14 = 1e4 = 10000
        // This represents $0.000001 in 18-decimal form: 1e12
        assertEq(value, 1e12, "Smallest USDC unit value");
    }

    // ============================================================
    // Multicall3 Builder Tests
    // ============================================================

    function testBuildAaveV2Call() public view {
        address testPool = address(0x1234);
        address testUser = address(0x5678);

        IMulticall3.Call3 memory call = detector.buildAaveV2Call(testPool, testUser);

        assertEq(call.target, testPool);
        assertTrue(call.allowFailure);
        assertEq(
            call.callData,
            abi.encodeWithSelector(IAaveV2LendingPool.getUserAccountData.selector, testUser)
        );
    }

    function testBuildCompoundV2Call() public view {
        address testComptroller = address(0xABCD);
        address testAccount = address(0xEF01);

        IMulticall3.Call3 memory call = detector.buildCompoundV2Call(testComptroller, testAccount);

        assertEq(call.target, testComptroller);
        assertTrue(call.allowFailure);
        assertEq(
            call.callData,
            abi.encodeWithSelector(ICompoundV2Comptroller.getAccountLiquidity.selector, testAccount)
        );
    }

    // ============================================================
    // Reserve Protocol RToken Detection Tests
    // ============================================================

    function testGetRTokenPositionOverCollateralized() public {
        address mockRToken = address(0xBEEF);
        address mockMain = address(0xCA11);
        address mockBasketHandler = address(0xBA5E);

        // Mock totalSupply: 1000 tokens
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.totalSupply.selector),
            abi.encode(uint256(1000e18))
        );

        // Mock basketsNeeded: 1059 baskets (5.9% over-collateralized, matching POC)
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.basketsNeeded.selector),
            abi.encode(uint192(1059e18))
        );

        // Mock main()
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.main.selector),
            abi.encode(mockMain)
        );

        // Mock basketHandler()
        vm.mockCall(
            mockMain,
            abi.encodeWithSelector(IMain.basketHandler.selector),
            abi.encode(mockBasketHandler)
        );

        // Mock quote for 1e18 baskets
        address[] memory tokens = new address[](2);
        tokens[0] = address(0x1111);
        tokens[1] = address(0x2222);
        uint256[] memory amounts = new uint256[](2);
        amounts[0] = 0.5e18;
        amounts[1] = 0.5e18;
        vm.mockCall(
            mockBasketHandler,
            abi.encodeWithSelector(IBasketHandler.quote.selector),
            abi.encode(tokens, amounts)
        );

        CollateralizationDetector.RTokenPositionData memory data = detector.getRTokenPosition(mockRToken);

        assertEq(data.totalSupply, 1000e18, "Total supply should be 1000");
        assertEq(data.basketsNeeded, 1059e18, "Baskets needed should be 1059");
        assertTrue(data.isOverCollateralized, "Should be over-collateralized");
        assertEq(data.excessBaskets, 59e18, "Excess baskets should be 59");
        assertGt(data.collateralRatio, 1e18, "Collateral ratio should exceed 100%");
        assertGt(data.profitPerToken, 0, "Profit per token should be positive");
        assertEq(data.collateralTokens.length, 2, "Should have 2 collateral tokens");
        assertEq(data.collateralAmounts.length, 2, "Should have 2 collateral amounts");
    }

    function testGetRTokenPositionNotOverCollateralized() public {
        address mockRToken = address(0xBEEF);

        // Mock totalSupply: 1000 tokens
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.totalSupply.selector),
            abi.encode(uint256(1000e18))
        );

        // Mock basketsNeeded: 1000 baskets (exactly collateralized)
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.basketsNeeded.selector),
            abi.encode(uint192(1000e18))
        );

        // Mock main() - will revert, but that's fine for quote
        vm.mockCallRevert(
            mockRToken,
            abi.encodeWithSelector(IRToken.main.selector),
            "no main"
        );

        CollateralizationDetector.RTokenPositionData memory data = detector.getRTokenPosition(mockRToken);

        assertEq(data.totalSupply, 1000e18);
        assertEq(data.basketsNeeded, 1000e18);
        assertFalse(data.isOverCollateralized);
        assertEq(data.excessBaskets, 0);
    }

    function testAnalyzeRTokenProfitability() public {
        address mockRToken = address(0xBEEF);

        // Mock totalSupply: 60400000 tokens (matching POC block state)
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.totalSupply.selector),
            abi.encode(uint256(60_400_000e18))
        );

        // Mock basketsNeeded: 63966000 (5.9% over, matching POC)
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.basketsNeeded.selector),
            abi.encode(uint192(63_966_000e18))
        );

        (
            uint256 excessBaskets,
            uint256 profitBps,
            uint256 totalProfitUsd,
            bool isOpportunity
        ) = detector.analyzeRTokenProfitability(mockRToken, 1800e18);

        assertTrue(isOpportunity, "Should detect opportunity");
        assertEq(excessBaskets, 3_566_000e18, "Excess baskets should match");
        assertGt(profitBps, 500, "Profit should exceed 500 bps (~5.9%)");
        assertGt(totalProfitUsd, 6_000_000e18, "Total profit should exceed $6M");
    }

    function testAnalyzeRTokenNoProfitability() public {
        address mockRToken = address(0xBEEF);

        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.totalSupply.selector),
            abi.encode(uint256(1000e18))
        );

        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.basketsNeeded.selector),
            abi.encode(uint192(900e18)) // Under-collateralized
        );

        (,,, bool isOpportunity) = detector.analyzeRTokenProfitability(mockRToken, 1800e18);
        assertFalse(isOpportunity, "Should not detect opportunity when under-collateralized");
    }

    function testClassifyProtocolReserve() public {
        address mockRToken = address(0xBEEF);

        // Mock basketsNeeded() to succeed
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.basketsNeeded.selector),
            abi.encode(uint192(1000e18))
        );

        // Mock main() to succeed
        vm.mockCall(
            mockRToken,
            abi.encodeWithSelector(IRToken.main.selector),
            abi.encode(address(0xCA11))
        );

        CollateralizationDetector.ProtocolType pType = detector.classifyProtocol(mockRToken);
        assertEq(uint8(pType), uint8(CollateralizationDetector.ProtocolType.ReserveProtocol));
    }
}
