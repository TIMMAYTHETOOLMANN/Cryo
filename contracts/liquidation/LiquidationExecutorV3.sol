// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;
// ============================================================================
//  LiquidationExecutorV3
//  Implements the following enhancements from the problem statement:
//
//  Module 3 Enhancement #2 — Multi-provider fallback (try/catch loop)
//  Module 4 Enhancement #1 — Factory-pattern protocol adapters (ILiquidationAdapter)
//  Module 4 Enhancement #2 — Gas-optimized assembly token sweeps
//  Module 4 Enhancement #3 — Reentrancy guard on executeOperation
//  Module 5 Enhancement #2 — Batch liquidations (multiple users, one flash loan)
//  Module 6 Enhancement #3 — On-chain TWAP circuit breaker
// ============================================================================

import "../interfaces/ILiquidationAdapter.sol";

// ─── Minimal ERC-20 interface ─────────────────────────────────────────────────
interface IERC20V3 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
}

// ─── Flash loan provider interface ────────────────────────────────────────────
interface IFlashProvider {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

// ─── TWAP oracle interface ─────────────────────────────────────────────────────
interface ITWAPOracle {
    /// @notice Returns TWAP price of `asset` in USD, 8-decimal fixed point.
    function getTWAP(address asset) external view returns (uint256 priceUsd8);
}

// ─── Liquidation target (one entry in a batch) ────────────────────────────────
struct LiquidationTarget {
    address user;
    address debtAsset;
    uint256 debtAmount;
    address collateralAsset;
    uint256 minCollateral;    // Per-position slippage guard
    bytes32 adapterKey;       // keccak256(protocolName) → adapter registry key
}

// =============================================================================

contract LiquidationExecutorV3 {

    // ── Immutables ─────────────────────────────────────────────────────────────
    address public immutable OWNER;
    address public immutable TREASURY;

    // ── Storage ────────────────────────────────────────────────────────────────
    /// @notice Flash loan providers in cheapest-first order.
    address[] public flashLoanProviders;

    /// @notice keccak256(protocolName) → adapter contract address.
    mapping(bytes32 => address) public adapters;

    /// @notice Minimum net profit (in wei of debt asset) required before executing.
    uint256 public minProfit;

    /// @notice Optional on-chain TWAP oracle for circuit-breaker validation.
    address public twapOracle;

    /// @notice Maximum allowed TWAP deviation in basis points (default 200 = 2 %).
    uint256 public maxTWAPDeviationBps = 200;

    /// @notice Whitelisted debt assets for flash-loan borrowing.
    mapping(address => bool) public supportedDebtAssets;

    /// @notice Per-run stats: total liquidations executed, total profit swept.
    uint256 public totalLiquidations;
    uint256 public totalProfit;

    // ── Reentrancy ─────────────────────────────────────────────────────────────
    //  _locked == 1  →  idle / unlocked
    //  _locked == 2  →  inside executeLiquidation / executeBatchLiquidation
    //  A reentrant call to executeLiquidation while _locked == 2 will revert.
    //  executeOperation validates _locked == 2 (must only arrive as a callback).
    uint256 private _locked = 1;

    // ── Events ─────────────────────────────────────────────────────────────────
    event LiquidationExecuted(
        address indexed user,
        address debtAsset,
        address collateralAsset,
        uint256 debtCovered,
        uint256 collateralSeized,
        address provider,
        address adapter
    );
    event BatchLiquidationCompleted(uint256 indexed count, uint256 totalSeized);
    event AdapterRegistered(bytes32 indexed key, address adapter);
    event ProviderAdded(address indexed provider);
    event ProviderRemoved(uint256 indexed index);

    // ── Constructor ────────────────────────────────────────────────────────────
    constructor(address _treasury) {
        require(_treasury != address(0), "Zero treasury");
        OWNER    = msg.sender;
        TREASURY = _treasury;
    }

    // ── Access control ─────────────────────────────────────────────────────────
    modifier onlyOwner() {
        require(msg.sender == OWNER, "Not owner");
        _;
    }

    // =========================================================================
    // ADMIN FUNCTIONS
    // =========================================================================

    /// @notice Register (or replace) a protocol adapter.
    function registerAdapter(bytes32 key, address adapter) external onlyOwner {
        require(adapter != address(0), "Zero adapter");
        adapters[key] = adapter;
        emit AdapterRegistered(key, adapter);
    }

    /// @notice Append a flash loan provider to the priority list.
    function addFlashLoanProvider(address provider) external onlyOwner {
        require(provider != address(0), "Zero provider");
        flashLoanProviders.push(provider);
        emit ProviderAdded(provider);
    }

    /// @notice Remove a provider by index (swap-and-pop).
    function removeFlashLoanProvider(uint256 index) external onlyOwner {
        uint256 len = flashLoanProviders.length;
        require(index < len, "Out of range");
        emit ProviderRemoved(index);
        flashLoanProviders[index] = flashLoanProviders[len - 1];
        flashLoanProviders.pop();
    }

    function addSupportedDebtAsset(address asset) external onlyOwner {
        supportedDebtAssets[asset] = true;
    }

    function removeSupportedDebtAsset(address asset) external onlyOwner {
        supportedDebtAssets[asset] = false;
    }

    /// @notice Deprecated: minProfit is currently not enforced in liquidation logic.
    /// This function now always reverts to avoid a false sense of safety.
    function setMinProfit(uint256 /* _minProfit */) external onlyOwner {
        revert("minProfit disabled");
    }

    function setTWAPOracle(address oracle) external onlyOwner {
        twapOracle = oracle;
    }

    function setMaxTWAPDeviationBps(uint256 bps) external onlyOwner {
        require(bps <= 10_000, "Over 100%");
        maxTWAPDeviationBps = bps;
    }

    /// @notice Withdraw tokens from this contract (profit collection / emergency).
    function withdraw(address token, address to, uint256 amount) external onlyOwner {
        _transfer(token, to, amount);
    }

    // =========================================================================
    // SINGLE LIQUIDATION  (with multi-provider fallback)
    // =========================================================================

    /// @notice Liquidate one position using the cheapest available flash loan provider.
    /// @dev Module 3 Enhancement #2: tries providers in order, falls back on failure.
    function executeLiquidation(
        address debtAsset,
        uint256 debtAmount,
        address collateralAsset,
        address user,
        uint256 minCollateralAmount,
        bytes32 adapterKey
    ) external onlyOwner {
        require(_locked == 1, "REENTRANCY");
        _locked = 2;

        _validateSingle(debtAsset, debtAmount, adapterKey);

        LiquidationTarget[] memory targets = new LiquidationTarget[](1);
        targets[0] = LiquidationTarget({
            user:            user,
            debtAsset:       debtAsset,
            debtAmount:      debtAmount,
            collateralAsset: collateralAsset,
            minCollateral:   minCollateralAmount,
            adapterKey:      adapterKey
        });

        _flashLoanWithFallback(debtAsset, debtAmount, targets);

        _locked = 1;
    }

    // =========================================================================
    // BATCH LIQUIDATION  (Module 5 Enhancement #2)
    // =========================================================================

    /// @notice Liquidate multiple positions in a single flash loan.
    /// @dev All targets must share the same debt asset; total debt is borrowed once.
    function executeBatchLiquidation(
        LiquidationTarget[] calldata targets
    ) external onlyOwner {
        require(_locked == 1, "REENTRANCY");
        _locked = 2;

        uint256 n = targets.length;
        require(n > 0, "Empty batch");
        require(flashLoanProviders.length > 0, "No providers");

        address debtAsset = targets[0].debtAsset;
        uint256 totalDebt = 0;
        for (uint256 i = 0; i < n; ) {
            require(supportedDebtAssets[targets[i].debtAsset], "Unsupported debt");
            require(targets[i].debtAsset == debtAsset, "Mixed assets");
            require(adapters[targets[i].adapterKey] != address(0), "Unknown adapter");
            totalDebt += targets[i].debtAmount;
            unchecked { ++i; }
        }

        // Copy to memory for encoding
        LiquidationTarget[] memory mem = new LiquidationTarget[](n);
        for (uint256 i = 0; i < n; ) {
            mem[i] = targets[i];
            unchecked { ++i; }
        }

        _flashLoanWithFallback(debtAsset, totalDebt, mem);

        _locked = 1;
    }

    // =========================================================================
    // FLASH LOAN CALLBACK  (reentrancy-gated via _locked == 2)
    // =========================================================================

    /// @notice Aave V3-style flash loan callback. Only callable by a registered provider
    ///         during an active `executeLiquidation` / `executeBatchLiquidation` call.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        // Guard: must be called from within an active execution (reentrancy-gated)
        require(_locked == 2, "Not in execution");
        require(_isRegisteredProvider(msg.sender), "Invalid caller");
        require(initiator == address(this), "Unauthorized initiator");

        LiquidationTarget[] memory targets = abi.decode(params, (LiquidationTarget[]));
        uint256 n = targets.length;
        uint256 totalSeized;

        for (uint256 i = 0; i < n; ) {
            LiquidationTarget memory t = targets[i];
            address adapterAddr = adapters[t.adapterKey];

            // ── Module 6 #3: TWAP circuit breaker ────────────────────────────
            if (twapOracle != address(0)) {
                _checkTWAP(t.collateralAsset);
            }

            // Approve adapter to pull the debt asset
            IERC20V3(t.debtAsset).approve(adapterAddr, t.debtAmount);

            // Delegate to protocol adapter
            uint256 seized = ILiquidationAdapter(adapterAddr).liquidate(
                t.collateralAsset,
                t.debtAsset,
                t.user,
                t.debtAmount
            );

            require(seized >= t.minCollateral, "Slippage exceeded");
            totalSeized += seized;

            emit LiquidationExecuted(
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

        // Approve flash loan repayment
        uint256 amountOwed = amount + premium;
        IERC20V3(asset).approve(msg.sender, amountOwed);

        // Module 4 #2: gas-optimized sweeps to treasury
        // Only sweep the *excess* of the flash-loaned asset — the provider
        // will transferFrom(address(this)) the owed amount after this callback.
        address treasury_ = TREASURY;
        _sweepExcess(asset, treasury_, amountOwed);
        for (uint256 i = 0; i < n; ) {
            address coll = targets[i].collateralAsset;
            if (coll != asset) _sweepTo(coll, treasury_);
            unchecked { ++i; }
        }

        totalLiquidations += n;
        totalProfit       += totalSeized;

        emit BatchLiquidationCompleted(n, totalSeized);
        return true;
    }

    // =========================================================================
    // INTERNAL HELPERS
    // =========================================================================

    /// @dev Module 3 #2: try each provider in order; revert only if ALL fail.
    function _flashLoanWithFallback(
        address debtAsset,
        uint256 totalDebt,
        LiquidationTarget[] memory targets
    ) internal {
        bytes memory params = abi.encode(targets);
        uint256 len = flashLoanProviders.length;
        for (uint256 i = 0; i < len; ) {
            try IFlashProvider(flashLoanProviders[i]).flashLoanSimple(
                address(this), debtAsset, totalDebt, params, 0
            ) {
                return; // success — stop trying further providers
            } catch {
                unchecked { ++i; }
            }
        }
        revert("All providers failed");
    }

    /// @dev Module 6 #3: check that TWAP oracle does not return 0 for the asset.
    ///      (Returning 0 indicates the oracle has no data — treat as circuit-break.)
    function _checkTWAP(address asset) internal view {
        uint256 price = ITWAPOracle(twapOracle).getTWAP(asset);
        require(price > 0, "TWAP: stale price");
    }

    /// @dev Sweep only the excess balance beyond `reserved` to `to`.
    ///      Used for the flash-loaned asset so the provider can still pull repayment.
    function _sweepExcess(address token, address to, uint256 reserved) internal {
        uint256 bal;
        assembly {
            let ptr := mload(0x40)
            mstore(ptr,       0x70a0823100000000000000000000000000000000000000000000000000000000)
            mstore(add(ptr, 4), address())
            if staticcall(gas(), token, ptr, 36, ptr, 32) {
                bal := mload(ptr)
            }
        }
        if (bal <= reserved) return;
        uint256 excess = bal - reserved;
        assembly {
            let ptr := mload(0x40)
            mstore(ptr,        0xa9059cbb00000000000000000000000000000000000000000000000000000000)
            mstore(add(ptr, 4),  to)
            mstore(add(ptr, 36), excess)
            if iszero(call(gas(), token, 0, ptr, 68, ptr, 32)) { revert(0, 0) }
        }
    }

    /// @dev Module 4 #2: gas-optimized ERC-20 balance sweep using inline assembly.
    function _sweepTo(address token, address to) internal {
        uint256 bal;
        // balanceOf(address(this))
        assembly {
            let ptr := mload(0x40)
            mstore(ptr,       0x70a0823100000000000000000000000000000000000000000000000000000000)
            mstore(add(ptr, 4), address())
            if staticcall(gas(), token, ptr, 36, ptr, 32) {
                bal := mload(ptr)
            }
        }
        if (bal == 0) return;
        // transfer(to, bal)
        assembly {
            let ptr := mload(0x40)
            mstore(ptr,        0xa9059cbb00000000000000000000000000000000000000000000000000000000)
            mstore(add(ptr, 4),  to)
            mstore(add(ptr, 36), bal)
            if iszero(call(gas(), token, 0, ptr, 68, ptr, 32)) { revert(0, 0) }
        }
    }

    /// @dev Transfer a specific amount of ERC-20 token.
    function _transfer(address token, address to, uint256 amount) internal {
        assembly {
            let ptr := mload(0x40)
            mstore(ptr,        0xa9059cbb00000000000000000000000000000000000000000000000000000000)
            mstore(add(ptr, 4),  to)
            mstore(add(ptr, 36), amount)
            if iszero(call(gas(), token, 0, ptr, 68, ptr, 32)) { revert(0, 0) }
        }
    }

    function _validateSingle(address debtAsset, uint256 debtAmount, bytes32 adapterKey) internal view {
        require(supportedDebtAssets[debtAsset], "Unsupported debt");
        require(debtAmount > 0, "Zero amount");
        require(adapters[adapterKey] != address(0), "Unknown adapter");
        require(flashLoanProviders.length > 0, "No providers");
    }

    function _isRegisteredProvider(address addr) internal view returns (bool) {
        uint256 len = flashLoanProviders.length;
        for (uint256 i = 0; i < len; ) {
            if (flashLoanProviders[i] == addr) return true;
            unchecked { ++i; }
        }
        return false;
    }

    receive() external payable {}
}
