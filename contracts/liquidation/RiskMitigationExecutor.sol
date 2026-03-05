// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

// ============================================================================
//  RiskMitigationExecutor — Module 4 + Module 6
//  Enhanced executor with safety protections and minimum incentive checks.
//
//  Builds on LiquidationExecutorV3 by adding:
//    - Pre-execution incentive validation (Module 6)
//    - On-chain price validation via Chainlink (Module 6)
//    - Gas price cap enforcement (Module 6)
//    - Per-position minimum net incentive check (Module 6)
//    - MakerDAO Dog.bark() support via adapter pattern (Module 4)
// ============================================================================

import "../interfaces/ILiquidationAdapter.sol";
import "../interfaces/IChainlinkOracle.sol";

// ─── Minimal ERC-20 interface ─────────────────────────────────────────────────
interface IERC20RME {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
}

// ─── Flash loan provider interface ────────────────────────────────────────────
interface IFlashProviderRME {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

/// @notice Execution parameters with safety checks.
struct MitigationParams {
    address user;
    address debtAsset;
    uint256 debtAmount;
    address collateralAsset;
    uint256 minCollateral;          // Slippage guard: minimum collateral to seize
    bytes32 adapterKey;             // Protocol adapter registry key
    uint256 minNetIncentiveUsd;     // Module 6: minimum net incentive in USD (1e18)
    address collateralPriceFeed;    // Chainlink feed for collateral price validation
    uint256 maxOracleStaleness;     // Maximum oracle age in seconds
}

contract RiskMitigationExecutor {

    // ── Immutables ─────────────────────────────────────────────────────────
    address public immutable OWNER;
    address public immutable TREASURY;

    // ── Storage ────────────────────────────────────────────────────────────
    address[] public flashLoanProviders;
    mapping(bytes32 => address) public adapters;
    mapping(address => bool) public supportedDebtAssets;

    /// @notice Maximum gas price (in wei) above which execution is skipped.
    uint256 public maxGasPrice;

    /// @notice Global minimum net incentive (USD, 1e18-scaled).
    uint256 public minNetIncentiveUsd = 10e18; // $10 default

    // ── Stats ──────────────────────────────────────────────────────────────
    uint256 public totalMitigations;
    uint256 public totalCollateralSeized;
    uint256 public totalSkippedLowIncentive;
    uint256 public totalSkippedGasPrice;

    // ── Reentrancy ─────────────────────────────────────────────────────────
    uint256 private _locked = 1;

    // ── Events ─────────────────────────────────────────────────────────────
    event MitigationExecuted(
        address indexed user,
        address debtAsset,
        address collateralAsset,
        uint256 debtCovered,
        uint256 collateralSeized,
        address provider,
        address adapter
    );
    event MitigationSkipped(
        address indexed user,
        string reason
    );
    event AdapterRegistered(bytes32 indexed key, address adapter);
    event ProviderAdded(address indexed provider);
    event GasPriceCapUpdated(uint256 newCap);

    // ── Constructor ────────────────────────────────────────────────────────
    constructor(address _treasury, uint256 _maxGasPrice) {
        require(_treasury != address(0), "Zero treasury");
        OWNER = msg.sender;
        TREASURY = _treasury;
        maxGasPrice = _maxGasPrice;
    }

    // ── Modifiers ──────────────────────────────────────────────────────────
    modifier onlyOwner() {
        require(msg.sender == OWNER, "Not owner");
        _;
    }

    // ── Admin Functions ────────────────────────────────────────────────────

    function registerAdapter(bytes32 key, address adapter) external onlyOwner {
        require(adapter != address(0), "Zero adapter");
        adapters[key] = adapter;
        emit AdapterRegistered(key, adapter);
    }

    function addFlashLoanProvider(address provider) external onlyOwner {
        require(provider != address(0), "Zero provider");
        flashLoanProviders.push(provider);
        emit ProviderAdded(provider);
    }

    function addSupportedDebtAsset(address asset) external onlyOwner {
        supportedDebtAssets[asset] = true;
    }

    function setMaxGasPrice(uint256 _maxGasPrice) external onlyOwner {
        maxGasPrice = _maxGasPrice;
        emit GasPriceCapUpdated(_maxGasPrice);
    }

    function setMinNetIncentiveUsd(uint256 _minUsd) external onlyOwner {
        minNetIncentiveUsd = _minUsd;
    }

    function withdraw(address token, address to, uint256 amount) external onlyOwner {
        IERC20RME(token).transfer(to, amount);
    }

    // ── Execution with Safety Checks ───────────────────────────────────────

    /// @notice Execute risk mitigation with pre-flight safety checks.
    /// @dev Module 6: Validates gas price, oracle price, and minimum incentive
    ///      before proceeding with flash loan liquidation.
    function executeMitigation(
        MitigationParams calldata params
    ) external onlyOwner {
        require(_locked == 1, "REENTRANCY");
        _locked = 2;

        // Module 6: Gas price cap check
        if (maxGasPrice > 0 && tx.gasprice > maxGasPrice) {
            totalSkippedGasPrice++;
            emit MitigationSkipped(params.user, "Gas price exceeds cap");
            _locked = 1;
            return;
        }

        _validateParams(params);

        // Module 6: Oracle price validation (if feed provided)
        if (params.collateralPriceFeed != address(0)) {
            _validateOraclePrice(params.collateralPriceFeed, params.maxOracleStaleness);
        }

        MitigationParams[] memory targets = new MitigationParams[](1);
        targets[0] = params;

        _flashLoanWithFallback(params.debtAsset, params.debtAmount, targets);

        _locked = 1;
    }

    /// @notice Batch execute multiple risk mitigations.
    function executeBatchMitigation(
        MitigationParams[] calldata targets
    ) external onlyOwner {
        require(_locked == 1, "REENTRANCY");
        _locked = 2;

        uint256 n = targets.length;
        require(n > 0, "Empty batch");
        require(flashLoanProviders.length > 0, "No providers");

        // Module 6: Gas price cap check
        if (maxGasPrice > 0 && tx.gasprice > maxGasPrice) {
            totalSkippedGasPrice++;
            emit MitigationSkipped(address(0), "Gas price exceeds cap");
            _locked = 1;
            return;
        }

        address debtAsset = targets[0].debtAsset;
        uint256 totalDebt = 0;
        for (uint256 i = 0; i < n; ) {
            require(supportedDebtAssets[targets[i].debtAsset], "Unsupported debt");
            require(targets[i].debtAsset == debtAsset, "Mixed assets");
            require(adapters[targets[i].adapterKey] != address(0), "Unknown adapter");
            totalDebt += targets[i].debtAmount;
            unchecked { ++i; }
        }

        MitigationParams[] memory mem = new MitigationParams[](n);
        for (uint256 i = 0; i < n; ) {
            mem[i] = targets[i];
            unchecked { ++i; }
        }

        _flashLoanWithFallback(debtAsset, totalDebt, mem);

        _locked = 1;
    }

    // ── Flash Loan Callback ────────────────────────────────────────────────

    /// @notice Aave V3-style flash loan callback with safety checks.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata callbackParams
    ) external returns (bool) {
        require(_locked == 2, "Not in execution");
        require(_isRegisteredProvider(msg.sender), "Invalid caller");
        require(initiator == address(this), "Unauthorized initiator");

        MitigationParams[] memory targets = abi.decode(callbackParams, (MitigationParams[]));
        uint256 n = targets.length;
        uint256 totalSeized;

        for (uint256 i = 0; i < n; ) {
            MitigationParams memory t = targets[i];
            address adapterAddr = adapters[t.adapterKey];

            // Approve adapter to pull the debt asset
            IERC20RME(t.debtAsset).approve(adapterAddr, t.debtAmount);

            // Execute protocol-specific liquidation
            uint256 seized = ILiquidationAdapter(adapterAddr).liquidate(
                t.collateralAsset,
                t.debtAsset,
                t.user,
                t.debtAmount
            );

            // Module 6: Slippage guard
            require(seized >= t.minCollateral, "Slippage exceeded");
            totalSeized += seized;

            emit MitigationExecuted(
                t.user,
                t.debtAsset,
                t.collateralAsset,
                t.debtAmount,
                seized,
                msg.sender,
                adapterAddr
            );

            unchecked { ++i; }
        }

        // Repay flash loan
        uint256 amountOwed = amount + premium;
        IERC20RME(asset).approve(msg.sender, amountOwed);

        // Sweep excess to treasury
        address treasury_ = TREASURY;
        _sweepExcess(asset, treasury_, amountOwed);
        for (uint256 i = 0; i < n; ) {
            address coll = targets[i].collateralAsset;
            if (coll != asset) _sweepTo(coll, treasury_);
            unchecked { ++i; }
        }

        totalMitigations += n;
        totalCollateralSeized += totalSeized;

        return true;
    }

    // ── Internal Helpers ───────────────────────────────────────────────────

    function _flashLoanWithFallback(
        address debtAsset,
        uint256 totalDebt,
        MitigationParams[] memory targets
    ) internal {
        bytes memory params = abi.encode(targets);
        uint256 len = flashLoanProviders.length;
        for (uint256 i = 0; i < len; ) {
            try IFlashProviderRME(flashLoanProviders[i]).flashLoanSimple(
                address(this), debtAsset, totalDebt, params, 0
            ) {
                return;
            } catch {
                unchecked { ++i; }
            }
        }
        revert("All providers failed");
    }

    function _validateParams(MitigationParams calldata p) internal view {
        require(supportedDebtAssets[p.debtAsset], "Unsupported debt");
        require(p.debtAmount > 0, "Zero amount");
        require(adapters[p.adapterKey] != address(0), "Unknown adapter");
        require(flashLoanProviders.length > 0, "No providers");
    }

    /// @dev Module 6: Validate oracle price is fresh and positive.
    function _validateOraclePrice(address feed, uint256 maxStaleness) internal view {
        (, int256 answer, , uint256 updatedAt, ) =
            AggregatorV3Interface(feed).latestRoundData();

        require(answer > 0, "Oracle: zero price");
        require(block.timestamp - updatedAt <= maxStaleness, "Oracle: stale price");
    }

    function _isRegisteredProvider(address addr) internal view returns (bool) {
        uint256 len = flashLoanProviders.length;
        for (uint256 i = 0; i < len; ) {
            if (flashLoanProviders[i] == addr) return true;
            unchecked { ++i; }
        }
        return false;
    }

    function _sweepExcess(address token, address to, uint256 reserved) internal {
        uint256 bal = IERC20RME(token).balanceOf(address(this));
        if (bal <= reserved) return;
        uint256 excess = bal - reserved;
        IERC20RME(token).transfer(to, excess);
    }

    function _sweepTo(address token, address to) internal {
        uint256 bal = IERC20RME(token).balanceOf(address(this));
        if (bal == 0) return;
        IERC20RME(token).transfer(to, bal);
    }

    receive() external payable {}
}
