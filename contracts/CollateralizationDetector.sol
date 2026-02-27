// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "./interfaces/IAaveV2.sol";
import "./interfaces/IAaveV3.sol";
import "./interfaces/ICompoundV2.sol";
import "./interfaces/ICompoundV3.sol";
import "./interfaces/IMakerDAO.sol";
import "./interfaces/IChainlinkOracle.sol";
import "./interfaces/IERC4626.sol";
import "./interfaces/IUniswapV2.sol";
import "./interfaces/IMulticall3.sol";
import "./interfaces/IReserveProtocol.sol";
import "./OracleIntegration.sol";
import "./LPPricing.sol";

/// @title CollateralizationDetector
/// @notice Multi-protocol DeFi over-collateralization detection system
/// @dev Supports Aave v2/v3, Compound v2/v3, MakerDAO, ERC-4626 vaults, and Uniswap v2 LP tokens
///
/// Over-collateralization thresholds:
///   > 200% = well-collateralized
///   > 300% = significantly over-collateralized
///   > 500% = extremely conservative / capital-inefficient
contract CollateralizationDetector {
    using OracleIntegration for address;

    // --- Structs ---

    struct AavePositionData {
        uint256 totalCollateral;
        uint256 totalDebt;
        uint256 healthFactor;
        uint256 collateralRatio; // Scaled to 1e18 (1e18 = 100%)
        bool isOverCollateralized;
    }

    struct CompoundV2PositionData {
        uint256 liquidity;
        uint256 shortfall;
        bool isOverCollateralized;
    }

    struct CompoundV3PositionData {
        uint256 totalBorrowed;
        uint256 totalCollateralValue;
        uint256 collateralRatio; // Scaled to 1e18
        bool isOverCollateralized;
    }

    struct MakerPositionData {
        uint256 collateralAmount; // ink (wad, 1e18)
        uint256 debtAmount;       // art * rate (wad, 1e18)
        uint256 collateralRatio;  // Scaled to 1e18 (1.5e18 = 150%)
        uint256 minRatio;         // Minimum required ratio from Spotter
        bool isOverCollateralized;
    }

    struct VaultPositionData {
        uint256 shares;
        uint256 underlyingValue;
        address underlyingAsset;
    }

    struct LPPositionData {
        uint256 lpBalance;
        uint256 fairValue_1e18;
        address token0;
        address token1;
    }

    /// @notice Reserve Protocol RToken position data
    struct RTokenPositionData {
        uint256 totalSupply;           // Total RToken supply
        uint256 basketsNeeded;         // Number of baskets needed to back supply
        uint256 excessBaskets;         // basketsNeeded - totalSupply (0 if not over-collateralized)
        uint256 collateralRatio;       // basketsNeeded / totalSupply scaled to 1e18
        uint256 profitPerToken;        // Profit per token in basis points (1e18 scaled)
        address[] collateralTokens;    // Basket collateral token addresses
        uint256[] collateralAmounts;   // Required amounts per 1 RToken (1e18)
        bool isOverCollateralized;     // True when basketsNeeded > totalSupply
    }

    /// @notice Protocol type enum for heuristic classification
    enum ProtocolType {
        Unknown,
        AaveV2,
        AaveV3,
        CompoundV2,
        CompoundV3,
        MakerDAO,
        ERC4626Vault,
        YearnV2,
        BeefyVault,
        UniswapV2,
        UniswapV3,
        CurvePool,
        BalancerV2,
        ChainlinkOracle,
        ReserveProtocol
    }

    // --- Over-Collateralization Thresholds (scaled to 1e18) ---

    uint256 public constant AT_RISK = 1.2e18;                        // 120%
    uint256 public constant WELL_COLLATERALIZED = 2e18;              // 200%
    uint256 public constant SIGNIFICANTLY_OVER_COLLATERALIZED = 3e18; // 300%
    uint256 public constant EXTREMELY_CONSERVATIVE = 5e18;            // 500%
    uint256 public constant EXTREME_OUTLIER = 10e18;                  // 1000%

    // --- Aave Detection ---

    /// @notice Reads Aave v2 position data for a user
    /// @param pool The Aave v2 LendingPool address
    /// @param user The user address
    /// @return data The position data with collateral ratio and health factor
    function getAaveV2Position(address pool, address user)
        external
        view
        returns (AavePositionData memory data)
    {
        (
            uint256 totalCollateralETH,
            uint256 totalDebtETH,
            ,
            ,
            ,
            uint256 healthFactor
        ) = IAaveV2LendingPool(pool).getUserAccountData(user);

        data.totalCollateral = totalCollateralETH;
        data.totalDebt = totalDebtETH;
        data.healthFactor = healthFactor;

        if (totalDebtETH > 0) {
            data.collateralRatio = (totalCollateralETH * 1e18) / totalDebtETH;
        } else {
            data.collateralRatio = type(uint256).max; // No debt = infinite ratio
        }

        data.isOverCollateralized = data.collateralRatio >= WELL_COLLATERALIZED;
    }

    /// @notice Reads Aave v3 position data for a user
    /// @dev Returns values in base currency (USD with 8 decimals)
    /// @param pool The Aave v3 Pool address
    /// @param user The user address
    /// @return data The position data
    function getAaveV3Position(address pool, address user)
        external
        view
        returns (AavePositionData memory data)
    {
        (
            uint256 totalCollateralBase,
            uint256 totalDebtBase,
            ,
            ,
            ,
            uint256 healthFactor
        ) = IAaveV3Pool(pool).getUserAccountData(user);

        data.totalCollateral = totalCollateralBase;
        data.totalDebt = totalDebtBase;
        data.healthFactor = healthFactor;

        if (totalDebtBase > 0) {
            data.collateralRatio = (totalCollateralBase * 1e18) / totalDebtBase;
        } else {
            data.collateralRatio = type(uint256).max;
        }

        data.isOverCollateralized = data.collateralRatio >= WELL_COLLATERALIZED;
    }

    // --- Compound Detection ---

    /// @notice Reads Compound v2 account liquidity
    /// @param comptroller The Comptroller address
    /// @param account The user address
    /// @return data Position data: liquidity > 0 means over-collateralized
    function getCompoundV2Position(address comptroller, address account)
        external
        view
        returns (CompoundV2PositionData memory data)
    {
        (uint256 err, uint256 liquidity, uint256 shortfall) =
            ICompoundV2Comptroller(comptroller).getAccountLiquidity(account);

        require(err == 0, "Comptroller error");

        data.liquidity = liquidity;
        data.shortfall = shortfall;
        data.isOverCollateralized = liquidity > 0 && shortfall == 0;
    }

    /// @notice Reads Compound v3 position data
    /// @param comet The Comet (Compound v3) contract address
    /// @param account The user address
    /// @param collateralAssets Array of collateral asset addresses to check
    /// @param oracleFeeds Array of Chainlink oracle feeds for each collateral asset
    /// @param assetDecimals Array of decimal counts for each collateral asset
    /// @param baseFeed Chainlink feed for the base token
    /// @param baseDecimals Decimals for the base token
    /// @return data Position data with collateral ratio
    function getCompoundV3Position(
        address comet,
        address account,
        address[] calldata collateralAssets,
        address[] calldata oracleFeeds,
        uint8[] calldata assetDecimals,
        address baseFeed,
        uint8 baseDecimals
    ) external view returns (CompoundV3PositionData memory data) {
        data.totalBorrowed = ICompoundV3Comet(comet).borrowBalanceOf(account);

        // Sum collateral values
        uint256 totalCollateralUsd18 = 0;
        for (uint256 i = 0; i < collateralAssets.length; i++) {
            uint128 balance = ICompoundV3Comet(comet).collateralBalanceOf(account, collateralAssets[i]);
            if (balance > 0) {
                (uint256 price, uint8 feedDec) = OracleIntegration.getPrice(oracleFeeds[i]);
                totalCollateralUsd18 += OracleIntegration.tokenValueUsd18(
                    uint256(balance), assetDecimals[i], price, feedDec
                );
            }
        }

        data.totalCollateralValue = totalCollateralUsd18;

        if (data.totalBorrowed > 0) {
            // Convert borrowed amount to USD
            (uint256 basePrice, uint8 baseFeedDec) = OracleIntegration.getPrice(baseFeed);
            uint256 borrowedUsd18 = OracleIntegration.tokenValueUsd18(
                data.totalBorrowed, baseDecimals, basePrice, baseFeedDec
            );

            if (borrowedUsd18 > 0) {
                data.collateralRatio = (totalCollateralUsd18 * 1e18) / borrowedUsd18;
            }
        } else {
            data.collateralRatio = type(uint256).max;
        }

        data.isOverCollateralized = data.collateralRatio >= WELL_COLLATERALIZED;
    }

    // --- MakerDAO Detection ---

    /// @notice Reads MakerDAO vault (CDP) position data
    /// @param vat The Vat address (0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B)
    /// @param spotter The Spotter address
    /// @param ilk The collateral type identifier
    /// @param urn The urn address
    /// @param oracleFeed Chainlink oracle feed for the collateral
    /// @param oracleMaxStaleness Maximum staleness in seconds
    /// @return data Position data with collateral ratio
    function getMakerPosition(
        address vat,
        address spotter,
        bytes32 ilk,
        address urn,
        address oracleFeed,
        uint256 oracleMaxStaleness
    ) external view returns (MakerPositionData memory data) {
        // Read urn data: ink (collateral), art (normalized debt)
        (uint256 ink, uint256 art) = IMakerVat(vat).urns(ilk, urn);

        // Read ilk data: rate (accumulated rate, ray 1e27)
        (, uint256 rate,,,) = IMakerVat(vat).ilks(ilk);

        // Read minimum ratio from Spotter
        (, uint256 mat) = IMakerSpotter(spotter).ilks(ilk);

        data.collateralAmount = ink;
        // Actual debt = art × rate / 1e27 (ray to wad conversion)
        data.debtAmount = (art * rate) / 1e27;
        data.minRatio = mat; // ray (1e27), e.g., 1.5e27 = 150%

        if (data.debtAmount > 0) {
            // Get collateral price from oracle
            (uint256 price, uint8 feedDec) = OracleIntegration.getValidatedPrice(oracleFeed, oracleMaxStaleness);

            // collateral_value = ink * price
            // CR = collateral_value / debt
            // All in 1e18 for consistency
            uint256 collateralValue = (ink * price) / (10 ** feedDec);
            data.collateralRatio = (collateralValue * 1e18) / data.debtAmount;
        } else {
            data.collateralRatio = type(uint256).max;
        }

        // Over-collateralized if ratio exceeds minimum by 2x
        data.isOverCollateralized = data.collateralRatio >= WELL_COLLATERALIZED;
    }

    // --- Vault Detection ---

    /// @notice Reads ERC-4626 vault position data
    /// @param vault The vault address
    /// @param account The user address
    /// @return data The vault position data
    function getERC4626Position(address vault, address account)
        external
        view
        returns (VaultPositionData memory data)
    {
        IERC4626 v = IERC4626(vault);
        data.shares = v.balanceOf(account);
        data.underlyingValue = v.convertToAssets(data.shares);
        data.underlyingAsset = v.asset();
    }

    // --- LP Token Detection ---

    /// @notice Reads Uniswap v2 LP position with fair pricing
    /// @param pair The Uniswap v2 pair address
    /// @param account The LP holder address
    /// @param oracle0 Chainlink feed for token0
    /// @param oracle1 Chainlink feed for token1
    /// @return data LP position data with fair value
    function getUniswapV2LPPosition(
        address pair,
        address account,
        address oracle0,
        address oracle1
    ) external view returns (LPPositionData memory data) {
        IUniswapV2Pair p = IUniswapV2Pair(pair);

        data.lpBalance = p.balanceOf(account);
        data.token0 = p.token0();
        data.token1 = p.token1();

        if (data.lpBalance > 0) {
            (uint112 reserve0, uint112 reserve1,) = p.getReserves();
            uint256 totalSupply = p.totalSupply();

            // Get oracle prices
            (uint256 price0, uint8 dec0) = OracleIntegration.getPrice(oracle0);
            (uint256 price1, uint8 dec1) = OracleIntegration.getPrice(oracle1);

            // Normalize prices to 1e18
            uint256 price0_1e18 = (price0 * 1e18) / (10 ** dec0);
            uint256 price1_1e18 = (price1 * 1e18) / (10 ** dec1);

            // Calculate fair LP price using Alpha Homora formula
            uint256 fairPricePerToken = LPPricing.fairLPPrice(
                uint256(reserve0),
                uint256(reserve1),
                price0_1e18,
                price1_1e18,
                totalSupply
            );

            data.fairValue_1e18 = (data.lpBalance * fairPricePerToken) / 1e18;
        }
    }

    // --- Reserve Protocol RToken Detection ---

    /// @notice Reads Reserve Protocol RToken over-collateralization data
    /// @dev Detects when basketsNeeded > totalSupply, indicating exploitable over-collateralization.
    ///      This is the production equivalent of the POC exploit detection.
    /// @param rToken The RToken contract address (e.g., ETH+ at 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8)
    /// @return data The RToken position data with collateral analysis
    function getRTokenPosition(address rToken) external view returns (RTokenPositionData memory data) {
        IRToken token = IRToken(rToken);

        data.totalSupply = token.totalSupply();
        data.basketsNeeded = uint256(token.basketsNeeded());

        if (data.totalSupply > 0) {
            // Calculate collateral ratio (basketsNeeded / totalSupply)
            data.collateralRatio = (data.basketsNeeded * 1e18) / data.totalSupply;
        }

        // Detect over-collateralization
        if (data.basketsNeeded > data.totalSupply) {
            data.isOverCollateralized = true;
            data.excessBaskets = data.basketsNeeded - data.totalSupply;
            // Profit per token in 1e18 basis (excess / supply)
            if (data.totalSupply > 0) {
                data.profitPerToken = (data.excessBaskets * 1e18) / data.totalSupply;
            }
        }

        // Get collateral quote for 1 RToken (basket composition)
        try token.main() returns (address mainAddr) {
            try IMain(mainAddr).basketHandler() returns (address handler) {
                try IBasketHandler(handler).quote(uint192(1e18), 0) returns (
                    address[] memory tokens,
                    uint256[] memory amounts
                ) {
                    data.collateralTokens = tokens;
                    data.collateralAmounts = amounts;
                } catch {}
            } catch {}
        } catch {}
    }

    /// @notice Calculates the maximum extractable value from an over-collateralized RToken
    /// @dev Production-grade profit analysis replacing the POC calculateMaxProfit
    /// @param rToken The RToken contract address
    /// @param basketValueUsd The estimated USD value per basket (1e18 scaled, e.g., 1800e18)
    /// @return excessBaskets Number of excess baskets beyond what's needed
    /// @return profitBps Profit per token in basis points
    /// @return totalProfitUsd Estimated total extractable profit in USD (1e18 scaled)
    /// @return isOpportunity True if over-collateralized
    function analyzeRTokenProfitability(address rToken, uint256 basketValueUsd)
        external
        view
        returns (
            uint256 excessBaskets,
            uint256 profitBps,
            uint256 totalProfitUsd,
            bool isOpportunity
        )
    {
        IRToken token = IRToken(rToken);

        uint256 supply = token.totalSupply();
        uint256 needed = uint256(token.basketsNeeded());

        if (needed > supply && supply > 0) {
            isOpportunity = true;
            excessBaskets = needed - supply;
            profitBps = (excessBaskets * 10000) / supply;
            totalProfitUsd = (excessBaskets * basketValueUsd) / 1e18;
        }
    }

    // --- Heuristic Protocol Classification ---

    /// @notice Attempts to classify an unknown contract by probing known DeFi selectors
    /// @dev Tries calling known view functions and catches reverts
    /// @param target The contract address to classify
    /// @return protocolType The detected protocol type
    function classifyProtocol(address target) external view returns (ProtocolType protocolType) {
        // Check getReserves() → Uniswap v2 pair (0x0902f1ac)
        (bool success,) = target.staticcall(abi.encodeWithSelector(0x0902f1ac));
        if (success) return ProtocolType.UniswapV2;

        // Check basketsNeeded() → Reserve Protocol RToken (0x77d9f986)
        (success,) = target.staticcall(abi.encodeWithSelector(IRToken.basketsNeeded.selector));
        if (success) {
            // Also verify main() exists to confirm Reserve Protocol
            (bool hasMain,) = target.staticcall(abi.encodeWithSelector(IRToken.main.selector));
            if (hasMain) return ProtocolType.ReserveProtocol;
        }

        // Check totalAssets() → ERC-4626 vault (0x01e1d114)
        (success,) = target.staticcall(abi.encodeWithSelector(0x01e1d114));
        if (success) {
            // Also check asset() to confirm ERC-4626
            (bool hasAsset,) = target.staticcall(abi.encodeWithSelector(0x38d52e0f));
            if (hasAsset) return ProtocolType.ERC4626Vault;
        }

        // Check pricePerShare() → Yearn v2 vault (0x99530b06)
        (success,) = target.staticcall(abi.encodeWithSelector(0x99530b06));
        if (success) return ProtocolType.YearnV2;

        // Check latestRoundData() → Chainlink oracle (0xfeaf968c)
        (success,) = target.staticcall(abi.encodeWithSelector(0xfeaf968c));
        if (success) return ProtocolType.ChainlinkOracle;

        // Check get_virtual_price() → Curve pool (0x07a2d13a)
        (success,) = target.staticcall(abi.encodeWithSelector(0x07a2d13a));
        if (success) return ProtocolType.CurvePool;

        return ProtocolType.Unknown;
    }

    // --- Batch Operations via Multicall3 ---

    /// @notice Builds a Multicall3 call to read Aave v2 position
    /// @param pool The Aave v2 pool address
    /// @param user The user address
    /// @return call The Multicall3 Call3 struct
    function buildAaveV2Call(address pool, address user)
        external
        pure
        returns (IMulticall3.Call3 memory call)
    {
        call.target = pool;
        call.allowFailure = true;
        call.callData = abi.encodeWithSelector(
            IAaveV2LendingPool.getUserAccountData.selector,
            user
        );
    }

    /// @notice Builds a Multicall3 call to read Compound v2 liquidity
    /// @param comptroller The Comptroller address
    /// @param account The user address
    /// @return call The Multicall3 Call3 struct
    function buildCompoundV2Call(address comptroller, address account)
        external
        pure
        returns (IMulticall3.Call3 memory call)
    {
        call.target = comptroller;
        call.allowFailure = true;
        call.callData = abi.encodeWithSelector(
            ICompoundV2Comptroller.getAccountLiquidity.selector,
            account
        );
    }

    // --- Utility Functions ---

    /// @notice Classifies the over-collateralization level
    /// @param collateralRatio The collateral ratio (1e18 = 100%)
    /// @return level 0=under, 1=at-risk(100-120%), 2=normal(120-200%), 3=well(200-300%),
    ///               4=significant(300-500%), 5=extreme(500-1000%), 6=outlier(>1000%)
    function classifyCollateralization(uint256 collateralRatio) external pure returns (uint8 level) {
        if (collateralRatio >= EXTREME_OUTLIER) return 6;
        if (collateralRatio >= EXTREMELY_CONSERVATIVE) return 5;
        if (collateralRatio >= SIGNIFICANTLY_OVER_COLLATERALIZED) return 4;
        if (collateralRatio >= WELL_COLLATERALIZED) return 3;
        if (collateralRatio >= AT_RISK) return 2;
        if (collateralRatio >= 1e18) return 1; // 100-120% = at risk
        return 0; // Under-collateralized
    }
}
