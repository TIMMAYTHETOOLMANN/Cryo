# ⚡ Flash Loan Liquidation Engine

## Zero-Capital Profit from Undercollateralized DeFi Positions

This engine transforms the Cryo1 detection system into an autonomous profit machine that liquidates undercollateralized positions across 96+ chains using flash loans.

---

## 🎯 How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                    LIQUIDATION FLOW                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. DETECT          2. CALCULATE       3. FLASH LOAN            │
│  ┌─────────┐       ┌──────────┐       ┌────────────┐           │
│  │ Health  │  →    │ Profit   │  →    │ Borrow     │           │
│  │ Factor  │       │ Check    │       │ Debt       │           │
│  │ < 1.05  │       │ > $10    │       │ Asset      │           │
│  └─────────┘       └──────────┘       └────────────┘           │
│                                              │                  │
│                                              ↓                  │
│  6. PROFIT         5. REPAY          4. LIQUIDATE              │
│  ┌─────────┐       ┌──────────┐       ┌────────────┐           │
│  │ Keep    │  ←    │ Flash    │  ←    │ Seize      │           │
│  │ Excess  │       │ Loan +   │       │ Collateral │           │
│  │ Collat  │       │ Fee      │       │ + Bonus    │           │
│  └─────────┘       └──────────┘       └────────────┘           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📦 Architecture

### Module 1: Opportunity Detector (`detector.py`)
- Scans Aave V2/V3, Compound V2, MakerDAO positions
- Monitors health factor < 1.05 threshold
- Queues liquidatable positions for execution

### Module 2: Profitability Calculator (`calculator.py`)
- Computes net profit after flash loan fees, gas, slippage
- Supports multiple flash loan providers
- Minimum $10 profit threshold

### Module 3: Flash Loan Executor (`LiquidationExecutor.sol`)
- Zero-capital execution via Aave V3 flash loans
- 0.05% flash loan fee
- Automatic profit transfer to treasury

---

## 🚀 Quick Start

### 1. Deploy Executor Contract

```bash
cd C:\Users\timot\IdeaProjects\Cryo1

# Deploy on Ethereum mainnet
forge script script/DeployLiquidationExecutor.s.sol:DeployLiquidationExecutor \
  --rpc-url https://eth-mainnet.g.alchemy.com/v2/Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu \
  --private-key $PRIVATE_KEY \
  --broadcast \
  -vvv
```

### 2. Run Detection Engine

```bash
cd liquidation_engine

# Install dependencies
pip install web3 aiohttp

# Start detector
python detector.py
```

### 3. Monitor & Execute

```bash
# Run profitability calculator
python calculator.py

# Expected output:
# ============================================================
# LIQUIDATION PROFITABILITY EXAMPLE
# ============================================================
# Debt Amount:      $10,000.00
# Collateral Value: $8,500.00
# Liquidation Bonus: 5.0%
#
# --- Profit Breakdown ---
# Gross Profit:     $500.00
# Flash Loan Fee:   $5.00
# Gas Cost:         $18.00
# Net Profit:       $477.00
#
# Profitable:       ✅ YES
# ROI:              9,440.0%
# ============================================================
```

---

## 💰 Profitability Examples

### Example 1: Aave V3 on Ethereum
| Parameter | Value |
|-----------|-------|
| Debt | $10,000 USDC |
| Collateral | $8,500 WETH |
| Health Factor | 0.85 |
| Liquidation Bonus | 5% |
| Flash Loan Fee (0.05%) | $5 |
| Gas Cost (30 gwei) | $18 |
| **Net Profit** | **$477** |

### Example 2: Aave V3 on Arbitrum
| Parameter | Value |
|-----------|-------|
| Debt | $5,000 USDC |
| Collateral | $4,250 WETH |
| Health Factor | 0.85 |
| Liquidation Bonus | 5% |
| Flash Loan Fee (0.05%) | $2.50 |
| Gas Cost (~3 gwei, L2) | $0.50 |
| **Net Profit** | **$247** |

### Example 3: Compound V2 on Ethereum
| Parameter | Value |
|-----------|-------|
| Debt | $20,000 DAI |
| Collateral | $16,000 WBTC |
| Health Factor | 0.80 |
| Liquidation Bonus | 8% |
| Flash Loan Fee (0.05%) | $10 |
| Gas Cost (30 gwei) | $21 |
| **Net Profit** | **$1,569** |

---

## 🛡️ Risk Management

### Pre-Flight Checks
- ✅ Health factor < 1.05
- ✅ Debt > $1,000 (covers gas)
- ✅ Net profit > $10
- ✅ Gas price < max threshold
- ✅ Oracle prices fresh (< 1 hour)

### Execution Safeguards
- ✅ Slippage protection (min collateral amount)
- ✅ Flash loan atomicity (reverts if unprofitable)
- ✅ MEV protection via Flashbots
- ✅ Gas limit protection

---

## 📊 Supported Protocols

| Protocol | Chains | Liquidation Bonus | Flash Loan Source |
|----------|--------|-------------------|-------------------|
| Aave V2 | Ethereum | 5% | Aave V2 Pool |
| Aave V3 | 10+ chains | 5% | Aave V3 Pool |
| Compound V2 | Ethereum | 8% | Uniswap V3 |
| Compound V3 | Ethereum, Arb | 5% | Aave V3 |
| MakerDAO | Ethereum | 13% | Maker Flash Mint |

---

## 🔧 Configuration

### Environment Variables

```bash
# .env
PRIVATE_KEY=0x...
ALCHEMY_API_KEY=Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu

# Detection thresholds
MIN_DEBT_USD=1000
MIN_PROFIT_USD=10
MAX_HEALTH_FACTOR=1.05

# Gas settings
MAX_GAS_PRICE_GWEI=50
GAS_LIMIT_BUFFER=1.2
```

### Contract Parameters

```solidity
// LiquidationExecutor.sol
uint256 public minProfit = 10e18;  // $10 minimum
address public TREASURY;           // Profit destination
mapping(address => bool) public supportedDebtAssets;
```

---

## 📈 Scaling Strategy

### Phase 1: Single Chain (Ethereum)
- Deploy executor on mainnet
- Monitor top 100 Aave users
- Target: 1-5 liquidations/day

### Phase 2: Multi-Chain (L2s)
- Deploy on Arbitrum, Optimism, Base
- Lower gas = smaller positions profitable
- Target: 10-20 liquidations/day

### Phase 3: Full Coverage (96+ Chains)
- Cross-chain orchestration via LayerZero
- Centralized detection, distributed execution
- Target: 50-100 liquidations/day

---

## 🎯 Profit Projections

| Scenario | Liquidations/Day | Avg Profit | Daily Revenue | Monthly Revenue |
|----------|-----------------|------------|---------------|-----------------|
| Conservative | 5 | $100 | $500 | $15,000 |
| Moderate | 20 | $150 | $3,000 | $90,000 |
| Aggressive | 50 | $200 | $10,000 | $300,000 |

**ROI: Infinite** (zero capital required, only gas which is covered by profit)

---

## 🚨 Important Warnings

1. **Smart Contract Risk**: Executor contract could have bugs
2. **Liquidation Risk**: Positions may be liquidated by others first
3. **Gas Risk**: Price spikes can make liquidations unprofitable
4. **Oracle Risk**: Stale prices can lead to incorrect health factors
5. **MEV Risk**: Bots may front-run liquidation transactions

**Always test on testnet before deploying to mainnet!**

---

## 📁 File Structure

```
Cryo1/
├── contracts/
│   └── liquidation/
│       ├── LiquidationExecutor.sol    # Flash loan executor
│       └── interfaces.sol             # Protocol interfaces
├── liquidation_engine/
│   ├── detector.py                   # Opportunity scanner
│   ├── calculator.py                 # Profitability calculator
│   └── executor.py                   # Transaction submitter
├── script/
│   └── DeployLiquidationExecutor.s.sol
└── LIQUIDATION_ENGINE.md             # This file
```

---

## 🔗 Resources

- [Aave V3 Flash Loans Docs](https://docs.aave.com/developers/guides/flash-loans)
- [Compound Liquidation Guide](https://docs.compound.finance/#liquidations)
- [Flashbots Protect](https://docs.flashbots.net/flashbots-protect)
- [LayerZero Cross-Chain](https://layerzero.gitbook.io/docs/)

---

**Built with ❤️ by the Cryo1 Team**

*Zero capital. Infinite opportunity.*
