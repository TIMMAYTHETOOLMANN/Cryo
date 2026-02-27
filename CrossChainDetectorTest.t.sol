// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import "./contracts/CrossChainDetector.sol";
import "./contracts/CollateralizationDetector.sol";

/// @title CrossChainDetectorTest
/// @notice Unit tests for the cross-chain over-collateralization detection system
/// @dev All tests run without fork; mocking is used for on-chain interactions
contract CrossChainDetectorTest is Test {
    CrossChainDetector public crossChain;
    CollateralizationDetector public detector;

    // Standard Multicall3 address
    address constant MULTICALL3 = 0xcA11bde05977b3631167028862bE2a173976CA11;

    function setUp() public {
        detector = new CollateralizationDetector();
        crossChain = new CrossChainDetector(address(detector));
    }

    // ============================================================
    // Chain ID Constants
    // ============================================================

    function testChainConstants() public view {
        assertEq(crossChain.CHAIN_ETHEREUM(), 1);
        assertEq(crossChain.CHAIN_ARBITRUM(), 42161);
        assertEq(crossChain.CHAIN_OPTIMISM(), 10);
        assertEq(crossChain.CHAIN_POLYGON(), 137);
        assertEq(crossChain.CHAIN_BASE(), 8453);
        assertEq(crossChain.SUPPORTED_NETWORKS(), 5);
        assertEq(crossChain.PROTOCOL_TYPES(), 13);
    }

    // ============================================================
    // Network Configuration Tests
    // ============================================================

    function testGetEthereumConfig() public view {
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(1);
        assertEq(config.chainId, 1);
        assertEq(keccak256(bytes(config.name)), keccak256("Ethereum"));
        assertEq(config.aaveV3Pool, 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2);
        assertEq(config.compoundV3Comet, 0xc3d688B66703497DAA19211EEdff47f25384cdc3);
        assertEq(config.multicall3, MULTICALL3);
        assertEq(config.sequencerUptimeFeed, address(0));
        assertFalse(config.isL2);
    }

    function testGetArbitrumConfig() public view {
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(42161);
        assertEq(config.chainId, 42161);
        assertEq(keccak256(bytes(config.name)), keccak256("Arbitrum"));
        assertEq(config.aaveV3Pool, 0x794a61358D6845594F94dc1DB02A252b5b4814aD);
        assertEq(config.compoundV3Comet, 0xA5EDBDD9646f8dFF606d7448e414884C7d905dCA);
        assertEq(config.multicall3, MULTICALL3);
        assertTrue(config.sequencerUptimeFeed != address(0));
        assertTrue(config.isL2);
    }

    function testGetOptimismConfig() public view {
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(10);
        assertEq(config.chainId, 10);
        assertEq(keccak256(bytes(config.name)), keccak256("Optimism"));
        assertTrue(config.aaveV3Pool != address(0));
        assertTrue(config.compoundV3Comet != address(0));
        assertTrue(config.sequencerUptimeFeed != address(0));
        assertTrue(config.isL2);
    }

    function testGetPolygonConfig() public view {
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(137);
        assertEq(config.chainId, 137);
        assertEq(keccak256(bytes(config.name)), keccak256("Polygon"));
        assertTrue(config.aaveV3Pool != address(0));
        assertTrue(config.compoundV3Comet != address(0));
        assertEq(config.sequencerUptimeFeed, address(0)); // No sequencer for PoS
        assertFalse(config.isL2);
    }

    function testGetBaseConfig() public view {
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(8453);
        assertEq(config.chainId, 8453);
        assertEq(keccak256(bytes(config.name)), keccak256("Base"));
        assertTrue(config.aaveV3Pool != address(0));
        assertTrue(config.compoundV3Comet != address(0));
        assertTrue(config.sequencerUptimeFeed != address(0));
        assertTrue(config.isL2);
    }

    function testUnsupportedChainReverts() public {
        vm.expectRevert(abi.encodeWithSelector(CrossChainDetector.UnsupportedChain.selector, 999));
        crossChain.getNetworkConfig(999);
    }

    function testAllSupportedChainsHaveValidConfigs() public view {
        uint256[] memory chains = crossChain.getSupportedChains();
        assertEq(chains.length, 5);

        for (uint256 i = 0; i < chains.length; i++) {
            CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(chains[i]);
            assertTrue(config.aaveV3Pool != address(0), "Aave V3 pool should be set");
            assertTrue(config.compoundV3Comet != address(0), "Compound V3 should be set");
            assertEq(config.multicall3, MULTICALL3, "Multicall3 should be standard address");
        }
    }

    // ============================================================
    // Supported Chains Tests
    // ============================================================

    function testGetSupportedChains() public view {
        uint256[] memory chains = crossChain.getSupportedChains();
        assertEq(chains.length, 5);
        assertEq(chains[0], 1);     // Ethereum
        assertEq(chains[1], 42161); // Arbitrum
        assertEq(chains[2], 10);    // Optimism
        assertEq(chains[3], 137);   // Polygon
        assertEq(chains[4], 8453);  // Base
    }

    // ============================================================
    // Identification Capacity Tests
    // ============================================================

    function testIdentificationCapacity() public view {
        (uint256 capacity, uint256 singleChain, uint256 multiplier) = crossChain.identificationCapacity();
        assertEq(singleChain, 13, "13 protocol types");
        assertEq(multiplier, 5, "5 supported networks");
        assertEq(capacity, 65, "65 total identification targets (13 x 5)");
        assertGt(capacity, singleChain, "Cross-chain capacity exceeds single-chain");
    }

    // ============================================================
    // L2 Sequencer Safety Tests
    // ============================================================

    function testCheckSequencerUptimeZeroAddress() public view {
        // L1 chains have zero sequencer feed → always up
        (bool isUp, uint256 timeSinceUp) = crossChain.checkSequencerUptime(address(0));
        assertTrue(isUp, "L1 should always report sequencer up");
        assertEq(timeSinceUp, type(uint256).max, "L1 should report max time since up");
    }

    function testCheckSequencerUptimeWithMock() public {
        // Deploy a mock sequencer feed
        address mockFeed = address(0xFEED);

        // Mock latestRoundData: sequencer is UP (answer = 0), startedAt = block.timestamp - 7200
        vm.mockCall(
            mockFeed,
            abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
            abi.encode(uint80(1), int256(0), block.timestamp - 7200, block.timestamp - 100, uint80(1))
        );

        (bool isUp, uint256 timeSinceUp) = crossChain.checkSequencerUptime(mockFeed);
        assertTrue(isUp, "Sequencer should be up when answer is 0");
        assertEq(timeSinceUp, 7200, "Should be 7200 seconds since sequencer came up");
    }

    function testCheckSequencerDownWithMock() public {
        address mockFeed = address(0xFEED);

        // Mock latestRoundData: sequencer is DOWN (answer = 1)
        vm.mockCall(
            mockFeed,
            abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
            abi.encode(uint80(1), int256(1), block.timestamp - 100, block.timestamp - 50, uint80(1))
        );

        (bool isUp,) = crossChain.checkSequencerUptime(mockFeed);
        assertFalse(isUp, "Sequencer should be down when answer is 1");
    }

    function testValidateSequencerL1() public view {
        // L1 config (Ethereum) should always pass
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(1);
        bool safe = crossChain.validateSequencer(config);
        assertTrue(safe, "L1 should always be safe");
    }

    function testValidateSequencerL2Up() public {
        // Mock Arbitrum sequencer feed as UP with grace period elapsed
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(42161);

        vm.mockCall(
            config.sequencerUptimeFeed,
            abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
            abi.encode(uint80(1), int256(0), block.timestamp - 7200, block.timestamp - 100, uint80(1))
        );

        bool safe = crossChain.validateSequencer(config);
        assertTrue(safe, "L2 with up sequencer past grace period should be safe");
    }

    function testValidateSequencerL2DownReverts() public {
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(42161);

        // Mock sequencer DOWN
        vm.mockCall(
            config.sequencerUptimeFeed,
            abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
            abi.encode(uint80(1), int256(1), block.timestamp - 100, block.timestamp - 50, uint80(1))
        );

        vm.expectRevert(abi.encodeWithSelector(CrossChainDetector.SequencerDown.selector, 42161));
        crossChain.validateSequencer(config);
    }

    function testValidateSequencerGracePeriodReverts() public {
        CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(42161);

        // Mock sequencer UP but only 600 seconds ago (less than 3600s grace period)
        vm.mockCall(
            config.sequencerUptimeFeed,
            abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
            abi.encode(uint80(1), int256(0), block.timestamp - 600, block.timestamp - 100, uint80(1))
        );

        vm.expectRevert(
            abi.encodeWithSelector(CrossChainDetector.SequencerGracePeriod.selector, 42161, 600)
        );
        crossChain.validateSequencer(config);
    }

    // ============================================================
    // Aggregation Tests
    // ============================================================

    function testAggregateEmptyResults() public view {
        CrossChainDetector.NetworkScanResult[] memory empty = new CrossChainDetector.NetworkScanResult[](0);
        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(empty);

        assertEq(agg.totalNetworksScanned, 0);
        assertEq(agg.networksWithOverCollateralization, 0);
        assertEq(agg.highestCollateralRatio, 0);
        assertEq(agg.lowestCollateralRatio, 0);
        assertEq(agg.highestLevel, 0);
        assertEq(agg.totalTargetsIdentified, 0);
    }

    function testAggregateSingleResult() public view {
        CrossChainDetector.NetworkScanResult[] memory results = new CrossChainDetector.NetworkScanResult[](1);
        results[0] = CrossChainDetector.NetworkScanResult({
            chainId: 1,
            networkName: "Ethereum",
            detectedProtocol: CollateralizationDetector.ProtocolType.AaveV3,
            collateralRatio: 3e18,    // 300%
            collateralLevel: 3,       // Significantly over-collateralized
            isOverCollateralized: true,
            sequencerUp: true
        });

        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(results);

        assertEq(agg.totalNetworksScanned, 1);
        assertEq(agg.networksWithOverCollateralization, 1);
        assertEq(agg.highestCollateralRatio, 3e18);
        assertEq(agg.lowestCollateralRatio, 3e18);
        assertEq(agg.highestLevel, 3);
        assertEq(agg.totalTargetsIdentified, 1);
    }

    function testAggregateMultiNetworkResults() public view {
        CrossChainDetector.NetworkScanResult[] memory results = new CrossChainDetector.NetworkScanResult[](4);

        // Ethereum: Aave V3 at 300%
        results[0] = CrossChainDetector.NetworkScanResult({
            chainId: 1,
            networkName: "Ethereum",
            detectedProtocol: CollateralizationDetector.ProtocolType.AaveV3,
            collateralRatio: 3e18,
            collateralLevel: 3,
            isOverCollateralized: true,
            sequencerUp: true
        });

        // Arbitrum: Compound V3 at 500%
        results[1] = CrossChainDetector.NetworkScanResult({
            chainId: 42161,
            networkName: "Arbitrum",
            detectedProtocol: CollateralizationDetector.ProtocolType.CompoundV3,
            collateralRatio: 5e18,
            collateralLevel: 4,
            isOverCollateralized: true,
            sequencerUp: true
        });

        // Optimism: ERC4626 at 150% (not over-collateralized by our threshold)
        results[2] = CrossChainDetector.NetworkScanResult({
            chainId: 10,
            networkName: "Optimism",
            detectedProtocol: CollateralizationDetector.ProtocolType.ERC4626Vault,
            collateralRatio: 1.5e18,
            collateralLevel: 1,
            isOverCollateralized: false,
            sequencerUp: true
        });

        // Ethereum: Unknown target
        results[3] = CrossChainDetector.NetworkScanResult({
            chainId: 1,
            networkName: "Ethereum",
            detectedProtocol: CollateralizationDetector.ProtocolType.Unknown,
            collateralRatio: 0,
            collateralLevel: 0,
            isOverCollateralized: false,
            sequencerUp: true
        });

        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(results);

        assertEq(agg.totalNetworksScanned, 3, "3 unique chains (Ethereum, Arbitrum, Optimism)");
        assertEq(agg.networksWithOverCollateralization, 2, "2 over-collateralized positions");
        assertEq(agg.highestCollateralRatio, 5e18, "Highest ratio is 500%");
        assertEq(agg.lowestCollateralRatio, 1.5e18, "Lowest non-zero ratio is 150%");
        assertEq(agg.highestLevel, 4, "Highest level is 4 (extremely conservative)");
        assertEq(agg.totalTargetsIdentified, 3, "3 identified protocols (excluding Unknown)");
        assertEq(agg.results.length, 4, "All 4 results preserved");
    }

    function testAggregateDuplicateChainsCounted() public view {
        // Two results from same chain should count as 1 network
        CrossChainDetector.NetworkScanResult[] memory results = new CrossChainDetector.NetworkScanResult[](3);

        results[0] = CrossChainDetector.NetworkScanResult({
            chainId: 1,
            networkName: "Ethereum",
            detectedProtocol: CollateralizationDetector.ProtocolType.AaveV2,
            collateralRatio: 2.5e18,
            collateralLevel: 2,
            isOverCollateralized: true,
            sequencerUp: true
        });

        results[1] = CrossChainDetector.NetworkScanResult({
            chainId: 1,
            networkName: "Ethereum",
            detectedProtocol: CollateralizationDetector.ProtocolType.CompoundV2,
            collateralRatio: 4e18,
            collateralLevel: 3,
            isOverCollateralized: true,
            sequencerUp: true
        });

        results[2] = CrossChainDetector.NetworkScanResult({
            chainId: 42161,
            networkName: "Arbitrum",
            detectedProtocol: CollateralizationDetector.ProtocolType.AaveV3,
            collateralRatio: 6e18,
            collateralLevel: 4,
            isOverCollateralized: true,
            sequencerUp: true
        });

        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(results);

        assertEq(agg.totalNetworksScanned, 2, "Only 2 unique chains");
        assertEq(agg.networksWithOverCollateralization, 3, "All 3 positions are over-collateralized");
        assertEq(agg.totalTargetsIdentified, 3, "All 3 targets identified");
        assertEq(agg.highestCollateralRatio, 6e18, "Highest is 600%");
        assertEq(agg.lowestCollateralRatio, 2.5e18, "Lowest is 250%");
    }

    // ============================================================
    // Multicall3 Batch Building Tests
    // ============================================================

    function testBuildAaveV3BatchCalls() public view {
        address pool = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
        address[] memory users = new address[](3);
        users[0] = address(0x1111);
        users[1] = address(0x2222);
        users[2] = address(0x3333);

        IMulticall3.Call3[] memory calls = crossChain.buildAaveV3BatchCalls(pool, users);

        assertEq(calls.length, 3);
        for (uint256 i = 0; i < 3; i++) {
            assertEq(calls[i].target, pool);
            assertTrue(calls[i].allowFailure);
            assertEq(
                calls[i].callData,
                abi.encodeWithSelector(IAaveV3Pool.getUserAccountData.selector, users[i])
            );
        }
    }

    function testBuildClassifyBatch() public view {
        address[] memory targets = new address[](2);
        targets[0] = address(0xAAAA);
        targets[1] = address(0xBBBB);

        IMulticall3.Call3[] memory calls = crossChain.buildClassifyBatch(targets);

        assertEq(calls.length, 2);
        for (uint256 i = 0; i < 2; i++) {
            assertEq(calls[i].target, address(detector));
            assertTrue(calls[i].allowFailure);
            assertEq(
                calls[i].callData,
                abi.encodeWithSelector(
                    CollateralizationDetector.classifyProtocol.selector,
                    targets[i]
                )
            );
        }
    }

    // ============================================================
    // Constructor / Detector Reference Test
    // ============================================================

    function testDetectorReference() public view {
        assertEq(address(crossChain.detector()), address(detector));
    }

    // ============================================================
    // L2 Sequencer Grace Period Constant
    // ============================================================

    function testGracePeriodConstant() public view {
        assertEq(crossChain.L2_SEQUENCER_GRACE_PERIOD(), 3600, "Grace period should be 1 hour");
    }

    // ============================================================
    // L2 Networks Have Sequencer Feeds
    // ============================================================

    function testL2NetworksHaveSequencerFeeds() public view {
        uint256[] memory chains = crossChain.getSupportedChains();

        for (uint256 i = 0; i < chains.length; i++) {
            CrossChainDetector.NetworkConfig memory config = crossChain.getNetworkConfig(chains[i]);
            if (config.isL2) {
                assertTrue(
                    config.sequencerUptimeFeed != address(0),
                    "L2 networks should have sequencer uptime feeds"
                );
            }
        }
    }

    // ============================================================
    // Fuzz: Aggregation never reverts
    // ============================================================

    function testFuzzAggregateNeverReverts(
        uint256 ratio1,
        uint256 ratio2,
        uint8 level1,
        uint8 level2,
        bool oc1,
        bool oc2
    ) public view {
        CrossChainDetector.NetworkScanResult[] memory results = new CrossChainDetector.NetworkScanResult[](2);

        results[0] = CrossChainDetector.NetworkScanResult({
            chainId: 1,
            networkName: "Ethereum",
            detectedProtocol: CollateralizationDetector.ProtocolType.AaveV2,
            collateralRatio: ratio1,
            collateralLevel: level1,
            isOverCollateralized: oc1,
            sequencerUp: true
        });

        results[1] = CrossChainDetector.NetworkScanResult({
            chainId: 42161,
            networkName: "Arbitrum",
            detectedProtocol: CollateralizationDetector.ProtocolType.AaveV3,
            collateralRatio: ratio2,
            collateralLevel: level2,
            isOverCollateralized: oc2,
            sequencerUp: true
        });

        // Should never revert regardless of input values
        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(results);
        assertEq(agg.totalNetworksScanned, 2);
        assertEq(agg.results.length, 2);
    }
}
