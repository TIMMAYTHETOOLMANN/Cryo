# 🔍 OCDS — Over-Collateralization Detection System

## 📋 Overview

Production-grade multi-chain DeFi over-collateralization detection system, part of the **FALCON** security research ecosystem. Scans, classifies, and monitors over-collateralized positions across **8 EVM-compatible networks** with Multicall3 batching and Chainlink oracle integration.

**Mode**: Read-only detection and monitoring — no transactions, no exploitation.

## 🎯 Detection Capability

- **Protocol Coverage**: Aave v2/v3, Compound v2/v3, MakerDAO, Uniswap v2/v3, Curve, Balancer v2, ERC-4626 vaults, Yearn v2, Beefy
- **Network Coverage**: Ethereum, Arbitrum, Optimism, Polygon, Base, Avalanche, BSC, zkSync Era
- **Identification Capacity**: 13 protocol types × 8 networks = **104 identification targets**
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
│   ├── CollateralizationDetector.sol   # Multi-protocol detection (Aave, Compound, Maker, ERC-4626, LP)
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
│       └── IMulticall3.sol
├── CollateralizationDetector.t.sol     # Detection system unit tests
├── CrossChainDetectorTest.t.sol        # Cross-chain detection unit tests
├── Dockerfile                          # Production scanner container
├── docker-compose.yml                  # Container orchestration
├── entrypoint.sh                       # Scanner entrypoint
├── .env.template                       # Multi-chain RPC configuration template
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

## 🔐 Security

- **Read-Only**: Only `eth_call` and `eth_getLogs` operations — never submits transactions
- **Oracle Safety**: Chainlink staleness validation (1.5× heartbeat), zero-price rejection, L2 sequencer uptime checks
- **LP Pricing**: Alpha Homora formula resistant to flash loan manipulation
- **No Key Management**: No private keys stored or used

## 🏷️ Tags

`defi-security` `over-collateralization` `multi-chain` `detection` `monitoring` `falcon`

---

**Built with Foundry** — Blazing fast Ethereum development
