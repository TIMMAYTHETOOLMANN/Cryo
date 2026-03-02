// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IRToken
/// @notice Interface for Reserve Protocol RToken contracts (e.g., ETH+, eUSD)
/// @dev RTokens are basket-backed tokens that maintain a collateral ratio.
///      Over-collateralization exists when basketsNeeded() > totalSupply().
///      Detection selector for basketsNeeded: 0x77d9f986
interface IRToken {
    /// @notice Mint RTokens by depositing the required basket collateral
    /// @param amount Amount of RTokens to issue (1e18 scaled)
    function issue(uint256 amount) external;

    /// @notice Burn RTokens to redeem proportional collateral
    /// @param amount Amount of RTokens to redeem (1e18 scaled)
    function redeem(uint256 amount) external;

    /// @notice Returns the total supply of RTokens
    function totalSupply() external view returns (uint256);

    /// @notice Returns the balance of RTokens for an account
    function balanceOf(address account) external view returns (uint256);

    /// @notice Returns the number of baskets needed to back all circulating RTokens
    /// @dev When basketsNeeded > totalSupply, the token is over-collateralized
    function basketsNeeded() external view returns (uint192);

    /// @notice Returns the main controller contract address
    function main() external view returns (address);

    /// @notice Approve a spender to transfer RTokens
    function approve(address spender, uint256 amount) external returns (bool);
}

/// @title IMain
/// @notice Interface for Reserve Protocol Main controller
/// @dev Provides access to all subsystem components of an RToken deployment
interface IMain {
    /// @notice Returns the basket handler address
    function basketHandler() external view returns (address);

    /// @notice Returns the asset registry address
    function assetRegistry() external view returns (address);

    /// @notice Returns the backing manager address
    function backingManager() external view returns (address);
}

/// @title IAssetRegistry
/// @notice Interface for Reserve Protocol Asset Registry
/// @dev Tracks all assets registered for an RToken deployment
interface IAssetRegistry {
    /// @notice Returns the addresses of all registered assets
    function erc20s() external view returns (address[] memory);
}

/// @title IBasketHandler
/// @notice Interface for Reserve Protocol Basket Handler
/// @dev Manages the collateral basket composition and provides quotes
interface IBasketHandler {
    /// @notice Returns the token addresses and amounts needed to cover `amount` baskets
    /// @param amount Number of baskets to quote for (uint192 fixed-point, 1e18 = 1 basket)
    /// @param rounding 0 = FLOOR, 1 = CEIL
    /// @return tokens Array of collateral token addresses
    /// @return amounts Array of required collateral amounts
    function quote(uint192 amount, uint8 rounding)
        external
        view
        returns (address[] memory tokens, uint256[] memory amounts);

    /// @notice Returns the status of the basket backing
    /// @return status 0 = SOUND, 1 = DANGER, 2 = DISABLED
    function status() external view returns (uint8);
}

/// @title IBackingManager
/// @notice Interface for Reserve Protocol Backing Manager
/// @dev Manages RToken backing and auctions
interface IBackingManager {
    /// @notice Returns the current backing status
    /// @return status 0 = SOUND, 1 = DANGER, 2 = DISABLED
    function status() external view returns (uint8);
}

/// @title IERC20
/// @notice Minimal ERC-20 interface for collateral token interactions
interface IERC20Minimal {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
}

/// @title IWETH
/// @notice Interface for Wrapped Ether
interface IWETH is IERC20Minimal {
    function deposit() external payable;
}

/// @title ISwapRouter
/// @notice Interface for Uniswap V3 SwapRouter
/// @dev Mainnet: 0xE592427A0AEce92De3Edee1F18E0157C05861564
interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params)
        external
        payable
        returns (uint256 amountOut);

    struct ExactOutputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountOut;
        uint256 amountInMaximum;
        uint160 sqrtPriceLimitX96;
    }

    function exactOutputSingle(ExactOutputSingleParams calldata params)
        external
        payable
        returns (uint256 amountIn);
}
