# OCDS Production Deployment Guide

## Current Opportunity Status (Block 24,548,923)

### ETH+ - EXECUTE RECOMMENDED
| Metric | Value |
|--------|-------|
| **Status** | ⚠️ OVER-COLLATERALIZED |
| Collateral Ratio | 107% |
| Excess Baskets | 2,271 |
| **Gross Profit** | **$456,083** |
| **Net Profit** | **$456,083** |
| ROI | 100% |
| Risk Level | MEDIUM |

### eUSD - HOLD
| Metric | Value |
|--------|-------|
| Status | ✅ ADEQUATELY COLLATERALIZED |
| Collateral Ratio | 100% |
| Opportunity | None |

---

## Deployment Steps

### Step 1: Configure Private Key

Edit `.env` and add your private key:

```bash
PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
```

**Security Notes:**
- Never commit `.env` to git
- Use a dedicated wallet for arbitrage
- Keep only necessary funds in the wallet

### Step 2: Deploy Detection Contracts

```bash
cd C:\Users\timot\IdeaProjects\Cryo1

# Deploy to Ethereum mainnet
forge script script/DeployOCDS.s.sol:DeployOCDS \
  --rpc-url https://ethereum.publicnode.com \
  --broadcast \
  -vvv
```

### Step 3: Execute Arbitrage

Once contracts are deployed, execute the arbitrage:

```bash
# Full execution with broadcast
forge script script/MainnetScanner.s.sol:MainnetScanner \
  --rpc-url https://ethereum.publicnode.com \
  --private-key $PRIVATE_KEY \
  --broadcast \
  -vvv
```

### Step 4: Monitor Execution

Watch for transaction confirmation and profit realization.

---

## Alternative: Quick Scanner (No Deployment Required)

For scanning only (no execution):

```bash
forge script script/QuickScanner.s.sol:QuickScanner \
  --rpc-url https://ethereum.publicnode.com \
  -vvv
```

---

## Deep Dive Analysis

For comprehensive intelligence:

```bash
forge script script/DeepDiveScanner.s.sol:DeepDiveScanner \
  --rpc-url https://ethereum.publicnode.com \
  -vvv
```

---

## Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PRIVATE_KEY` | Wallet private key for execution | (required for execution) |
| `MIN_PROFIT_WEI` | Minimum profit threshold in wei | 10000000000000000 (0.01 ETH) |
| `GAS_PRICE_CAP_GWEI` | Maximum gas price in gwei | 50 |
| `MAINNET_RPC_URL` | Ethereum RPC endpoint | https://ethereum.publicnode.com |

### Risk Management

| Position Size | % of Excess | Baskets (ETH+) | Est. Profit |
|--------------|-------------|----------------|-------------|
| Conservative | 10% | 227 | ~$45,600 |
| Moderate | 25% | 568 | ~$114,000 |
| Aggressive | 50% | 1,136 | ~$228,000 |

---

## Execution Flow

1. **Acquire Collateral** - Buy basket tokens via Uniswap V3
2. **Mint RToken** - Create ETH+ tokens with collateral
3. **Redeem Immediately** - Burn RToken for underlying + excess
4. **Capture Profit** - Keep excess collateral as profit
5. **Swap Back** - Convert surplus tokens to ETH/stablecoins

---

## Risk Warnings

⚠️ **Smart Contract Risk** - Reserve Protocol contracts could have bugs
⚠️ **MEV Competition** - Other bots may compete for same opportunity
⚠️ **Gas Volatility** - High gas prices can reduce profitability
⚠️ **Liquidation Delay** - Basket redemption may have delays
⚠️ **Slippage** - Large positions may experience price impact

---

## Support

For issues or questions, review the contract source code in `contracts/` directory.
