// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;
// ============================================================================
//  LiquidationExecutor - Flash-Loan-Powered Aave V3 Liquidation Engine
// ============================================================================
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function approve(address, uint256) external returns (bool);
}
interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;
}
contract LiquidationExecutor {
    address public immutable TREASURY;
    address public immutable OWNER;
    IPool   public immutable POOL;
    uint256 public minProfit;
    mapping(address => bool) public supportedDebtAssets;
    uint256 private _locked = 1;
    modifier nonReentrant() {
        require(_locked == 1, "REENTRANCY");
        _locked = 2;
        _;
        _locked = 1;
    }
    event LiquidationExecuted(
        address indexed user,
        address debtAsset,
        address collateralAsset,
        uint256 debtCovered,
        uint256 collateralSeized,
        uint256 profit
    );
    event DebtAssetUpdated(address indexed asset, bool supported);
    event EmergencyWithdraw(address token, uint256 amount, address to);
    constructor(address _pool, address _treasury) {
        require(_pool != address(0), "Zero pool");
        require(_treasury != address(0), "Zero treasury");
        POOL = IPool(_pool);
        TREASURY = _treasury;
        OWNER = msg.sender;
    }
    modifier onlyOwner() {
        require(msg.sender == OWNER, "Not owner");
        _;
    }
    /// @notice Entry point - triggers flash loan liquidation
    function executeLiquidation(
        address debtAsset,
        uint256 debtAmount,
        address collateralAsset,
        address user,
        uint256 minCollateralAmount
    ) external onlyOwner nonReentrant {
        require(supportedDebtAssets[debtAsset], "Unsupported debt");
        require(debtAmount > 0, "Zero amount");
        POOL.flashLoanSimple(
            address(this),
            debtAsset,
            debtAmount,
            abi.encode(collateralAsset, user, minCollateralAmount),
            0
        );
    }
    /// @notice Aave V3 flash loan callback (IFlashLoanSimpleReceiver)
    /// @dev MUST be named executeOperation with this exact signature
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == address(POOL), "Invalid caller");
        require(initiator == address(this), "Unauthorized");
        (address collateralAsset, address user, uint256 minCollateral) =
            abi.decode(params, (address, address, uint256));
        uint256 collBefore = IERC20(collateralAsset).balanceOf(address(this));
        IERC20(asset).approve(address(POOL), amount);
        POOL.liquidationCall(collateralAsset, asset, user, amount, false);
        uint256 collAfter = IERC20(collateralAsset).balanceOf(address(this));
        uint256 collateralSeized = collAfter - collBefore;
        require(collateralSeized > 0, "Liquidation failed");
        require(collateralSeized >= minCollateral, "Slippage");
        uint256 amountOwed = amount + premium;
        IERC20(asset).approve(address(POOL), amountOwed);
        _sweep(collateralAsset);
        if (collateralAsset != asset) {
            _sweep(asset);
        }
        emit LiquidationExecuted(user, asset, collateralAsset, amount, collateralSeized, collateralSeized);
        return true;
    }
    function _sweep(address token) internal {
        uint256 bal = IERC20(token).balanceOf(address(this));
        if (bal > 0) {
            IERC20(token).transfer(TREASURY, bal);
        }
    }
    function addSupportedDebtAsset(address asset) external onlyOwner {
        supportedDebtAssets[asset] = true;
        emit DebtAssetUpdated(asset, true);
    }
    function removeSupportedDebtAsset(address asset) external onlyOwner {
        supportedDebtAssets[asset] = false;
        emit DebtAssetUpdated(asset, false);
    }
    function setMinProfit(uint256 _minProfit) external onlyOwner {
        minProfit = _minProfit;
    }
    function withdrawProfit(address token, uint256 amount, address to) external onlyOwner {
        IERC20(token).transfer(to, amount);
        emit EmergencyWithdraw(token, amount, to);
    }
    receive() external payable {}
}
