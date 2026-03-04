// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

// ============================================================================
//  FinancialExecutor — FIRE Core On-Chain Executor
//
//  Universal atomic plan executor for the Financial Operations Runtime
//  Environment (FIRE). Executes arbitrary sequences of operations atomically
//  using flash loans for zero-capital operation.
//
//  Features:
//    - Execute multi-step plans atomically (all-or-nothing)
//    - Optional per-step failure tolerance (allowFailure flag)
//    - Flash loan provider registry for zero-capital operation
//    - Profit collection and distribution to owner
//    - Emergency pause mechanism
//    - Reentrancy protection
// ============================================================================

// ─── Minimal ERC-20 interface ─────────────────────────────────────────────────
interface IERC20Fire {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
}

/// @notice A single execution step within a FIRE plan.
struct Step {
    address target;      // Contract to call
    bytes data;          // Encoded calldata
    uint256 value;       // ETH to send with call
    bool allowFailure;   // If true, continue even if this step reverts
}

/// @title FinancialExecutor
/// @notice Atomic multi-step plan executor for the FIRE system.
/// @dev Holds no funds directly; uses flash loans for zero-capital operation.
contract FinancialExecutor {

    // ── Immutables ─────────────────────────────────────────────────────────
    address public immutable OWNER;

    // ── Storage ────────────────────────────────────────────────────────────
    address[] public flashLoanProviders;
    bool public paused;

    // ── Stats ──────────────────────────────────────────────────────────────
    uint256 public totalPlansExecuted;
    uint256 public totalProfit;

    // ── Reentrancy ─────────────────────────────────────────────────────────
    uint256 private _locked = 1;

    // ── Events ─────────────────────────────────────────────────────────────
    event StepExecuted(uint256 indexed index, bool success, bytes returnData);
    event PlanExecuted(bytes32 indexed planHash, bool success, uint256 profit);
    event ProviderAdded(address indexed provider);
    event ProviderRemoved(uint256 indexed index, address provider);
    event PauseToggled(bool paused);

    // ── Constructor ────────────────────────────────────────────────────────
    constructor(address[] memory _providers) {
        OWNER = msg.sender;
        for (uint256 i = 0; i < _providers.length; i++) {
            require(_providers[i] != address(0), "Zero provider address");
            flashLoanProviders.push(_providers[i]);
        }
    }

    // ── Modifiers ──────────────────────────────────────────────────────────
    modifier onlyOwner() {
        require(msg.sender == OWNER, "Not owner");
        _;
    }

    modifier whenNotPaused() {
        require(!paused, "Executor paused");
        _;
    }

    // ── Core Execution ─────────────────────────────────────────────────────

    /// @notice Execute a plan (list of steps) atomically.
    /// @dev The plan must include its own flash loan logic if needed.
    ///      This contract does not hold funds permanently.
    /// @param steps Array of Step structs defining the execution plan.
    /// @return success True if the plan completed (all required steps succeeded).
    function executePlan(Step[] calldata steps)
        external
        payable
        onlyOwner
        whenNotPaused
        returns (bool success)
    {
        require(_locked == 1, "REENTRANCY");
        _locked = 2;

        require(steps.length > 0, "Empty plan");

        uint256 balanceBefore = address(this).balance;

        for (uint256 i = 0; i < steps.length; i++) {
            require(steps[i].target != address(0), "Zero target");
            (bool ok, bytes memory ret) = steps[i].target.call{value: steps[i].value}(
                steps[i].data
            );
            if (!ok && !steps[i].allowFailure) {
                // Revert with step index and error data for debugging
                _locked = 1;
                revert(string(abi.encodePacked("Step failed: ", _toAscii(i))));
            }
            emit StepExecuted(i, ok, ret);
        }

        uint256 balanceAfter = address(this).balance;
        uint256 profit = 0;
        if (balanceAfter > balanceBefore) {
            profit = balanceAfter - balanceBefore;
        }

        totalPlansExecuted++;
        totalProfit += profit;

        bytes32 planHash = keccak256(abi.encode(steps));
        emit PlanExecuted(planHash, true, profit);

        // Transfer profit to owner
        if (profit > 0) {
            (bool sent, ) = payable(OWNER).call{value: profit}("");
            require(sent, "Profit transfer failed");
        }

        _locked = 1;
        return true;
    }

    // ── Admin Functions ────────────────────────────────────────────────────

    /// @notice Add a flash loan provider to the registry.
    function addProvider(address provider) external onlyOwner {
        require(provider != address(0), "Zero provider");
        flashLoanProviders.push(provider);
        emit ProviderAdded(provider);
    }

    /// @notice Remove a flash loan provider by index (swap-and-pop).
    function removeProvider(uint256 index) external onlyOwner {
        require(index < flashLoanProviders.length, "Index out of bounds");
        address removed = flashLoanProviders[index];
        flashLoanProviders[index] = flashLoanProviders[flashLoanProviders.length - 1];
        flashLoanProviders.pop();
        emit ProviderRemoved(index, removed);
    }

    /// @notice Toggle the pause state.
    function setPaused(bool _paused) external onlyOwner {
        paused = _paused;
        emit PauseToggled(_paused);
    }

    /// @notice Withdraw ERC-20 tokens (emergency recovery).
    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        require(to != address(0), "Zero recipient");
        IERC20Fire(token).transfer(to, amount);
    }

    /// @notice Withdraw ETH (emergency recovery).
    function withdrawETH(address payable to, uint256 amount) external onlyOwner {
        require(to != address(0), "Zero recipient");
        (bool sent, ) = to.call{value: amount}("");
        require(sent, "ETH transfer failed");
    }

    /// @notice Get the number of registered flash loan providers.
    function providerCount() external view returns (uint256) {
        return flashLoanProviders.length;
    }

    // ── Fallback ───────────────────────────────────────────────────────────

    /// @notice Receive ETH from swaps, flash loans, etc.
    receive() external payable {}

    // ── Internal Helpers ───────────────────────────────────────────────────

    /// @dev Convert uint to ASCII string for error messages.
    function _toAscii(uint256 value) internal pure returns (string memory) {
        if (value == 0) return "0";
        uint256 temp = value;
        uint256 digits;
        while (temp != 0) {
            digits++;
            temp /= 10;
        }
        bytes memory buffer = new bytes(digits);
        while (value != 0) {
            digits -= 1;
            buffer[digits] = bytes1(uint8(48 + uint256(value % 10)));
            value /= 10;
        }
        return string(buffer);
    }
}
