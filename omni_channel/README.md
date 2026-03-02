# 🎯 Omni-Channel Opportunity Triangulation Engine

## Module 9: Universal MEV & Opportunity Discovery System

---

## 📖 Overview

The Omni-Channel Engine transforms your liquidation system from a **reactive scanner** into a **predictive, multi-vector opportunity discovery platform**. It ingests data from every possible source—public, private, and unlisted—then triangulates opportunities using five interlocking detector arrays.

### Key Features

- **Multi-Provider Mempool Radar**: 50-200ms latency via bloXroute, Infura, Blocknative
- **Advanced Signal Filtering**: Oracle updates, large swaps, flash loans, liquidations
- **Intelligent Deduplication**: Merge transactions from multiple providers with confidence scoring
- **Priority Queue Management**: Route high-value opportunities first
- **Real-Time Analytics**: Comprehensive statistics and monitoring
- **Kafka Integration**: Production-ready data lake streaming (optional)

---

## 🚀 Quick Start

### Installation

```bash
# Install dependencies
pip install -r omni_channel/requirements.txt

# Or install core dependencies only
pip install web3 eth-abi aiohttp websockets
```

### Basic Usage

```python
import asyncio
from omni_channel import OmniOrchestrator

async def main():
    # Configuration
    config = {
        # Optional: API keys for mempool providers
        'bloxroute_api_key': 'YOUR_BLOXROUTE_KEY',
        'infura_api_key': 'YOUR_INFURA_KEY',
        'blocknative_api_key': 'YOUR_BLOCKNATIVE_KEY',
        
        # Enable/disable modules
        'enable_mempool_radar': True,
        'enable_kafka': False,
    }
    
    # Create and start orchestrator
    orchestrator = OmniOrchestrator(config)
    await orchestrator.start()

asyncio.run(main())
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  OMNI-CHANNEL ENGINE                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   MEMPOOL    │  │   CONTRACT   │  │   STATIC     │      │
│  │    RADAR     │  │   CRAWLER    │  │  ANALYZER    │      │
│  │  (Module 9.1)│  │  (Module 9.2)│  │  (Module 9.3)│      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                 │                 │               │
│         └─────────────────┼─────────────────┘               │
│                           ▼                                 │
│              ┌────────────────────────┐                     │
│              │   CROSS-CHAIN MONITOR  │                     │
│              │      (Module 9.4)      │                     │
│              └───────────┬────────────┘                     │
│                          ▼                                  │
│              ┌────────────────────────┐                     │
│              │    ML AGGREGATOR       │                     │
│              │      (Module 9.5)      │                     │
│              └───────────┬────────────┘                     │
│                          ▼                                  │
│              ┌────────────────────────┐                     │
│              │   EXECUTION ROUTER     │                     │
│              └────────────────────────┘                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 📦 Module Structure

```
omni_channel/
├── data_lake/
│   ├── data_models.py       # Core data structures
│   ├── signal_queue.py      # Priority queue & routing
│   └── kafka_client.py      # Apache Kafka integration
│
├── mempool_radar/
│   ├── base_provider.py     # Provider interface
│   ├── bloxroute_provider.py # bloXroute implementation
│   ├── infura_provider.py   # Infura implementation
│   ├── blocknative_provider.py # Blocknative implementation
│   ├── signal_merger.py     # Multi-provider merge
│   └── advanced_filter.py   # Trigger detection
│
├── contract_crawler/        # (Coming soon)
├── static_analyzer/         # (Coming soon)
├── cross_chain_monitor/     # (Coming soon)
├── ml_aggregator/           # (Coming soon)
│
├── omni_orchestrator.py     # Main entry point
├── example_usage.py         # Usage examples
└── requirements.txt         # Dependencies
```

---

## 🔧 Configuration

### Environment Variables

```bash
# Mempool Providers
export BLOXROUTE_API_KEY="your_key"
export INFURA_API_KEY="your_key"
export BLOCKNATIVE_API_KEY="your_key"

# Kafka (optional)
export KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
export KAFKA_CONSUMER_GROUP="omni-channel"
```

### Configuration Options

```python
config = {
    # API Keys
    'bloxroute_api_key': '...',
    'infura_api_key': '...',
    'blocknative_api_key': '...',
    
    # Module Enable/Disable
    'enable_mempool_radar': True,
    'enable_contract_crawler': True,
    'enable_static_analyzer': True,
    'enable_cross_chain_monitor': True,
    'enable_kafka': False,
    
    # Performance Tuning
    'signal_queue_max_size': 10000,
    'processing_interval_ms': 100,
    
    # Filter Thresholds
    'large_swap_threshold_usd': 100000,
    'flash_loan_threshold_usd': 500000,
}
```

---

## 📊 Data Models

### OpportunitySignal

Primary signal structure passed through the system:

```python
from omni_channel import OpportunitySignal, SignalType, SignalSource

signal = OpportunitySignal(
    signal_type=SignalType.LIQUIDATION,
    source_module=SignalSource.MEMPOOL_RADAR,
    chain_id=1,
    target_contract="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    expected_value_usd=500.0,
    confidence=0.85,
    urgency_score=80,
    execution_complexity=3,
    gas_estimate=300000,
    expiry_block=20000000 + 2,
)
```

### Signal Types

| Type | Description | Priority |
|------|-------------|----------|
| `LIQUIDATION` | Undercollateralized position | High |
| `ARBITRAGE` | Cross-DEX price difference | Medium |
| `BACKRUN` | Opportunity after large trade | High |
| `SANDWICH` | MEV sandwich opportunity | Medium |
| `ORACLE_UPDATE` | Price feed update | Critical |
| `LARGE_SWAP` | Significant DEX swap | High |

---

## 🎯 Advanced Usage

### Custom Signal Handlers

```python
from omni_channel import OmniOrchestrator, OpportunitySignal

orchestrator = OmniOrchestrator(config)

# Register custom handler
async def my_handler(signal: OpportunitySignal):
    if signal.signal_type == SignalType.LIQUIDATION:
        # Send to liquidation engine
        await liquidation_engine.execute(signal)
    elif signal.signal_type == SignalType.ARBITRAGE:
        # Send to arbitrage bot
        await arbitrage_bot.execute(signal)

orchestrator.signal_router.add_routing_rule(my_handler)
```

### Standalone Mempool Radar

```python
from omni_channel import MempoolRadar, ProviderConfig, AdvancedFilter

# Configure providers
providers = {
    'bloxroute': ProviderConfig(
        api_key='key',
        ws_endpoint='wss://api.bloxroute.com/v2/ws'
    )
}

radar = MempoolRadar(providers)
filter = AdvancedFilter()

# Register callbacks
radar.on_merged_transaction(filter.analyze_merged_transaction)
filter.on_signal(lambda s: print(f"Signal: {s}"))

# Start
await radar.start()
```

### Statistics & Monitoring

```python
# Get system stats
stats = orchestrator.get_stats()

print(f"Uptime: {stats['uptime_hours']:.2f} hours")
print(f"Signals Processed: {stats['signals_processed']}")
print(f"Queue Depth: {stats['queue_depth']}")
print(f"Errors: {stats['errors']}")

# Get mempool radar stats
radar_stats = stats['mempool_radar']
print(f"Providers: {radar_stats['providers']}")
print(f"Merger Stats: {radar_stats['merger_stats']}")
```

---

## 🔌 Integration with Existing Modules

### Enhanced Detector Integration

```python
# Your existing enhanced_detector.py can consume from Omni-Channel

from omni_channel import OpportunitySignal

async def on_liquidation_signal(signal: OpportunitySignal):
    if signal.signal_type == SignalType.LIQUIDATION:
        # Use your existing liquidation logic
        await execute_liquidation(
            user=signal.user_address,
            debt_asset=signal.debt_asset,
            collateral_asset=signal.collateral_asset,
        )

orchestrator.signal_router.add_routing_rule(
    lambda s: ExecutionModule.LIQUIDATION_ENGINE 
    if s.signal_type == SignalType.LIQUIDATION
    else ExecutionModule.MANUAL_REVIEW
)
```

### Profit Calculator Integration

```python
# Use your existing calculator with Omni-Channel signals

from liquidation_engine.calculator import ProfitabilityCalculator

calculator = ProfitabilityCalculator()

async def process_signal(signal: OpportunitySignal):
    # Calculate profitability
    result = calculator.calculate(
        debt_amount_usd=signal.debt_amount / 1e6,
        collateral_amount_usd=signal.collateral_amount / 1e18 * 2000,
        liquidation_bonus=0.05,
        flash_loan_provider='aave_v3',
        gas_price_gwei=signal.gas_price_gwei,
        eth_price_usd=2000,
    )
    
    if result.is_profitable:
        await execute(signal)
```

---

## 📈 Performance Metrics

### Latency Targets

| Component | Target | Typical |
|-----------|--------|---------|
| Mempool Radar | <200ms | 50-150ms |
| Signal Processing | <100ms | 20-50ms |
| Execution Routing | <50ms | 5-20ms |

### Throughput Targets

| Metric | Target | Typical |
|--------|--------|---------|
| Signals/Second | 10,000+ | 50,000+ |
| Queue Depth | <1,000 | 100-500 |
| Error Rate | <0.1% | 0.01% |

---

## 🧪 Testing

### Run Unit Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-cov

# Run tests
pytest omni_channel/tests/ -v --cov=omni_channel
```

### Integration Testing

```python
# Test with mock data
from omni_channel import OpportunitySignal, SignalType

async def test_signal_processing():
    signal = OpportunitySignal(
        signal_type=SignalType.LIQUIDATION,
        expected_value_usd=1000.0,
        confidence=0.9,
    )
    
    assert signal.quality_score() > 0.5
    assert not signal.is_expired(signal.expiry_block)
```

---

## 🔐 Security Considerations

### API Key Management

```bash
# Use environment variables, never hardcode
export BLOXROUTE_API_KEY="..."
export INFURA_API_KEY="..."

# Or use a secrets manager
from dotenv import load_dotenv
load_dotenv()
```

### Rate Limiting

```python
# Implement rate limiting for external APIs
from asyncio import Semaphore

rate_limiter = Semaphore(10)  # 10 concurrent requests

async def fetch_data():
    async with rate_limiter:
        # Make API call
        pass
```

---

## 🚧 Roadmap

### Phase 1: Core Infrastructure ✅
- [x] Data models and signal structures
- [x] Mempool Radar with 3 providers
- [x] Signal merger and deduplication
- [x] Advanced filter for triggers
- [x] Kafka integration

### Phase 2: Discovery Modules (Coming Soon)
- [ ] Contract Discovery Crawler
- [ ] Static Analysis Engine
- [ ] Cross-Chain Bridge Monitor
- [ ] ML Aggregator & Ranker

### Phase 3: Execution Integration
- [ ] Liquidation Engine handoff
- [ ] Arbitrage module
- [ ] Backrun bot integration
- [ ] Sandwich bot integration

---

## 📚 Resources

### API Documentation
- [bloXroute API](https://bloxroute.com/)
- [Infura API](https://infura.io/docs)
- [Blocknative API](https://docs.blocknative.com/)

### Related Documentation
- [SYSTEM_ARCHITECTURE.md](../SYSTEM_ARCHITECTURE.md)
- [OMNI_CHANNEL_ARCHITECTURE.md](../OMNI_CHANNEL_ARCHITECTURE.md)
- [LIQUIDATION_ENGINE.md](../LIQUIDATION_ENGINE.md)

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `pytest omni_channel/tests/`
5. Submit a pull request

---

## 📄 License

MIT License - See LICENSE file for details

---

**🎯 Omni-Channel Opportunity Triangulation Engine**

*From reactive scanning → predictive discovery*

*Built by the Cryo1 Team*
