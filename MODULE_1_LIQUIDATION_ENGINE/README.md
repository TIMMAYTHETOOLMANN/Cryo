# MODULE 1: Flash Loan Liquidation Engine

## Zero-Capital Profit Generation from Over-Collateralized DeFi Positions

### Architecture

The engine is organized as a **stage-gated pipeline**. Each stage only activates when all prerequisites from prior stages are met. This prevents any action the system cannot complete (e.g., submitting a TX without gas).

```
$0 Initial Capital → Exponential Profit

Stage 0  PRE-FLIGHT         Zero capital   Validate system, RPCs, contracts, gas
         ↓ PASS?
Stage 1  DETECTION           Zero capital   Scan chains for liquidatable positions
         ↓ FOUND?
Stage 2  ANALYSIS            Zero capital   Calculate profit, assess risk
         ↓ PROFITABLE?
─────── GAS GATE ──────────  Requires gas   GasManager blocks execution without gas
         ↓ HAS GAS?
Stage 3  EXECUTION           Gas only       Flash loan → liquidation → repay → profit
         ↓
Stage 4  MEV PROTECTION      Gas only       Flashbots / private mempool routing
         ↓
Stage 5  CROSS-CHAIN         Gas/chain      Expand to additional chains
         ↓
Stage 6  PROFIT COLLECTION   Post-profit    Monitor & aggregate profits
```

### Folder Structure

```
MODULE_1_LIQUIDATION_ENGINE/
├── main.py                          ← Single entry point
├── pipeline.py                      ← Master pipeline (enforces stage order)
├── __init__.py
├── requirements.txt
│
├── config/
│   ├── settings.py                  ← Unified config (reads root .env)
│   └── .env.example                 ← Template
│
├── stage_0_preflight/
│   └── system_validator.py          ← Pre-flight checks (zero capital)
│
├── stage_1_detection/
│   └── opportunity_detector.py      ← Multi-chain scanner (zero capital)
│
├── stage_2_analysis/
│   ├── profitability_calculator.py  ← Net profit computation (zero capital)
│   └── risk_manager.py             ← Risk assessment + circuit breaker
│
├── stage_3_execution/
│   ├── flash_loan_aggregator.py     ← 6 flash loan providers
│   ├── liquidation_executor.py      ← On-chain TX interface
│   └── gas_manager.py              ← ⛽ Gas gate (blocks execution without gas)
│
├── stage_4_mev_protection/
│   └── flashbots.py                 ← Flashbots + bloXroute + oracle backruns
│
├── stage_5_cross_chain/
│   └── orchestrator.py              ← Multi-chain coordination
│
├── stage_6_profit_collection/
│   └── treasury_manager.py          ← Treasury monitoring + profit aggregation
│
├── monitoring/
│   └── profit_monitor.py            ← Event listener for LiquidationExecuted
│
├── database/
│   └── timescale_client.py          ← TimescaleDB persistence
│
├── dashboard/
│   └── dashboard.html               ← Web UI
│
├── src/                             ← Production implementations (internal)
│   ├── executors/
│   ├── flash_loan_providers/
│   ├── cross_chain/
│   ├── mev_protection/
│   ├── risk_management/
│   ├── calculators/
│   └── detectors/
│
├── contracts/                       ← Solidity source (reference)
│
└── docker/
    ├── Dockerfile
    └── docker-compose.yml
```

### Usage

```bash
# Full pipeline (scan → analyse → execute if gas available)
python -m MODULE_1_LIQUIDATION_ENGINE.main

# Pre-flight check only
python -m MODULE_1_LIQUIDATION_ENGINE.main --preflight-only

# Gas report only
python -m MODULE_1_LIQUIDATION_ENGINE.main --gas-report

# Treasury snapshot
python -m MODULE_1_LIQUIDATION_ENGINE.main --treasury
```

### Key Design Principle: Zero-Capital Safety

The system has a hard **Gas Gate** between Stage 2 (analysis) and Stage 3 (execution):

- Stages 0-2 are **always safe** — read-only, zero cost
- Stage 3+ requires **gas only** — the liquidation capital comes from flash loans
- The `GasManager` checks wallet balance on the target chain before EVERY TX
- If gas is insufficient, the system continues scanning but does NOT attempt execution
- Once gas is deposited, the system automatically detects it and unlocks execution

### Deployed Contracts

| Contract | Address | Chain |
|----------|---------|-------|
| Executor V1 | `0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f` | Ethereum |
| Executor V2 | `0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890` | Ethereum |
| Flash Executor | `0x8C7377e24030d8453408d8359960376E7e0f8424` | Ethereum |
| Treasury | `0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4` | Ethereum |
