# 🔓 RESERVE PROTOCOL EXPLOIT DEMO

## 📋 Overview

This project demonstrates a **critical over-collateralized arbitrage vulnerability** in the Reserve Protocol's ETH+ token. The exploit allows attackers to mint ETH+ tokens and immediately redeem them for ~5.9% profit due to excess collateral backing.

## 🎯 Impact

- **Financial Loss**: $6.4M+ drainable reserves
- **Protocol Risk**: Affects ETH+ Reserve Token (0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8)
- **Risk Level**: HIGH - Active exploit opportunity

## 🛠️ Setup

### Prerequisites
- Foundry (latest version)
- Mainnet RPC endpoint (Alchemy/Infura recommended)

### Installation
```bash
# Clone and install dependencies
git submodule update --init --recursive
forge install

# Set your RPC URL
cp .env.template .env
# Edit .env with your MAINNET_RPC_URL
```

## 🚀 Quick Start

### 1. Build the project
```bash
forge build
```

### 2. Run tests (recommended)
```bash
# Run passing exploit demonstration
forge test --match-path test/FullExploitPOC.t.sol -v

# Run all tests
forge test -v
```

### 3. Execute live exploit demo
```bash
# Demonstrates exploit on mainnet fork
forge script script/POCDeploy.s.sol

# Shows profit calculations and executes mint/redeem
```

## 📁 Project Structure

```
├── src/
│   └── POC.sol              # Deployable exploit contract
├── script/
│   └── POCDeploy.s.sol      # Live demo script
├── test/
│   ├── FullExploitPOC.t.sol # Core exploit tests
│   └── RealExploitTest.t.sol # Advanced testing
├── reports/
│   └── EXPLOIT_REPORT.md    # Detailed analysis
└── foundry.toml             # Foundry configuration
```

## 🎬 Video Demo Commands

```bash
# 1. Show the exploit report
cat reports/EXPLOIT_REPORT.md

# 2. Run successful tests
forge test --match-path test/FullExploitPOC.t.sol

# 3. Execute live exploit on mainnet
forge script script/POCDeploy.s.sol

# 4. Build and verify
forge build
```

## 🔍 Technical Details

### Root Cause
The ETH+ Reserve Token is 5.9% over-collateralized (baskets_needed > total_supply), allowing profitable mint/redeem cycles.

### Exploit Flow
1. **Mint Phase**: Deposit collateral → Receive ETH+ tokens
2. **Redeem Phase**: Return ETH+ tokens → Receive MORE collateral value
3. **Profit**: Difference between input/output collateral values

### Key Files
- `POC.sol`: Main exploit contract with `executeExploit()` function
- `POCDeploy.s.sol`: Script demonstrating live execution
- `FullExploitPOC.t.sol`: Test suite proving exploit viability

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
