// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;
import "../interfaces/IReserveProtocol.sol";
interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}
/// @title FlashLoanArbitrageExecutor
/// @notice Zero-capital arbitrage for Reserve Protocol RTokens via Aave V3 flash loans
contract FlashLoanArbitrageExecutor {
    address public immutable OWNER;
    IPool   public immutable AAVE_POOL;
    address public immutable WETH;
    address public immutable UNISWAP_V3_ROUTER;
    uint24  public constant POOL_FEE = 3000;
    event ArbitrageExecuted(address indexed rToken, uint256 profitEth);
    constructor(address _owner, address _aavePool, address _weth, address _uniswapRouter) {
        OWNER = _owner;
        AAVE_POOL = IPool(_aavePool);
        WETH = _weth;
        UNISWAP_V3_ROUTER = _uniswapRouter;
    }
    function executeArbitrage(address rToken, uint256 flashLoanAmount) external {
        require(msg.sender == OWNER, "Not owner");
        AAVE_POOL.flashLoanSimple(
            address(this),
            WETH,
            flashLoanAmount,
            abi.encode(rToken),
            0
        );
    }
    /// @notice Aave V3 flash loan callback
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == address(AAVE_POOL), "Invalid caller");
        require(initiator == address(this), "Unauthorized");
        require(asset == WETH, "Only WETH supported");
        address rToken = abi.decode(params, (address));
        IRToken token = IRToken(rToken);
        address mainAddr = token.main();
        address basketHandler = IMain(mainAddr).basketHandler();
        uint256 mintAmount = 1e18;
        (address[] memory collateralTokens, uint256[] memory collateralAmounts) =
            IBasketHandler(basketHandler).quote(uint192(mintAmount), 1);
        // Swap WETH -> collateral tokens via Uniswap V3
        IERC20Minimal(WETH).approve(UNISWAP_V3_ROUTER, amount);
        uint24[] memory fees = new uint24[](3);
        fees[0] = 500;
        fees[1] = 3000;
        fees[2] = 10000;
        for (uint256 i = 0; i < collateralTokens.length; i++) {
            if (collateralTokens[i] == WETH) continue;
            bool swapped = false;
            uint256 maxWethIn = (collateralAmounts[i] * 120) / 100;
            for (uint256 f = 0; f < fees.length; f++) {
                try ISwapRouter(UNISWAP_V3_ROUTER).exactOutputSingle(
                    ISwapRouter.ExactOutputSingleParams({
                        tokenIn: WETH,
                        tokenOut: collateralTokens[i],
                        fee: fees[f],
                        recipient: address(this),
                        deadline: block.timestamp + 300,
                        amountOut: collateralAmounts[i],
                        amountInMaximum: maxWethIn,
                        sqrtPriceLimitX96: 0
                    })
                ) returns (uint256) {
                    swapped = true;
                    break;
                } catch {}
            }
            require(swapped, "Could not swap for collateral");
        }
        // Mint RToken
        for (uint256 i = 0; i < collateralTokens.length; i++) {
            IERC20Minimal(collateralTokens[i]).approve(
                rToken,
                IERC20Minimal(collateralTokens[i]).balanceOf(address(this))
            );
        }
        token.issue(mintAmount);
        // Redeem RToken
        uint256 rTokenBal = token.balanceOf(address(this));
        token.redeem(rTokenBal);
        // Swap collateral back to WETH
        for (uint256 i = 0; i < collateralTokens.length; i++) {
            if (collateralTokens[i] == WETH) continue;
            uint256 bal = IERC20Minimal(collateralTokens[i]).balanceOf(address(this));
            if (bal > 0) {
                IERC20Minimal(collateralTokens[i]).approve(UNISWAP_V3_ROUTER, bal);
                bool swappedBack = false;
                for (uint256 f = 0; f < fees.length; f++) {
                    try ISwapRouter(UNISWAP_V3_ROUTER).exactInputSingle(
                        ISwapRouter.ExactInputSingleParams({
                            tokenIn: collateralTokens[i],
                            tokenOut: WETH,
                            fee: fees[f],
                            recipient: address(this),
                            deadline: block.timestamp + 300,
                            amountIn: bal,
                            amountOutMinimum: 0,
                            sqrtPriceLimitX96: 0
                        })
                    ) returns (uint256) {
                        swappedBack = true;
                        break;
                    } catch {}
                }
                require(swappedBack, "Could not swap back to WETH");
            }
        }
        // Repay flash loan
        uint256 amountOwed = amount + premium;
        uint256 currentWethBal = IERC20Minimal(WETH).balanceOf(address(this));
        require(currentWethBal >= amountOwed, "Insufficient funds to repay flash loan");
        IERC20Minimal(WETH).approve(address(AAVE_POOL), amountOwed);
        // Transfer profit to owner
        uint256 profit = currentWethBal - amountOwed;
        if (profit > 0) {
            IERC20Minimal(WETH).transfer(OWNER, profit);
        }
        emit ArbitrageExecuted(rToken, profit);
        return true;
    }
    receive() external payable {}
}
