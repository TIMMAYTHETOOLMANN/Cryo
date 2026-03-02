# 🚀 CRYO1 OMNI-CHANNEL SYSTEM - SETUP GUIDE

## Quick Start (5 minutes)

### 1. Install Python Dependencies

```bash
# Navigate to project root
cd C:\Users\timot\IdeaProjects\Cryo1

# Create virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
# source venv/bin/activate

# Install all dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy environment template
cp .env.template .env

# Edit .env with your configuration:
# - Add your Alchemy API key for RPC URLs
# - Add your PRIVATE_KEY (for execution)
# - Set contract addresses (already configured for deployed contracts)
```

**Required Environment Variables:**
```bash
# RPC Configuration (get from https://alchemy.com)
MAINNET_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY

# Execution (leave empty for scan-only mode)
PRIVATE_KEY=your_private_key_here

# Contract Addresses (pre-configured)
LIQUIDATION_EXECUTOR_V1=0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f
LIQUIDATION_EXECUTOR_V2=0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890
TREASURY_ADDRESS=0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4
```

### 3. Verify Installation

```bash
# Test Python imports
python -c "from web3 import Web3; from omni_channel import OmniOrchestrator; print('✅ All imports successful')"

# Check environment
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('PRIVATE_KEY set:', bool(os.getenv('PRIVATE_KEY')))"
```

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    CRYO1 OMNI-CHANNEL SYSTEM                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   MEMPOOL    │  │   CONTRACT   │  │   STATIC     │         │
│  │    RADAR     │  │   CRAWLER    │  │  ANALYZER    │         │
│  │  (Module 9.1)│  │  (Module 9.2)│  │  (Module 9.3)│         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                 │                 │                  │
│         └─────────────────┼─────────────────┘                  │
│                           ▼                                    │
│              ┌────────────────────────┐                        │
│              │   ML AGGREGATOR        │                        │
│              │      (Module 9.5)      │                        │
│              └───────────┬────────────┘                        │
│                          ▼                                     │
│              ┌────────────────────────┐                        │
│              │   EXECUTION ROUTER     │                        │
│              │  → LiquidationExecutor │                        │
│              │  → FlashLoanArbitrage  │                        │
│              └───────────┬────────────┘                        │
│                          ▼                                     │
│              ┌────────────────────────┐                        │
│              │   SMART CONTRACTS      │                        │
│              │  → LiquidationExecutor │                        │
│              │  → FlashLoanArbitrage  │                        │
│              └────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Running the System

### Option 1: Swiss Army Knife (Unified Entry Point)

```bash
# Start the unified system
python swiss_army_knife.py

# Scan-only mode (no execution)
python swiss_army_knife.py --scan-only
```

### Option 2: Individual Modules

```bash
# Start Omni-Channel orchestrator
cd omni_channel
python omni_orchestrator.py

# Run dynamic reconnaissance
python dynamic_recon.py

# Start liquidation detector
cd liquidation_engine
python enhanced_detector.py
```

### Option 3: Smart Contract Deployment

```bash
# Deploy Flash Loan Arbitrage Executor
forge script script/DeployFlashLoanArbitrage.s.sol:DeployFlashLoanArbitrage \
  --rpc-url $MAINNET_RPC_URL \
  --private-key $PRIVATE_KEY \
  --broadcast \
  -vvv

# Deploy Liquidation Executor
forge script script/DeployLiquidationExecutor.s.sol:DeployLiquidationExecutor \
  --rpc-url $MAINNET_RPC_URL \
  --private-key $PRIVATE_KEY \
  --broadcast \
  -vvv
```

---

## Configuration Reference

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `PRIVATE_KEY` | Execution wallet private key | - | For execution |
| `MAINNET_RPC_URL` | Ethereum RPC endpoint | - | ✅ Yes |
| `LIQUIDATION_EXECUTOR_V1` | Executor V1 address | `0x76dF...5B9f` | No |
| `LIQUIDATION_EXECUTOR_V2` | Executor V2 address | `0xFf11...4890` | No |
| `FLASH_EXECUTOR` | Flash loan executor address | - | For arb |
| `TREASURY_ADDRESS` | Profit collection address | `0xB323...1e4` | No |
| `MIN_PROFIT_USD` | Minimum profit threshold | 50 | No |
| `GAS_PRICE_CAP_GWEI` | Max gas price | 50 | No |
| `BLOXROUTE_API_KEY` | bloXroute mempool API | - | Optional |
| `INFURA_API_KEY` | Infura WebSocket API | - | Optional |

### Smart Contract Addresses (Pre-configured)

```bash
# Deployed Contracts
LiquidationExecutor V1: 0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f
LiquidationExecutor V2: 0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890
Treasury:               0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4
Aave V3 Pool:           0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2
```

---

## Testing

### Run Unit Tests

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all tests
pytest omni_channel/tests/ -v

# Run with coverage
pytest omni_channel/tests/ --cov=omni_channel --cov-report=html

# Run specific test
pytest omni_channel/tests/test_integration.py::TestDataModels -v
```

### Test Execution Flow

```bash
# Test contract connection
python -c "
from web3 import Web3
from dotenv import load_dotenv
import os

load_dotenv()
w3 = Web3(Web3.HTTPProvider(os.getenv('MAINNET_RPC_URL')))
print('Connected:', w3.is_connected())
print('Block:', w3.eth.block_number)
"
```

---

## Troubleshooting

### Import Errors

```bash
# If you get "ModuleNotFoundError: No module named 'web3'"
pip install web3 eth-abi aiohttp websockets

# Or reinstall all dependencies
pip install -r requirements.txt --force-reinstall
```

### Environment Not Loading

```bash
# Check if .env exists
ls -la .env

# If not, create from template
cp .env.template .env

# Verify environment variables
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('PRIVATE_KEY', 'NOT SET'))"
```

### RPC Connection Failed

```bash
# Test RPC endpoint
cast block-number --rpc-url $MAINNET_RPC_URL

# If fails, check your Alchemy API key in .env
# Get new key from: https://dashboard.alchemy.com
```

### Contract Execution Fails

```bash
# Check if executor contract is deployed
cast code 0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f --rpc-url $MAINNET_RPC_URL

# Should return bytecode, not 0x

# Check treasury balance
cast balance 0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4 --rpc-url $MAINNET_RPC_URL
```

---

## Project Structure

```
Cryo1/
├── contracts/                    # Solidity smart contracts
│   └── liquidation/
│       ├── LiquidationExecutor.sol
│       ├── LiquidationExecutorV2.sol
│       └── FlashLoanArbitrageExecutor.sol
│
├── omni_channel/                 # Python Omni-Channel system
│   ├── data_lake/               # Data models & queue
│   ├── mempool_radar/           # Mempool monitoring
│   ├── contract_crawler/        # Protocol discovery
│   ├── static_analyzer/         # Code analysis
│   ├── cross_chain_monitor/     # Bridge monitoring
│   ├── ml_aggregator/           # ML scoring
│   └── execution_router/        # Smart contract execution
│
├── liquidation_engine/          # Legacy liquidation modules
│   ├── enhanced_detector.py
│   ├── calculator.py
│   └── monitor.py
│
├── script/                      # Foundry deployment scripts
│   ├── DeployLiquidationExecutor.s.sol
│   └── DeployFlashLoanArbitrage.s.sol
│
├── .env                         # Environment configuration
├── .env.template                # Environment template
├── requirements.txt             # Python dependencies
├── swiss_army_knife.py          # Unified entry point
└── dynamic_recon.py             # Reconnaissance engine
```

---

## Next Steps

1. **Configure Environment** - Add your RPC URLs and private key
2. **Test Connection** - Verify RPC and contract connections
3. **Run Scanner** - Start with `python swiss_army_knife.py --scan-only`
4. **Monitor Opportunities** - Watch for detected opportunities
5. **Execute** - When profitable opportunity found, system will execute

---

## Support & Resources

- **Documentation**: See `OMNI_CHANNEL_ARCHITECTURE.md`
- **Integration Guide**: See `OMNI_CHANNEL_INTEGRATION_GUIDE.md`
- **System Status**: See `OMNI_CHANNEL_COMPLETE_STATUS.md`
- **Deployment Guide**: See `DEPLOYMENT_GUIDE.md`

---

**🚀 SYSTEM READY - HAPPY HUNTING!**

*Treasury: `0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4`*
*Executors: V1 `0x76dF...5B9f` | V2 `0xFf11...4890`*
