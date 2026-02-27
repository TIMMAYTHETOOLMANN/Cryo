# 🔓 RESERVE PROTOCOL EXPLOIT — MAINNET SYSTEM

## 📋 Overview

This project demonstrates a **critical over-collateralized arbitrage vulnerability** in the Reserve Protocol's ETH+ token. The exploit allows attackers to mint ETH+ tokens and immediately redeem them for ~5.9% profit due to excess collateral backing.

The repository includes both the original Proof-of-Concept and a **production-ready mainnet execution system** with Docker-based infrastructure, continuous monitoring, and automated execution.

## 🎯 Impact

- **Financial Loss**: $6.4M+ drainable reserves
- **Protocol Risk**: Affects ETH+ Reserve Token (0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8)
- **Risk Level**: HIGH - Active exploit opportunity

## 🛠️ Setup

### Prerequisites
- Docker & Docker Compose (for mainnet system)
- Foundry (for local development)
- Mainnet RPC endpoint (Alchemy, Infura, or QuickNode recommended)

### Installation
```bash
# Clone and install dependencies
git clone --recurse-submodules <repo-url>
cd Cryo

# Or if already cloned:
git submodule update --init --recursive

# Configure environment
cp .env.template .env
# Edit .env with your MAINNET_RPC_URL, PRIVATE_KEY, and thresholds
```

## 🚀 Quick Start (POC)

### 1. Build the project
```bash
forge build
```

### 2. Run tests
```bash
# Run the full exploit demonstration
forge test --match-contract FullExploitPOC -v

# Run reconnaissance
forge test --match-contract ReserveRecon -v

# Run all tests
forge test -v
```

### 3. Execute POC deploy script
```bash
forge script POCDeploy.s.sol
```

## 🏗️ Mainnet System

### Architecture

The mainnet system converts the POC into a continuously running arbitrage executor:

1. **Monitoring Loop** (`entrypoint.sh`): Polls the chain at a configurable interval.
2. **Execution Script** (`script/MainnetExploit.s.sol`): Checks the trigger condition, acquires collateral via Uniswap V3, and executes the mint/redeem cycle.
3. **Docker Infrastructure** (`Dockerfile` + `docker-compose.yml`): Isolated, reproducible environment with optional VPN routing.

### Running with Docker

```bash
# Build and start the continuous monitor
docker compose up --build -d

# View logs
docker compose logs -f arb-executor

# Stop
docker compose down
```

### Running Manually

```bash
# Single execution
forge script script/MainnetExploit.s.sol:MainnetExploit \
    --rpc-url "${MAINNET_RPC_URL}" \
    --private-key "${PRIVATE_KEY}" \
    --broadcast \
    -vvv
```

### Configuration

All settings are managed via `.env` (see `.env.template`):

| Variable | Description | Default |
|---|---|---|
| `MAINNET_RPC_URL` | Ethereum RPC endpoint | — |
| `PRIVATE_KEY` | Wallet private key | — |
| `MIN_PROFIT_WEI` | Minimum profit to execute (wei) | `10000000000000000` (0.01 ETH) |
| `GAS_PRICE_CAP_GWEI` | Max gas price (gwei) | `50` |
| `POLL_INTERVAL` | Seconds between checks | `300` |

### Network Isolation (VPN)

To route all RPC traffic through ExpressVPN, uncomment the `vpn` service in `docker-compose.yml` and set your activation code. The executor container will then route through the VPN sidecar.

## 📁 Project Structure

```
├── POC.sol                      # Original exploit contract
├── POCDeploy.s.sol              # Original deployment script
├── FullExploitPOC.t.sol         # Full exploit test suite
├── ReserveRecon.t.sol           # Reconnaissance test
├── script/
│   └── MainnetExploit.s.sol     # Mainnet execution script (the "brain")
├── Dockerfile                   # Foundry container image
├── docker-compose.yml           # Orchestration with optional VPN
├── entrypoint.sh                # Continuous monitoring loop
├── .env.template                # Environment variable template
├── .gitignore                   # Git ignore rules
├── foundry.toml                 # Foundry configuration
└── README.md                    # This file
```

## 🔍 Technical Details

### Trigger Condition
The exploit triggers when `Reserve.basketsNeeded() > totalSupply()`, indicating the protocol holds excess collateral (over-collateralization).

### Execution Flow (MainnetExploit.s.sol)
1. **Monitor**: Read `basketsNeeded` and `totalSupply` from ETH+.
2. **Evaluate**: Skip if not over-collateralized or profit < gas + threshold.
3. **Acquire Collateral**: Swap ETH → WETH → collateral tokens via Uniswap V3.
4. **Approve**: Grant ETH+ contract spending allowance.
5. **Mint**: Call `issue()` to create ETH+ backed by collateral.
6. **Redeem**: Call `redeem()` to convert ETH+ back to raw collateral.
7. **Profit**: Output collateral exceeds input due to over-collateralization.

### Capital Management
- The script estimates profit vs gas cost before executing.
- A configurable `MIN_PROFIT_WEI` threshold prevents unprofitable trades.
- A `GAS_PRICE_CAP_GWEI` cap prevents overpaying during network congestion.
- 5% slippage tolerance is applied to DEX swaps.

### Automation
- **Docker Loop**: The `entrypoint.sh` runs `forge script` every `POLL_INTERVAL` seconds.
- **Cloud Deployment**: The Docker image can be deployed to any cloud provider (AWS, GCP, Render) with a cron trigger or always-on container.

## ⚠️ Security Notice

**This is for educational/research purposes only.** The exploit demonstrates a real vulnerability that should be reported to the Reserve Protocol team for remediation.

## 📊 Current State (Block 23803481)

- **ETH+ Supply**: 60.4M tokens
- **Baskets Needed**: 63.9M baskets
- **Over-Collateralization**: 5.9%
- **Potential Profit**: $6.4M USD

## 🏷️ Tags

`reserve-protocol` `arbitrage` `over-collateralization` `exploit` `ethereum` `defi`

---

**Built with Foundry** - Blazing fast Ethereum development
