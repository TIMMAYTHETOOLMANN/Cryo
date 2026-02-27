# 🔍 OCDS — Over-Collateralization Detection System

## 📋 Overview

Production-grade multi-chain DeFi over-collateralization detection and execution system, part of the **FALCON** security research ecosystem. Scans, classifies, and monitors over-collateralized positions across **8 EVM-compatible networks** with Multicall3 batching and Chainlink oracle integration.

Includes full production mainnet execution capability for Reserve Protocol RToken arbitrage (mint/redeem cycle when basketsNeeded > totalSupply).

## 🎯 Detection Capability

- **Protocol Coverage**: Aave v2/v3, Compound v2/v3, MakerDAO, Uniswap v2/v3, Curve, Balancer v2, ERC-4626 vaults, Yearn v2, Beefy, **Reserve Protocol RTokens**
- **Network Coverage**: Ethereum, Arbitrum, Optimism, Polygon, Base, Avalanche, BSC, zkSync Era
- **Identification Capacity**: 14 protocol types × 8 networks = **112 identification targets**
- **Classification Levels**: 7-level granularity (Under → At-Risk → Normal → Well → Significant → Extreme → Outlier)

## 🛠️ Setup

### Prerequisites
- Docker & Docker Compose (for production scanning)
- Foundry (for local development)
- Multi-chain RPC endpoints (Alchemy recommended)

### Installation
```bash
# Clone and install dependencies
git clone --recurse-submodules <repo-url>
cd Cryo

# Or if already cloned:
git submodule update --init --recursive

# Configure environment
cp .env.template .env
# Edit .env with your RPC URLs for each network
```

## 🚀 Quick Start

### Build
```bash
forge build
```

### Run Tests
```bash
# Run all unit tests (no fork required)
forge test -vvv --match-contract "CollateralizationDetectorTest|CrossChainDetectorTest"
```

### Run Mainnet Scanner (scan-only)
```bash
# Scan for over-collateralization opportunities
forge script script/MainnetScanner.s.sol:MainnetScanner \
    --rpc-url "${MAINNET_RPC_URL}" -vvv
```

### Run Mainnet Scanner (with execution)
```bash
# Scan and execute profitable arbitrage
forge script script/MainnetScanner.s.sol:MainnetScanner \
    --rpc-url "${MAINNET_RPC_URL}" \
    --private-key "${PRIVATE_KEY}" \
    --broadcast -vvv
```

### Run with Docker
```bash
# Build and start the production scanner
docker compose up --build -d

# View logs
docker compose logs -f ocds-scanner

# Stop
docker compose down
```

## 📁 Project Structure

```
├── contracts/
│   ├── CollateralizationDetector.sol   # Multi-protocol detection (Aave, Compound, Maker, ERC-4626, LP, Reserve)
│   ├── CrossChainDetector.sol          # Multi-network scanning with L2 sequencer safety
│   ├── OracleIntegration.sol           # Chainlink oracle integration with staleness validation
│   ├── LPPricing.sol                   # Manipulation-resistant LP token pricing (Alpha Homora)
│   └── interfaces/                     # Protocol interface definitions
│       ├── IAaveV2.sol / IAaveV3.sol
│       ├── ICompoundV2.sol / ICompoundV3.sol
│       ├── IMakerDAO.sol
│       ├── IChainlinkOracle.sol
│       ├── IERC4626.sol
│       ├── IUniswapV2.sol / IUniswapV3.sol
│       ├── ICurve.sol / IBalancerV2.sol
│       ├── IMulticall3.sol
│       └── IReserveProtocol.sol        # Reserve Protocol (RToken, BasketHandler, SwapRouter)
├── script/
│   └── MainnetScanner.s.sol            # Production mainnet scanner and execution engine
├── CollateralizationDetector.t.sol     # Detection system unit tests
├── CrossChainDetectorTest.t.sol        # Cross-chain detection unit tests
├── Dockerfile                          # Production scanner container
├── docker-compose.yml                  # Container orchestration
├── entrypoint.sh                       # Scanner entrypoint (scan-only or with execution)
├── .env.template                       # Multi-chain RPC and execution configuration
├── foundry.toml                        # Foundry configuration
└── README.md
```

## 🔍 Classification Thresholds

| Level | Ratio | Classification | Action |
|-------|-------|----------------|--------|
| 0 | < 100% | Under-collateralized | CRITICAL — liquidation risk |
| 1 | 100% – 120% | At-Risk | WARNING — elevated monitoring |
| 2 | 120% – 200% | Normal | INFO — standard monitoring |
| 3 | 200% – 300% | Well-collateralized | DETECTION — flag for analysis |
| 4 | 300% – 500% | Significantly over | DETECTION — high priority |
| 5 | 500% – 1000% | Extremely conservative | DETECTION — capital inefficiency |
| 6 | > 1000% | Extreme outlier | DETECTION — investigate dormant positions |

## 🌐 Supported Networks

| Network | Chain ID | Aave V3 | Compound V3 | L2 Sequencer | Multicall3 |
|---------|----------|---------|-------------|--------------|------------|
| Ethereum | 1 | ✅ | ✅ | N/A | Standard |
| Arbitrum | 42161 | ✅ | ✅ | ✅ | Standard |
| Optimism | 10 | ✅ | ✅ | ✅ | Standard |
| Polygon | 137 | ✅ | ✅ | N/A | Standard |
| Base | 8453 | ✅ | ✅ | ✅ | Standard |
| Avalanche | 43114 | ✅ | — | N/A | Standard |
| BSC | 56 | — | — | N/A | Standard |
| zkSync Era | 324 | — | — | — | zkSync-specific |

## 🏦 Reserve Protocol Integration

The system provides full production-grade Reserve Protocol over-collateralization detection and execution:

### Detection
- `getRTokenPosition(rToken)` — Reads totalSupply, basketsNeeded, excess baskets, collateral ratio, and basket composition
- `analyzeRTokenProfitability(rToken, basketValueUsd)` — Computes extractable profit in basis points and USD
- `classifyProtocol(target)` — Automatically identifies Reserve Protocol contracts via `basketsNeeded()` + `main()` selector probing

### Known RTokens
| Token | Address | Type |
|-------|---------|------|
| ETH+ | `0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8` | ETH-backed |
| eUSD | `0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F` | USD-backed |

### Execution Flow (MainnetScanner.s.sol)
1. **Reconnaissance**: Scan all known RTokens for over-collateralization
2. **Profitability Analysis**: Calculate excess baskets, profit bps, gas cost vs profit
3. **Collateral Acquisition**: Swap ETH → WETH → collateral tokens via Uniswap V3 (5% slippage tolerance)
4. **Mint**: Issue RTokens using acquired collateral
5. **Redeem**: Burn RTokens to recover proportional collateral (which exceeds input due to over-collateralization)
6. **Profit Logging**: Detailed per-token profit summary

### Configuration

| Variable | Description | Default |
|---|---|---|
| `MAINNET_RPC_URL` | Ethereum RPC endpoint | — |
| `PRIVATE_KEY` | Wallet private key (empty = scan-only) | — |
| `MIN_PROFIT_WEI` | Minimum profit to execute (wei) | `10000000000000000` (0.01 ETH) |
| `GAS_PRICE_CAP_GWEI` | Max gas price (gwei) | `50` |
| `SCAN_INTERVAL` | Seconds between checks | `300` |

## 🔐 Security

- **Read-Only Default**: Scan-only mode when no PRIVATE_KEY is set
- **Oracle Safety**: Chainlink staleness validation (1.5× heartbeat), zero-price rejection, L2 sequencer uptime checks
- **LP Pricing**: Alpha Homora formula resistant to flash loan manipulation
- **Gas Protection**: Configurable gas price cap and minimum profit threshold prevent unprofitable executions
- **Slippage Protection**: 5% slippage tolerance on DEX swaps

## 🏷️ Tags

`defi-security` `over-collateralization` `multi-chain` `detection` `monitoring` `reserve-protocol` `falcon`

---

**Built with Foundry** — Blazing fast Ethereum development
