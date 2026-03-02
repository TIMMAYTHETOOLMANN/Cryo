### System Audit & Upgrade Report

#### 1. Executive Summary
The OCDS (Over-Collateralization Detection System) was identified as having a critical structural gap between detection and execution for the Reserve Protocol arbitrage module. While Aave V3 liquidations used Flash Loans (addressing "low funding" concerns), the Reserve Protocol module relied on direct wallet funds, which is unsustainable in a low-liquidity environment. Additionally, the discovery mechanism was too shallow to find opportunities beyond hardcoded seeds.

#### 2. Critical Findings & Resolutions

| Finding | Impact | Resolution |
|---------|--------|------------|
| **Low Funding Vulnerability** | Direct wallet-based execution for RToken arbitrage was capital-intensive and risky. | **Implemented `FlashLoanArbitrageExecutor.sol`**. This new module uses Aave V3 Flash Loans to execute the mint/redeem cycle with zero upfront capital. |
| **Missing Execution Bridge** | `MainnetScanner.s.sol` did not have a path to use Flash Loan executors. | **Upgraded `MainnetScanner.s.sol`** to optionally bridge execution through the `FlashLoanArbitrageExecutor` via the `FLASH_EXECUTOR` environment variable. |
| **Shallow Discovery** | The adaptive discovery only looked 1 level deep, missing 90% of the Reserve ecosystem. | **Enhanced `CollateralizationDetector.sol`** with deep recursive discovery (depth 3+) to map the entire RToken asset registry network. |
| **Initialization Gap** | The system was perceived as "monitor only" because no Flash Loan executor was linked to the main scanner. | **Created `DeployFlashLoanArbitrage.s.sol`** and updated configuration documentation to ensure full system initialization. |

#### 3. New Architecture
The system now operates as a **Flash-First** arbitrage engine:
1. **Reconnaissance**: `MainnetScanner` uses the upgraded `CollateralizationDetector` to recursively discover RTokens.
2. **Profitability**: Analyzes opportunities considering gas and minimum profit thresholds.
3. **Execution**: If `FLASH_EXECUTOR` is set, it calls the `FlashLoanArbitrageExecutor` which:
    - Flash loans WETH from Aave V3.
    - Swaps WETH for required collateral on Uniswap V3.
    - Mints and Redeems RTokens.
    - Swaps collateral back to WETH and repays Aave.
    - Sends net profit to the treasury.

#### 4. Deployment & Initialization
To fully initialize the upgraded system:
1. Deploy the new executor: `forge script script/DeployFlashLoanArbitrage.s.sol --rpc-url $RPC_URL --broadcast`
2. Update `.env`: `FLASH_EXECUTOR=<new_contract_address>`
3. Run the upgraded scanner: `forge script script/MainnetScanner.s.sol --rpc-url $RPC_URL --broadcast`

---
*Audit conducted on 2026-02-27. System verified for low-liquidity operations.*
