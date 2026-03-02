// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import "../contracts/CrossChainDetector.sol";
import "../contracts/CollateralizationDetector.sol";
import "../contracts/interfaces/IChainlinkOracle.sol";
import "../contracts/interfaces/IMulticall3.sol";
import "../contracts/interfaces/IAaveV3.sol";

// ---------------------------------------------------------------------------
// Mock sequencer uptime feed
// ---------------------------------------------------------------------------

/// @dev Chainlink-style L2 sequencer uptime feed
contract MockSequencerFeed {
    int256  public answer;       // 0 = up, 1 = down
    uint256 public startedAt;

    constructor(int256 _answer, uint256 _startedAt) {
        answer    = _answer;
        startedAt = _startedAt;
    }

    function latestRoundData()
        external view
        returns (uint80, int256, uint256, uint256, uint80)
    {
        return (1, answer, startedAt, block.timestamp, 1);
    }

    function decimals() external pure returns (uint8) { return 0; }
}

// ---------------------------------------------------------------------------
// Test contract
// ---------------------------------------------------------------------------

contract CrossChainDetectorTest is Test {
    CollateralizationDetector internal detector;
    CrossChainDetector          internal crossChain;

    function setUp() public {
        detector   = new CollateralizationDetector();
        crossChain = new CrossChainDetector(address(detector));
        vm.warp(10_000);
    }

    // -----------------------------------------------------------------------
    // getNetworkConfig — all 8 supported chains
    // -----------------------------------------------------------------------

    function testGetNetworkConfig_Ethereum() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(1);
        assertEq(cfg.chainId, 1);
        assertEq(cfg.aaveV3Pool,     0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2);
        assertEq(cfg.compoundV3Comet, 0xc3d688B66703497DAA19211EEdff47f25384cdc3);
        assertEq(cfg.multicall3,     crossChain.MULTICALL3());
        assertFalse(cfg.isL2);
        assertEq(cfg.sequencerUptimeFeed, address(0));
    }

    function testGetNetworkConfig_Arbitrum() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(42161);
        assertEq(cfg.chainId, 42161);
        assertEq(cfg.aaveV3Pool, 0x794a61358D6845594F94dc1DB02A252b5b4814aD);
        assertTrue(cfg.isL2);
        assertTrue(cfg.sequencerUptimeFeed != address(0));
    }

    function testGetNetworkConfig_Optimism() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(10);
        assertEq(cfg.chainId, 10);
        assertTrue(cfg.isL2);
        assertTrue(cfg.sequencerUptimeFeed != address(0));
    }

    function testGetNetworkConfig_Polygon() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(137);
        assertEq(cfg.chainId, 137);
        assertFalse(cfg.isL2);
        assertEq(cfg.sequencerUptimeFeed, address(0));
    }

    function testGetNetworkConfig_Base() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(8453);
        assertEq(cfg.chainId, 8453);
        assertTrue(cfg.isL2);
        assertTrue(cfg.sequencerUptimeFeed != address(0));
    }

    function testGetNetworkConfig_Avalanche() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(43114);
        assertEq(cfg.chainId, 43114);
        assertFalse(cfg.isL2);
        assertEq(cfg.compoundV3Comet, address(0));
    }

    function testGetNetworkConfig_BSC() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(56);
        assertEq(cfg.chainId, 56);
        assertFalse(cfg.isL2);
        assertEq(cfg.aaveV3Pool, address(0));
        assertEq(cfg.compoundV3Comet, address(0));
    }

    function testGetNetworkConfig_ZkSync() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(324);
        assertEq(cfg.chainId, 324);
        assertTrue(cfg.isL2);
        assertEq(cfg.multicall3, crossChain.MULTICALL3_ZKSYNC());
        assertEq(cfg.sequencerUptimeFeed, address(0));
    }

    function testGetNetworkConfig_UnsupportedChain() public {
        vm.expectRevert(
            abi.encodeWithSelector(CrossChainDetector.UnsupportedChain.selector, 9999)
        );
        crossChain.getNetworkConfig(9999);
    }

    // -----------------------------------------------------------------------
    // getSupportedChains
    // -----------------------------------------------------------------------

    function testGetSupportedChains_Length() public view {
        uint256[] memory chains = crossChain.getSupportedChains();
        assertEq(chains.length, crossChain.SUPPORTED_NETWORKS());
        assertEq(chains.length, 8);
    }

    function testGetSupportedChains_ContainsAllChains() public view {
        uint256[] memory chains = crossChain.getSupportedChains();
        assertEq(chains[0], crossChain.CHAIN_ETHEREUM());
        assertEq(chains[1], crossChain.CHAIN_ARBITRUM());
        assertEq(chains[2], crossChain.CHAIN_OPTIMISM());
        assertEq(chains[3], crossChain.CHAIN_POLYGON());
        assertEq(chains[4], crossChain.CHAIN_BASE());
        assertEq(chains[5], crossChain.CHAIN_AVALANCHE());
        assertEq(chains[6], crossChain.CHAIN_BSC());
        assertEq(chains[7], crossChain.CHAIN_ZKSYNC());
    }

    // -----------------------------------------------------------------------
    // identificationCapacity
    // -----------------------------------------------------------------------

    function testIdentificationCapacity() public view {
        (uint256 capacity, uint256 single, uint256 mult) = crossChain.identificationCapacity();
        assertEq(single, crossChain.PROTOCOL_TYPES());
        assertEq(mult,   crossChain.SUPPORTED_NETWORKS());
        assertEq(capacity, single * mult);
        assertEq(capacity, 14 * 8); // 112
    }

    // -----------------------------------------------------------------------
    // checkSequencerUptime
    // -----------------------------------------------------------------------

    function testCheckSequencerUptime_ZeroAddress_AlwaysUp() public view {
        (bool isUp, uint256 timeSinceUp) = crossChain.checkSequencerUptime(address(0));
        assertTrue(isUp);
        assertEq(timeSinceUp, type(uint256).max);
    }

    function testCheckSequencerUptime_SequencerUp() public {
        // startedAt = 1000 seconds ago → timeSinceUp = 1000
        uint256 startedAt = block.timestamp - 1000;
        MockSequencerFeed feed = new MockSequencerFeed(0, startedAt);

        (bool isUp, uint256 timeSinceUp) = crossChain.checkSequencerUptime(address(feed));
        assertTrue(isUp);
        assertEq(timeSinceUp, 1000);
    }

    function testCheckSequencerUptime_SequencerDown() public {
        MockSequencerFeed feed = new MockSequencerFeed(1, block.timestamp);
        (bool isUp,) = crossChain.checkSequencerUptime(address(feed));
        assertFalse(isUp);
    }

    function testCheckSequencerUptime_FutureStartedAt() public {
        // startedAt in the future → timeSinceUp = 0
        uint256 startedAt = block.timestamp + 100;
        MockSequencerFeed feed = new MockSequencerFeed(0, startedAt);

        (bool isUp, uint256 timeSinceUp) = crossChain.checkSequencerUptime(address(feed));
        assertTrue(isUp);
        assertEq(timeSinceUp, 0);
    }

    // -----------------------------------------------------------------------
    // validateSequencer
    // -----------------------------------------------------------------------

    function testValidateSequencer_L1_AlwaysSafe() public view {
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(1);
        bool safe = crossChain.validateSequencer(cfg);
        assertTrue(safe);
    }

    function testValidateSequencer_L2_SequencerUpAndGracePassed() public {
        // startedAt = well past the grace period
        uint256 startedAt = block.timestamp - crossChain.L2_SEQUENCER_GRACE_PERIOD() - 100;
        MockSequencerFeed feed = new MockSequencerFeed(0, startedAt);

        CrossChainDetector.NetworkConfig memory cfg;
        cfg.chainId            = 42161;
        cfg.isL2               = true;
        cfg.sequencerUptimeFeed = address(feed);

        bool safe = crossChain.validateSequencer(cfg);
        assertTrue(safe);
    }

    function testValidateSequencer_L2_SequencerDown_Reverts() public {
        MockSequencerFeed feed = new MockSequencerFeed(1, block.timestamp);

        CrossChainDetector.NetworkConfig memory cfg;
        cfg.chainId            = 42161;
        cfg.isL2               = true;
        cfg.sequencerUptimeFeed = address(feed);

        vm.expectRevert(
            abi.encodeWithSelector(CrossChainDetector.SequencerDown.selector, 42161)
        );
        crossChain.validateSequencer(cfg);
    }

    function testValidateSequencer_L2_GracePeriodNotElapsed_Reverts() public {
        // startedAt = just 10 seconds ago → grace period not elapsed
        uint256 startedAt = block.timestamp - 10;
        MockSequencerFeed feed = new MockSequencerFeed(0, startedAt);

        CrossChainDetector.NetworkConfig memory cfg;
        cfg.chainId            = 42161;
        cfg.isL2               = true;
        cfg.sequencerUptimeFeed = address(feed);

        vm.expectRevert(
            abi.encodeWithSelector(CrossChainDetector.SequencerGracePeriod.selector, 42161, 10)
        );
        crossChain.validateSequencer(cfg);
    }

    function testValidateSequencer_L2_NoFeed_AlwaysSafe() public {
        // zkSync has no sequencer feed → should be safe
        CrossChainDetector.NetworkConfig memory cfg = crossChain.getNetworkConfig(324);
        bool safe = crossChain.validateSequencer(cfg);
        assertTrue(safe);
    }

    // -----------------------------------------------------------------------
    // aggregateResults
    // -----------------------------------------------------------------------

    function testAggregateResults_Empty() public view {
        CrossChainDetector.NetworkScanResult[] memory empty =
            new CrossChainDetector.NetworkScanResult[](0);
        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(empty);

        assertEq(agg.totalNetworksScanned, 0);
        assertEq(agg.networksWithOverCollateralization, 0);
        assertEq(agg.highestCollateralRatio, 0);
        assertEq(agg.lowestCollateralRatio, 0);
        assertEq(agg.totalTargetsIdentified, 0);
    }

    function testAggregateResults_SingleNetwork() public view {
        CrossChainDetector.NetworkScanResult[] memory results =
            new CrossChainDetector.NetworkScanResult[](1);

        results[0].chainId          = 1;
        results[0].networkName      = "Ethereum";
        results[0].collateralRatio  = 3e18;
        results[0].collateralLevel  = 4; // significant
        results[0].isOverCollateralized = true;
        results[0].detectedProtocol = CollateralizationDetector.ProtocolType.AaveV3;
        results[0].sequencerUp      = true;

        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(results);

        assertEq(agg.totalNetworksScanned, 1);
        assertEq(agg.networksWithOverCollateralization, 1);
        assertEq(agg.highestCollateralRatio, 3e18);
        assertEq(agg.lowestCollateralRatio, 3e18);
        assertEq(agg.highestLevel, 4);
        assertEq(agg.totalTargetsIdentified, 1); // AaveV3 != Unknown
    }

    function testAggregateResults_MultipleNetworks() public view {
        CrossChainDetector.NetworkScanResult[] memory results =
            new CrossChainDetector.NetworkScanResult[](3);

        results[0].chainId         = 1;
        results[0].collateralRatio = 5e18;
        results[0].collateralLevel = 5;
        results[0].isOverCollateralized = true;
        results[0].detectedProtocol = CollateralizationDetector.ProtocolType.CompoundV2;

        results[1].chainId         = 42161;
        results[1].collateralRatio = 1.5e18;
        results[1].collateralLevel = 2;
        results[1].isOverCollateralized = false;
        results[1].detectedProtocol = CollateralizationDetector.ProtocolType.Unknown;

        results[2].chainId         = 10;
        results[2].collateralRatio = 3e18;
        results[2].collateralLevel = 4;
        results[2].isOverCollateralized = true;
        results[2].detectedProtocol = CollateralizationDetector.ProtocolType.AaveV2;

        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(results);

        assertEq(agg.totalNetworksScanned, 3);
        assertEq(agg.networksWithOverCollateralization, 2);
        assertEq(agg.highestCollateralRatio, 5e18);
        assertEq(agg.lowestCollateralRatio, 1.5e18);
        assertEq(agg.highestLevel, 5);
        assertEq(agg.totalTargetsIdentified, 2); // Unknown doesn't count
    }

    function testAggregateResults_SameChainDeduplicated() public view {
        // Two results from the same chain → totalNetworksScanned = 1
        CrossChainDetector.NetworkScanResult[] memory results =
            new CrossChainDetector.NetworkScanResult[](2);

        results[0].chainId = 1;
        results[0].collateralRatio = 2e18;
        results[0].detectedProtocol = CollateralizationDetector.ProtocolType.AaveV3;

        results[1].chainId = 1;
        results[1].collateralRatio = 3e18;
        results[1].detectedProtocol = CollateralizationDetector.ProtocolType.CompoundV2;

        CrossChainDetector.CrossChainAggregation memory agg = crossChain.aggregateResults(results);
        assertEq(agg.totalNetworksScanned, 1); // same chain deduplicated
        assertEq(agg.totalTargetsIdentified, 2); // both protocols counted
    }

    // -----------------------------------------------------------------------
    // scanTarget
    // -----------------------------------------------------------------------

    function testScanTarget_Ethereum_Returns_ChainId() public {
        // Pass address(this) — will be Unknown protocol but fields set correctly
        CrossChainDetector.NetworkScanResult memory result =
            crossChain.scanTarget(address(this), 1);

        assertEq(result.chainId, 1);
        assertTrue(result.sequencerUp); // L1 always up
        assertEq(uint8(result.detectedProtocol), uint8(CollateralizationDetector.ProtocolType.Unknown));
    }

    function testScanTarget_Arbitrum_WithMockedSequencer() public {
        // Mock the Arbitrum sequencer feed to return a "down" sequencer
        // so result.sequencerUp = false
        address arbitrumFeed = 0xFdB631F5EE196F0ed6FAa767959853A9F217697D;
        // Encode latestRoundData() return: roundId=1, answer=1(down), startedAt=0, updatedAt=now, answeredInRound=1
        vm.mockCall(
            arbitrumFeed,
            abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector),
            abi.encode(uint80(1), int256(1), uint256(0), block.timestamp, uint80(1))
        );

        CrossChainDetector.NetworkScanResult memory result =
            crossChain.scanTarget(address(this), 42161);

        assertEq(result.chainId, 42161);
        assertFalse(result.sequencerUp); // sequencer is down
    }

    // -----------------------------------------------------------------------
    // buildAaveV3BatchCalls
    // -----------------------------------------------------------------------

    function testBuildAaveV3BatchCalls() public view {
        address pool = address(0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2);
        address[] memory users = new address[](2);
        users[0] = address(0x1);
        users[1] = address(0x2);

        IMulticall3.Call3[] memory calls = crossChain.buildAaveV3BatchCalls(pool, users);

        assertEq(calls.length, 2);
        assertEq(calls[0].target, pool);
        assertTrue(calls[0].allowFailure);
        assertEq(calls[1].target, pool);
        // Verify selector is getUserAccountData
        bytes4 sel = bytes4(calls[0].callData);
        assertEq(sel, IAaveV3Pool.getUserAccountData.selector);
    }

    function testBuildAaveV3BatchCalls_Empty() public view {
        address[] memory users = new address[](0);
        IMulticall3.Call3[] memory calls =
            crossChain.buildAaveV3BatchCalls(address(0x1), users);
        assertEq(calls.length, 0);
    }

    // -----------------------------------------------------------------------
    // buildClassifyBatch
    // -----------------------------------------------------------------------

    function testBuildClassifyBatch() public view {
        address[] memory targets = new address[](3);
        targets[0] = address(0xA);
        targets[1] = address(0xB);
        targets[2] = address(0xC);

        IMulticall3.Call3[] memory calls = crossChain.buildClassifyBatch(targets);

        assertEq(calls.length, 3);
        for (uint256 i = 0; i < 3; i++) {
            assertEq(calls[i].target, address(detector));
            assertTrue(calls[i].allowFailure);
            bytes4 sel = bytes4(calls[i].callData);
            assertEq(sel, CollateralizationDetector.classifyProtocol.selector);
        }
    }

    function testBuildClassifyBatch_Empty() public view {
        address[] memory targets = new address[](0);
        IMulticall3.Call3[] memory calls = crossChain.buildClassifyBatch(targets);
        assertEq(calls.length, 0);
    }

    // -----------------------------------------------------------------------
    // Constants correctness
    // -----------------------------------------------------------------------

    function testConstants() public view {
        assertEq(crossChain.CHAIN_ETHEREUM(), 1);
        assertEq(crossChain.CHAIN_ARBITRUM(), 42161);
        assertEq(crossChain.CHAIN_OPTIMISM(), 10);
        assertEq(crossChain.CHAIN_POLYGON(), 137);
        assertEq(crossChain.CHAIN_BASE(), 8453);
        assertEq(crossChain.CHAIN_AVALANCHE(), 43114);
        assertEq(crossChain.CHAIN_BSC(), 56);
        assertEq(crossChain.CHAIN_ZKSYNC(), 324);
        assertEq(crossChain.SUPPORTED_NETWORKS(), 8);
        assertEq(crossChain.PROTOCOL_TYPES(), 14);
        assertEq(crossChain.L2_SEQUENCER_GRACE_PERIOD(), 3600);
        assertEq(crossChain.MULTICALL3(), 0xcA11bde05977b3631167028862bE2a173976CA11);
        assertEq(crossChain.MULTICALL3_ZKSYNC(), 0xF9cda624FBC7e059355ce98a31693d299FACd963);
    }

    function testDetectorAddress() public view {
        assertEq(address(crossChain.detector()), address(detector));
    }
}
