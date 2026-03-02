// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import "../contracts/CollateralizationDetector.sol";
import "../contracts/OracleIntegration.sol";
import "../contracts/interfaces/IChainlinkOracle.sol";
import "../contracts/interfaces/IAaveV2.sol";
import "../contracts/interfaces/ICompoundV2.sol";
import "../contracts/interfaces/IMulticall3.sol";

// ---------------------------------------------------------------------------
// Mock contracts
// ---------------------------------------------------------------------------

/// @dev Mock Aave V2/V3 lending pool
contract MockAavePool {
    uint256 public collateral;
    uint256 public debt;
    uint256 public healthFactor;

    constructor(uint256 _collateral, uint256 _debt, uint256 _hf) {
        collateral  = _collateral;
        debt        = _debt;
        healthFactor = _hf;
    }

    function getUserAccountData(address)
        external view
        returns (uint256, uint256, uint256, uint256, uint256, uint256)
    {
        return (collateral, debt, 0, 0, 0, healthFactor);
    }
}

/// @dev Mock Compound V2 Comptroller
contract MockComptroller {
    uint256 public err;
    uint256 public liquidity;
    uint256 public shortfall;

    constructor(uint256 _err, uint256 _liq, uint256 _short) {
        err       = _err;
        liquidity = _liq;
        shortfall = _short;
    }

    function getAccountLiquidity(address)
        external view
        returns (uint256, uint256, uint256)
    {
        return (err, liquidity, shortfall);
    }
}

/// @dev Mock Maker Vat
contract MockVat {
    uint256 public inkVal;
    uint256 public artVal;
    uint256 public rateVal;

    constructor(uint256 _ink, uint256 _art, uint256 _rate) {
        inkVal  = _ink;
        artVal  = _art;
        rateVal = _rate;
    }

    function urns(bytes32, address) external view returns (uint256, uint256) {
        return (inkVal, artVal);
    }

    function ilks(bytes32) external view returns (uint256, uint256, uint256, uint256, uint256) {
        // Art, rate, spot, line, dust
        return (0, rateVal, 0, 0, 0);
    }
}

/// @dev Mock Maker Spotter
contract MockSpotter {
    uint256 public mat;

    constructor(uint256 _mat) {
        mat = _mat;
    }

    function ilks(bytes32) external view returns (address, uint256) {
        return (address(0), mat);
    }
}

/// @dev Mock Chainlink oracle (fresh price by default)
contract MockOracle {
    int256  public price;
    uint8   public dec;
    uint256 public updatedAtOverride;

    constructor(int256 _price, uint8 _dec) {
        price = _price;
        dec   = _dec;
        updatedAtOverride = 0; // 0 means use block.timestamp
    }

    function setUpdatedAt(uint256 _updatedAt) external { updatedAtOverride = _updatedAt; }

    function latestRoundData()
        external view
        returns (uint80, int256, uint256, uint256, uint80)
    {
        uint256 ts = updatedAtOverride == 0 ? block.timestamp : updatedAtOverride;
        return (1, price, 0, ts, 1);
    }

    function decimals() external view returns (uint8) { return dec; }
}

/// @dev Mock ERC-4626 vault
contract MockERC4626 {
    address public underlyingAsset;
    uint256 public shareBalance;
    uint256 public assetsPerShare;

    constructor(address _asset, uint256 _shares, uint256 _assets) {
        underlyingAsset = _asset;
        shareBalance    = _shares;
        assetsPerShare  = _assets;
    }

    function totalAssets() external view returns (uint256) { return assetsPerShare; }
    function asset()       external view returns (address)  { return underlyingAsset; }
    function balanceOf(address) external view returns (uint256) { return shareBalance; }
    function convertToAssets(uint256 shares) external view returns (uint256) {
        return shares * assetsPerShare / 1e18;
    }
}

/// @dev Mock Uniswap V2 pair
contract MockUniswapV2Pair {
    uint112 public r0;
    uint112 public r1;
    address public tok0;
    address public tok1;
    uint256 public supply;
    uint256 public bal;

    constructor(uint112 _r0, uint112 _r1, address _tok0, address _tok1, uint256 _supply, uint256 _bal) {
        r0 = _r0; r1 = _r1; tok0 = _tok0; tok1 = _tok1; supply = _supply; bal = _bal;
    }

    function getReserves() external view returns (uint112, uint112, uint32) { return (r0, r1, 0); }
    function totalSupply() external view returns (uint256) { return supply; }
    function token0()      external view returns (address) { return tok0; }
    function token1()      external view returns (address) { return tok1; }
    function balanceOf(address) external view returns (uint256) { return bal; }
}

/// @dev Mock RToken
contract MockRToken {
    uint256 public supply;
    uint192 public needed;
    address public mainAddr;

    constructor(uint256 _supply, uint192 _needed, address _main) {
        supply  = _supply;
        needed  = _needed;
        mainAddr = _main;
    }

    function totalSupply()    external view returns (uint256) { return supply; }
    function basketsNeeded()  external view returns (uint192) { return needed; }
    function main()           external view returns (address) { return mainAddr; }
    function balanceOf(address) external view returns (uint256) { return 0; }
}

/// @dev Mock Main contract
contract MockMain {
    address public bHandler;
    address public aRegistry;
    address public bManager;

    constructor(address _handler, address _registry, address _manager) {
        bHandler  = _handler;
        aRegistry = _registry;
        bManager  = _manager;
    }

    function basketHandler()  external view returns (address) { return bHandler; }
    function assetRegistry()  external view returns (address) { return aRegistry; }
    function backingManager() external view returns (address) { return bManager; }
}

/// @dev Mock BasketHandler
contract MockBasketHandler {
    address[] public tokensArr;
    uint256[] public amountsArr;
    uint8    public basketStatus;

    constructor(address[] memory _tokens, uint256[] memory _amounts, uint8 _status) {
        tokensArr    = _tokens;
        amountsArr   = _amounts;
        basketStatus = _status;
    }

    function quote(uint192, uint8) external view returns (address[] memory, uint256[] memory) {
        return (tokensArr, amountsArr);
    }
    function status() external view returns (uint8) { return basketStatus; }
}

/// @dev Mock AssetRegistry
contract MockAssetRegistry {
    address[] public assets;

    constructor(address[] memory _assets) { assets = _assets; }
    function erc20s() external view returns (address[] memory) { return assets; }
}

/// @dev Mock BackingManager
contract MockBackingManager {
    uint8 public backingStatus;
    constructor(uint8 _status) { backingStatus = _status; }
    function status() external view returns (uint8) { return backingStatus; }
}

// ---------------------------------------------------------------------------
// Test contract
// ---------------------------------------------------------------------------

contract CollateralizationDetectorTest is Test {
    CollateralizationDetector internal detector;

    function setUp() public {
        detector = new CollateralizationDetector();
        // Use a predictable timestamp for staleness checks
        vm.warp(10_000);
    }

    // -----------------------------------------------------------------------
    // classifyCollateralization
    // -----------------------------------------------------------------------

    function testClassifyCollateralization_Under() public view {
        // < 1e18 (< 100%)
        assertEq(detector.classifyCollateralization(0.9e18), 0);
    }

    function testClassifyCollateralization_AtRisk() public view {
        // 100% - 120%
        assertEq(detector.classifyCollateralization(1e18), 1);
        assertEq(detector.classifyCollateralization(1.1e18), 1);
    }

    function testClassifyCollateralization_Normal() public view {
        // 120% - 200%
        assertEq(detector.classifyCollateralization(1.2e18), 2);
        assertEq(detector.classifyCollateralization(1.5e18), 2);
        assertEq(detector.classifyCollateralization(1.99e18), 2);
    }

    function testClassifyCollateralization_Well() public view {
        // 200% - 300%
        assertEq(detector.classifyCollateralization(2e18), 3);
        assertEq(detector.classifyCollateralization(2.5e18), 3);
    }

    function testClassifyCollateralization_Significant() public view {
        // 300% - 500%
        assertEq(detector.classifyCollateralization(3e18), 4);
        assertEq(detector.classifyCollateralization(4.5e18), 4);
    }

    function testClassifyCollateralization_Extreme() public view {
        // 500% - 1000%
        assertEq(detector.classifyCollateralization(5e18), 5);
        assertEq(detector.classifyCollateralization(9.9e18), 5);
    }

    function testClassifyCollateralization_Outlier() public view {
        // > 1000%
        assertEq(detector.classifyCollateralization(10e18), 6);
        assertEq(detector.classifyCollateralization(100e18), 6);
    }

    // -----------------------------------------------------------------------
    // classifyProtocol — heuristic
    // -----------------------------------------------------------------------

    function testClassifyProtocol_UniswapV2() public {
        // Deploy a mock that responds to getReserves()
        MockUniswapV2Pair pair = new MockUniswapV2Pair(
            1e18, 1e18, address(1), address(2), 1e18, 1e18
        );
        assertEq(uint8(detector.classifyProtocol(address(pair))), uint8(CollateralizationDetector.ProtocolType.UniswapV2));
    }

    function testClassifyProtocol_ERC4626() public {
        // totalAssets + asset → ERC4626
        MockERC4626 vault = new MockERC4626(address(3), 1e18, 1e18);
        assertEq(uint8(detector.classifyProtocol(address(vault))), uint8(CollateralizationDetector.ProtocolType.ERC4626Vault));
    }

    function testClassifyProtocol_Unknown() public {
        // An EOA or contract with no matching selectors
        assertEq(uint8(detector.classifyProtocol(address(this))), uint8(CollateralizationDetector.ProtocolType.Unknown));
    }

    function testClassifyProtocol_ReserveProtocol() public {
        // basketsNeeded + main → ReserveProtocol
        MockMain mainMock = new MockMain(address(0), address(0), address(0));
        MockRToken rToken = new MockRToken(1e18, uint192(1.1e18), address(mainMock));
        assertEq(uint8(detector.classifyProtocol(address(rToken))), uint8(CollateralizationDetector.ProtocolType.ReserveProtocol));
    }

    // -----------------------------------------------------------------------
    // getAaveV2Position
    // -----------------------------------------------------------------------

    function testGetAaveV2Position_WithDebt() public {
        // 4 ETH collateral, 1 ETH debt → CR = 400%
        MockAavePool pool = new MockAavePool(4e18, 1e18, 3e18);
        CollateralizationDetector.AavePositionData memory data =
            detector.getAaveV2Position(address(pool), address(this));

        assertEq(data.totalCollateral, 4e18);
        assertEq(data.totalDebt, 1e18);
        assertEq(data.healthFactor, 3e18);
        assertEq(data.collateralRatio, 4e18);
        assertTrue(data.isOverCollateralized); // 400% ≥ WELL_COLLATERALIZED (200%)
    }

    function testGetAaveV2Position_NoDebt() public {
        // No debt → collateralRatio = type(uint256).max
        // type(uint256).max >= WELL_COLLATERALIZED (2e18) → isOverCollateralized = true
        MockAavePool pool = new MockAavePool(5e18, 0, type(uint256).max);
        CollateralizationDetector.AavePositionData memory data =
            detector.getAaveV2Position(address(pool), address(this));

        assertEq(data.collateralRatio, type(uint256).max);
        assertTrue(data.isOverCollateralized);
    }

    function testGetAaveV2Position_AtRisk() public {
        // 1.1 ETH collateral, 1 ETH debt → 110% → NOT well-collateralized
        MockAavePool pool = new MockAavePool(1.1e18, 1e18, 1.05e18);
        CollateralizationDetector.AavePositionData memory data =
            detector.getAaveV2Position(address(pool), address(this));
        assertFalse(data.isOverCollateralized);
    }

    // -----------------------------------------------------------------------
    // getAaveV3Position
    // -----------------------------------------------------------------------

    function testGetAaveV3Position_WithDebt() public {
        // USD values (8-decimal Aave v3): 3000e8 USD collateral, 1000e8 USD debt
        MockAavePool pool = new MockAavePool(3000e8, 1000e8, 2.25e18);
        CollateralizationDetector.AavePositionData memory data =
            detector.getAaveV3Position(address(pool), address(this));

        assertEq(data.totalCollateral, 3000e8);
        assertEq(data.totalDebt, 1000e8);
        assertEq(data.collateralRatio, 3e18); // 300%
        assertTrue(data.isOverCollateralized);
    }

    function testGetAaveV3Position_NoDebt() public {
        MockAavePool pool = new MockAavePool(5000e8, 0, type(uint256).max);
        CollateralizationDetector.AavePositionData memory data =
            detector.getAaveV3Position(address(pool), address(this));
        assertEq(data.collateralRatio, type(uint256).max);
    }

    // -----------------------------------------------------------------------
    // getCompoundV2Position
    // -----------------------------------------------------------------------

    function testGetCompoundV2Position_OverCollateralized() public {
        MockComptroller comp = new MockComptroller(0, 500e18, 0);
        CollateralizationDetector.CompoundV2PositionData memory data =
            detector.getCompoundV2Position(address(comp), address(this));
        assertTrue(data.isOverCollateralized);
        assertEq(data.liquidity, 500e18);
        assertEq(data.shortfall, 0);
    }

    function testGetCompoundV2Position_UnderCollateralized() public {
        MockComptroller comp = new MockComptroller(0, 0, 100e18);
        CollateralizationDetector.CompoundV2PositionData memory data =
            detector.getCompoundV2Position(address(comp), address(this));
        assertFalse(data.isOverCollateralized);
        assertEq(data.shortfall, 100e18);
    }

    function testGetCompoundV2Position_RevertsOnError() public {
        MockComptroller comp = new MockComptroller(1, 0, 0); // error code = 1
        vm.expectRevert("Comptroller error");
        detector.getCompoundV2Position(address(comp), address(this));
    }

    // -----------------------------------------------------------------------
    // getERC4626Position
    // -----------------------------------------------------------------------

    function testGetERC4626Position() public {
        address underlying = address(0xBEEF);
        // 2e18 shares, each worth 1.5 underlying (3e18 total)
        MockERC4626 vault = new MockERC4626(underlying, 2e18, 1.5e18);
        CollateralizationDetector.VaultPositionData memory data =
            detector.getERC4626Position(address(vault), address(this));

        assertEq(data.shares, 2e18);
        assertEq(data.underlyingAsset, underlying);
        // convertToAssets(2e18) = 2e18 * 1.5e18 / 1e18 = 3e18
        assertEq(data.underlyingValue, 3e18);
    }

    function testGetERC4626Position_ZeroShares() public {
        MockERC4626 vault = new MockERC4626(address(1), 0, 1e18);
        CollateralizationDetector.VaultPositionData memory data =
            detector.getERC4626Position(address(vault), address(this));
        assertEq(data.shares, 0);
        assertEq(data.underlyingValue, 0);
    }

    // -----------------------------------------------------------------------
    // getRTokenPosition
    // -----------------------------------------------------------------------

    function testGetRTokenPosition_OverCollateralized() public {
        // totalSupply = 100e18, basketsNeeded = 110e18 → over-collateralized
        MockMain mainMock = new MockMain(address(0), address(0), address(0));
        MockRToken rToken = new MockRToken(100e18, uint192(110e18), address(mainMock));

        CollateralizationDetector.RTokenPositionData memory data =
            detector.getRTokenPosition(address(rToken));

        assertTrue(data.isOverCollateralized);
        assertEq(data.totalSupply, 100e18);
        assertEq(data.basketsNeeded, 110e18);
        assertEq(data.excessBaskets, 10e18);
        assertEq(data.collateralRatio, 1.1e18); // 110%
        assertEq(data.profitPerToken, 0.1e18);  // 10%
    }

    function testGetRTokenPosition_AdequatelyCollateralized() public {
        // totalSupply = 100e18, basketsNeeded = 95e18 → NOT over-collateralized
        MockMain mainMock = new MockMain(address(0), address(0), address(0));
        MockRToken rToken = new MockRToken(100e18, uint192(95e18), address(mainMock));

        CollateralizationDetector.RTokenPositionData memory data =
            detector.getRTokenPosition(address(rToken));

        assertFalse(data.isOverCollateralized);
        assertEq(data.excessBaskets, 0);
    }

    function testGetRTokenPosition_ZeroSupply() public {
        MockMain mainMock = new MockMain(address(0), address(0), address(0));
        MockRToken rToken = new MockRToken(0, 0, address(mainMock));

        CollateralizationDetector.RTokenPositionData memory data =
            detector.getRTokenPosition(address(rToken));

        assertEq(data.totalSupply, 0);
        assertFalse(data.isOverCollateralized);
    }

    // -----------------------------------------------------------------------
    // analyzeRTokenProfitability
    // -----------------------------------------------------------------------

    function testAnalyzeRTokenProfitability_WithOpportunity() public {
        MockMain mainMock = new MockMain(address(0), address(0), address(0));
        // 1000 supply, 1100 needed → 10% over
        MockRToken rToken = new MockRToken(1000e18, uint192(1100e18), address(mainMock));

        (uint256 excess, uint256 bps, uint256 profit, bool isOpp) =
            detector.analyzeRTokenProfitability(address(rToken), 2000e18);

        assertTrue(isOpp);
        assertEq(excess, 100e18);
        assertEq(bps, 1000); // 10% in basis points
        // totalProfitUsd = 100e18 * 2000e18 / 1e18 = 200_000e18
        assertEq(profit, 200_000e18);
    }

    function testAnalyzeRTokenProfitability_NoOpportunity() public {
        MockMain mainMock = new MockMain(address(0), address(0), address(0));
        MockRToken rToken = new MockRToken(1000e18, uint192(900e18), address(mainMock));

        (, , , bool isOpp) = detector.analyzeRTokenProfitability(address(rToken), 2000e18);
        assertFalse(isOpp);
    }

    // -----------------------------------------------------------------------
    // buildAaveV2Call / buildCompoundV2Call
    // -----------------------------------------------------------------------

    function testBuildAaveV2Call() public view {
        address pool = address(0xAA);
        address user = address(0xBB);
        IMulticall3.Call3 memory call = detector.buildAaveV2Call(pool, user);

        assertEq(call.target, pool);
        assertTrue(call.allowFailure);
        // callData must start with getUserAccountData selector (0x35ea6a75)
        bytes4 sel = bytes4(call.callData);
        assertEq(sel, IAaveV2LendingPool.getUserAccountData.selector);
    }

    function testBuildCompoundV2Call() public view {
        address comp = address(0xCC);
        address acct = address(0xDD);
        IMulticall3.Call3 memory call = detector.buildCompoundV2Call(comp, acct);

        assertEq(call.target, comp);
        assertTrue(call.allowFailure);
        bytes4 sel = bytes4(call.callData);
        assertEq(sel, ICompoundV2Comptroller.getAccountLiquidity.selector);
    }

    // -----------------------------------------------------------------------
    // getMakerPosition (requires oracle)
    // -----------------------------------------------------------------------

    function testGetMakerPosition_OverCollateralized() public {
        // ink = 2 ETH, art = 1 (normalised), rate = 1e27 → debt = 1e18
        // Oracle: price = 2000e8 USD/ETH (8 dec)
        // collateral_value = 2e18 * 2000e8 / 1e8 = 4000e18
        // CR = 4000e18 / 1e18 = 4000e18 → well over threshold

        MockVat vat         = new MockVat(2e18, 1e18, 1e27);
        MockSpotter spotter = new MockSpotter(1.5e27);
        MockOracle  oracle  = new MockOracle(2000e8, 8);

        CollateralizationDetector.MakerPositionData memory data =
            detector.getMakerPosition(address(vat), address(spotter), bytes32("ETH-A"), address(this), address(oracle), 3600);

        assertEq(data.collateralAmount, 2e18);
        assertEq(data.debtAmount, 1e18);    // art * rate / 1e27 = 1e18 * 1e27 / 1e27 = 1e18
        assertTrue(data.isOverCollateralized);
        assertEq(data.collateralRatio, 4000e18);
    }

    function testGetMakerPosition_NoDebt() public {
        MockVat vat         = new MockVat(2e18, 0, 1e27);
        MockSpotter spotter = new MockSpotter(1.5e27);
        MockOracle  oracle  = new MockOracle(2000e8, 8);

        CollateralizationDetector.MakerPositionData memory data =
            detector.getMakerPosition(address(vat), address(spotter), bytes32("ETH-A"), address(this), address(oracle), 3600);

        assertEq(data.collateralRatio, type(uint256).max);
    }
}
