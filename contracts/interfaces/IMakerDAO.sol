// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title IMakerVat
/// @notice Interface for MakerDAO Vat (core accounting engine)
/// @dev Mainnet: 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B
/// @dev Precision types: wad (1e18), ray (1e27), rad (1e45)
interface IMakerVat {
    /// @notice Returns urn data (collateral and debt)
    /// @param ilk The collateral type identifier
    /// @param urn The urn address
    /// @return ink Collateral amount (wad, 1e18)
    /// @return art Normalized debt (wad, 1e18). Actual debt = art × rate
    function urns(bytes32 ilk, address urn) external view returns (uint256 ink, uint256 art);

    /// @notice Returns ilk data
    /// @param ilk The collateral type identifier
    /// @return Art Total normalized debt (wad)
    /// @return rate Accumulated rate (ray, 1e27)
    /// @return spot Price with safety margin (ray)
    /// @return line Debt ceiling (rad, 1e45)
    /// @return dust Debt floor (rad)
    function ilks(bytes32 ilk)
        external
        view
        returns (uint256 Art, uint256 rate, uint256 spot, uint256 line, uint256 dust);
}

/// @title IMakerCdpManager
/// @notice Interface for MakerDAO CDP Manager
/// @dev Mainnet: 0x5ef30b9986345249bc32d8928B7ee64DE9435E39
interface IMakerCdpManager {
    /// @notice Returns the ilk for a CDP
    function ilks(uint256 cdp) external view returns (bytes32);

    /// @notice Returns the urn address for a CDP
    function urns(uint256 cdp) external view returns (address);

    /// @notice Returns the owner of a CDP
    function owns(uint256 cdp) external view returns (address);

    /// @notice Returns the last CDP ID
    function last(address owner) external view returns (uint256);

    /// @notice Returns the total count of CDPs
    function count(address owner) external view returns (uint256);
}

/// @title IMakerSpotter
/// @notice Interface for MakerDAO Spotter (oracle manager)
interface IMakerSpotter {
    /// @notice Returns ilk data from Spotter
    /// @param ilk The collateral type identifier
    /// @return pip Oracle address
    /// @return mat Liquidation ratio (ray, 1e27). E.g., 1.5e27 = 150%
    function ilks(bytes32 ilk) external view returns (address pip, uint256 mat);
}
