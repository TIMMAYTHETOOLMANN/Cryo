// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IUniswapV3NonfungiblePositionManager
/// @notice Interface for Uniswap v3 NFT Position Manager
/// @dev Mainnet: 0xC36442b4a4522E871399CD717aBDD847Ab11FE88
interface IUniswapV3NonfungiblePositionManager {
    /// @notice Returns position data for a given token ID
    /// @param tokenId The NFT token ID
    function positions(uint256 tokenId)
        external
        view
        returns (
            uint96 nonce,
            address operator,
            address token0,
            address token1,
            uint24 fee,
            int24 tickLower,
            int24 tickUpper,
            uint128 liquidity,
            uint256 feeGrowthInside0LastX128,
            uint256 feeGrowthInside1LastX128,
            uint128 tokensOwed0,
            uint128 tokensOwed1
        );

    /// @notice Returns the total number of positions
    function totalSupply() external view returns (uint256);

    /// @notice Returns the owner of a position NFT
    function ownerOf(uint256 tokenId) external view returns (address);
}

/// @title IUniswapV3Pool
/// @notice Interface for Uniswap v3 Pool
interface IUniswapV3Pool {
    /// @notice Returns the current price and tick
    function slot0()
        external
        view
        returns (
            uint160 sqrtPriceX96,
            int24 tick,
            uint16 observationIndex,
            uint16 observationCardinality,
            uint16 observationCardinalityNext,
            uint8 feeProtocol,
            bool unlocked
        );

    function token0() external view returns (address);
    function token1() external view returns (address);
    function fee() external view returns (uint24);
    function liquidity() external view returns (uint128);
}

/// @title IUniswapV3Factory
/// @notice Interface for Uniswap v3 Factory
/// @dev PoolCreated event topic0: 0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118
interface IUniswapV3Factory {
    function getPool(address tokenA, address tokenB, uint24 fee) external view returns (address pool);
}
