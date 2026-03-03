# CRYO — Zero-Capital Flash Loan Profit Engine

## Objective

**$0 initial liquidity → rapid, exponential profit generation.**

Cryo is a unified system for detecting and executing profitable DeFi opportunities across 8 EVM networks using flash loans (zero capital required). Every module in this system exists to serve one purpose: finding and extracting profit with no upfront investment.

## Quick Start

```bash
# 1. Configure environment
cp .env.template .env
# Edit .env — set MAINNET_RPC_URL, PRIVATE_KEY, TREASURY_ADDRESS

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Launch (assesses your situation and recommends the best phase)
python main.py
```

### Entry Point

**`main.py`** is the single unified entry point. It assesses your current capital situation and launches the appropriate phase:

```bash
python main.py                   # Auto-detect best phase and launch
python main.py --phase 1         # Force Phase 1 (zero-capital flash loans)
python main.py --preflight       # Run system checks only
python main.py --scan-only       # Detection only, no execution
python main.py --status          # Print current system status
python main.py --pipeline        # Run full Module 1 stage-gated pipeline
```

## Architecture

### Phase-Gated Execution

The system deploys in phases, gated by cumulative profit:

| Phase | Trigger | Strategy | Target |
|-------|---------|----------|--------|
| **1 — Cold Start** | $0 capital | Flash-loan liquidations & arbitrage | $2,400-$4,800/hr |
| **2 — Heat Map** | $2,500 cumulative | Pattern learning + frequency optimization | $15K-$36K/mo |
| **3 — Multiplier** | $45,000 cumulative | 60% reinvestment + compounding | Exponential |

### Module Map

```
main.py                              ← UNIFIED ENTRY POINT
│
├── profit_engine/                   ← CORE PROFIT EXTRACTION
│   ├── triangulated_profit_engine.py   Phase 1-3 master orchestrator
│   ├── opportunity_scanner.py          6-vector opportunity detection
│   ├── jit_liquidation_engine.py       Just-in-time flash loan execution
│   ├── flash_loan_router.py            Multi-provider flash loan aggregation
│   ├── zero_revert_pipeline.py         Simulate-before-send safety
│   ├── gas_optimizer.py                Cross-chain gas tracking
│   ├── profit_ledger.py                P&L tracking + phase transitions
│   ├── heat_map.py                     Phase 2: pattern learning
│   ├── capital_multiplier.py           Phase 3: exponential compounding
│   └── rpc_gateway.py                  Multi-chain RPC abstraction
│
├── MODULE_1_LIQUIDATION_ENGINE/     ← STAGE-GATED PIPELINE
│   ├── stage_0_preflight/              System validation
│   ├── stage_1_detection/              Opportunity detection (mempool, DEX, NFT)
│   ├── stage_2_analysis/               Profitability + risk assessment
│   ├── stage_3_execution/              Flash loans + liquidation execution
│   ├── stage_4_mev_protection/         Flashbots bundle submission
│   ├── stage_5_cross_chain/            Multi-chain orchestration
│   ├── stage_6_profit_collection/      Treasury management
│   ├── stage_7_analytics/              RL-tuned parameter optimization
│   └── pipeline.py                     Stage orchestrator
│
├── MODULE_9_OMNI_SCOPE/             ← SIGNAL TRIANGULATION
│   ├── array_1_mempool_radar/          3-provider mempool fusion
│   ├── array_2_contract_crawler/       Protocol intelligence
│   ├── array_3_static_analysis/        Bytecode vulnerability scanning
│   ├── array_4_cross_chain_monitor/    Bridge & cross-chain tracking
│   ├── array_5_ml_ranker/              ML scoring & routing
│   └── engine.py                       Signal detection hub
│
├── omni_channel/                    ← ADVANCED MODULES
│   ├── mempool_radar/                  7 mempool provider implementations
│   ├── contract_crawler/               6 crawler modules
│   ├── static_analyzer/                5 analysis engines
│   ├── cross_chain_monitor/            5 cross-chain modules
│   ├── ml_aggregator/                  5 ML scoring modules
│   ├── execution_router/               6 executor types
│   ├── data_lake/                      Signal queue + data models
│   └── omni_orchestrator.py            Signal routing hub
│
├── contracts/                       ← SOLIDITY SMART CONTRACTS
│   ├── CollateralizationDetector.sol   Multi-protocol detection
│   ├── CrossChainDetector.sol          Multi-network scanning
│   ├── OracleIntegration.sol           Chainlink oracle integration
│   ├── LPPricing.sol                   Manipulation-resistant LP pricing
│   ├── liquidation/                    On-chain liquidation executors
│   └── interfaces/                     Protocol interfaces (12+ protocols)
│
├── script/                          ← FOUNDRY SCRIPTS
│   └── MainnetScanner.s.sol            RToken arbitrage + execution
│
├── test/                            ← FOUNDRY TESTS
│   ├── CollateralizationDetectorTest.t.sol
│   └── [6 more test suites]
│
└── docs/                            ← REFERENCE DOCUMENTATION
    ├── SYSTEM_ARCHITECTURE.md
    ├── DEPLOYMENT_GUIDE.md
    ├── SETUP_GUIDE.md
    └── [6 more reference docs]
```

### Signal Flow

```
Detection (6 vectors)
  ├─ Mempool Radar       → pending liquidation/arb transactions
  ├─ Collateral Health   → Aave/Compound health factor monitoring
  ├─ DEX Arbitrage       → cross-DEX price discrepancies
  ├─ Reserve Protocol    → over-collateralized RToken positions
  ├─ Cross-Chain Monitor → bridge arbitrage opportunities
  └─ NFT Liquidations    → NFT-backed lending liquidations
       │
       ▼
Opportunity Scanner → Gas Optimizer → Flash Loan Router
       │
       ▼
Execution Router (simulate → execute → confirm)
       │
       ▼
Profit Ledger → Heat Map → Capital Multiplier → (loop)
```

### Supported Protocols

| Protocol | Detection | Execution | Flash Loan |
|----------|-----------|-----------|------------|
| Aave v2/v3 | ✅ | ✅ | ✅ (provider) |
| Compound v2/v3 | ✅ | ✅ | — |
| MakerDAO | ✅ | ✅ | ✅ (provider) |
| Reserve Protocol | ✅ | ✅ | ✅ (mint/redeem arb) |
| Uniswap v2/v3 | ✅ | ✅ (arb) | ✅ (provider) |
| Balancer v2 | ✅ | ✅ (arb) | ✅ (provider) |
| Curve | ✅ | ✅ (arb) | — |
| ERC-4626 Vaults | ✅ | — | — |

### Supported Networks

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

## Configuration

| Variable | Description | Default |
|---|---|---|
| `MAINNET_RPC_URL` | Ethereum RPC endpoint | — |
| `PRIVATE_KEY` | Wallet private key (empty = scan-only) | — |
| `TREASURY_ADDRESS` | Profit collection address | — |
| `EXECUTION_ENABLED` | Enable live execution | `false` |
| `MIN_PROFIT_USD` | Minimum profit threshold | `50` |
| `GAS_PRICE_CAP_GWEI` | Max gas price | `50` |
| `HEALTH_FACTOR_THRESHOLD` | Min HF to target | `1.05` |
| `SCAN_INTERVAL_SECONDS` | Scan frequency | `10` |

## Development

### Build Contracts
```bash
forge build
```

### Run Solidity Tests
```bash
forge test -vvv --match-contract "CollateralizationDetectorTest|CrossChainDetectorTest|LiquidationExecutorTest|LiquidationExecutorV3Test|CollateralHealthMonitorTest|RiskMitigationExecutorTest|IncentiveFeasibilityCalculatorTest"
```

### Run Python Tests
```bash
pip install pytest pytest-asyncio web3 python-dotenv numpy scikit-learn websockets aiohttp
python -m pytest omni_channel/tests/test_integration.py -v --tb=short --asyncio-mode=auto
```

### Docker
```bash
docker compose up --build -d
docker compose logs -f ocds-scanner
```

## Security

- **Read-Only Default**: No execution without `PRIVATE_KEY` + `EXECUTION_ENABLED=true`
- **Simulate-Before-Send**: Zero-revert pipeline validates every trade before submission
- **Oracle Safety**: Chainlink staleness validation, zero-price rejection, L2 sequencer checks
- **MEV Protection**: Flashbots bundle submission to prevent frontrunning
- **Gas Cap**: Configurable max gas price prevents unprofitable execution
- **LP Pricing**: Alpha Homora formula resistant to flash loan manipulation
