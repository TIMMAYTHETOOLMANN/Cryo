# 🔌 Omni-Channel Integration Guide

## Integrating Module 9 with Existing Cryo1 Liquidation Engine

---

## 📋 Overview

This guide shows how to integrate the Omni-Channel Opportunity Triangulation Engine with your existing liquidation infrastructure to enhance opportunity discovery and execution.

### Current Architecture

```
┌────────────────────────────────────────────────────────────┐
│              EXISTING CRYO1 SYSTEM                          │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  enhanced_detector.py  →  detector.py  →  calculator.py    │
│         ↓                       ↓                ↓          │
│  mempool_sniffer.py   →  executor.py   →  treasury.sol    │
│                                                             │
└────────────────────────────────────────────────────────────┘
```

### Enhanced Architecture (with Omni-Channel)

```
┌────────────────────────────────────────────────────────────┐
│              OMNI-CHANNEL ENGINE                            │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  Mempool Radar → Advanced Filter → Signal Queue           │
│                                    ↓                        │
│                            ML Aggregator & Ranker           │
│                                    ↓                        │
└────────────────────────────────────┼────────────────────────┘
                                     │
                                     ↓
┌────────────────────────────────────────────────────────────┐
│              EXISTING CRYO1 SYSTEM                          │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  enhanced_detector.py  →  calculator.py  →  executor.py    │
│         ↓                       ↓                ↓          │
│  mempool_sniffer.py   →  LiquidationExecutor  →  Treasury  │
│                                                             │
└────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Integration

### Option 1: Replace Enhanced Detector (Recommended)

The Omni-Channel engine can completely replace your `enhanced_detector.py` with superior multi-provider detection.

```python
# liquidation_engine/enhanced_detector_v2.py

from omni_channel import OmniOrchestrator, OpportunitySignal, SignalType
from .calculator import ProfitabilityCalculator
from .executor import LiquidationExecutor

class EnhancedDetectorV2:
    """
    Enhanced detector powered by Omni-Channel
    Replaces enhanced_detector.py with multi-provider detection
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.calculator = ProfitabilityCalculator()
        self.executor = LiquidationExecutor()
        
        # Initialize Omni-Channel
        self.omni_config = {
            'bloxroute_api_key': config.get('bloxroute_api_key'),
            'infura_api_key': config.get('infura_api_key'),
            'blocknative_api_key': config.get('blocknative_api_key'),
            'enable_mempool_radar': True,
        }
        
        self.orchestrator = OmniOrchestrator(self.omni_config)
        
        # Register signal handler
        self.orchestrator.signal_router.add_routing_rule(
            self._route_signal
        )
    
    def _route_signal(self, signal: OpportunitySignal):
        """Route signals to appropriate handler"""
        if signal.signal_type == SignalType.LIQUIDATION:
            return ExecutionModule.LIQUIDATION_ENGINE
        elif signal.signal_type == SignalType.ARBITRAGE:
            return ExecutionModule.ARBITRAGE_MODULE
        else:
            return ExecutionModule.MANUAL_REVIEW
    
    async def start(self):
        """Start detection engine"""
        # Register liquidation handler
        async def on_liquidation(signal: OpportunitySignal):
            await self._process_liquidation(signal)
        
        self.orchestrator.signal_router.register_module(
            ExecutionModule.LIQUIDATION_ENGINE
        )
        
        # Start Omni-Channel
        await self.orchestrator.start()
    
    async def _process_liquidation(self, signal: OpportunitySignal):
        """Process liquidation signal"""
        # Calculate profitability using existing calculator
        profit_result = self.calculator.calculate(
            debt_amount_usd=signal.debt_amount / 1e6,
            collateral_amount_usd=signal.collateral_amount / 1e18 * 2000,
            liquidation_bonus=0.05,
            flash_loan_provider='aave_v3',
            gas_price_gwei=signal.gas_price_gwei,
            eth_price_usd=2000,
        )
        
        if profit_result.is_profitable:
            # Execute using existing executor
            await self.executor.execute_liquidation(
                user=signal.user_address,
                debt_asset=signal.debt_asset,
                collateral_asset=signal.collateral_asset,
            )
    
    async def stop(self):
        """Stop detection engine"""
        await self.orchestrator.stop()
```

---

### Option 2: Augment Existing Detector

Keep your existing `enhanced_detector.py` and supplement it with Omni-Channel signals.

```python
# liquidation_engine/detector_integration.py

from omni_channel import OpportunitySignal, SignalType
from .enhanced_detector import EnhancedDetector
from .mempool_sniffer import MempoolSniffer

class IntegratedDetector:
    """
    Combine existing detectors with Omni-Channel
    """
    
    def __init__(self, config: dict):
        # Keep existing detectors
        self.enhanced_detector = EnhancedDetector(config)
        self.mempool_sniffer = MempoolSniffer(config)
        
        # Add Omni-Channel for richer signals
        self.omni_signals = asyncio.Queue()
    
    async def start(self):
        """Start all detectors in parallel"""
        # Start existing detectors
        await self.enhanced_detector.start()
        await self.mempool_sniffer.start()
        
        # Merge signals from all sources
        asyncio.create_task(self._merge_signals())
    
    async def _merge_signals(self):
        """Merge signals from all sources"""
        while True:
            # Get signals from all sources
            # Deduplicate
            # Prioritize
            # Process
            pass
```

---

## 🔧 Integration Points

### 1. Signal Conversion

Convert Omni-Channel signals to your existing format:

```python
def convert_signal(omni_signal: OpportunitySignal) -> dict:
    """Convert Omni-Channel signal to legacy format"""
    return {
        'chain_id': omni_signal.chain_id,
        'protocol': omni_signal.target_contract,
        'user': omni_signal.user_address,
        'debt_asset': omni_signal.debt_asset,
        'collateral_asset': omni_signal.collateral_asset,
        'debt_amount': omni_signal.debt_amount,
        'collateral_amount': omni_signal.collateral_amount,
        'health_factor': omni_signal.health_factor,
        'confidence': omni_signal.confidence,
        'expected_profit_usd': omni_signal.expected_value_usd,
    }
```

### 2. Profit Calculator Integration

Use your existing `calculator.py` with Omni-Channel signals:

```python
from liquidation_engine.calculator import ProfitabilityCalculator

calculator = ProfitabilityCalculator()

async def evaluate_opportunity(signal: OpportunitySignal):
    """Evaluate opportunity using existing calculator"""
    
    result = calculator.calculate(
        debt_amount_usd=signal.debt_amount / 1e6,  # USDC
        collateral_amount_usd=signal.collateral_amount / 1e18 * 2000,  # ETH
        liquidation_bonus=0.05,  # Aave V3 bonus
        flash_loan_provider='aave_v3',
        gas_price_gwei=signal.gas_price_gwei,
        eth_price_usd=2000,
        chain_id=signal.chain_id,
    )
    
    # Update signal with profitability data
    signal.net_profit_usd = result.net_profit_usd
    signal.metadata['roi_percent'] = result.roi_percent
    
    return result.is_profitable
```

### 3. Executor Integration

Connect to your existing `LiquidationExecutor.sol`:

```python
from web3 import Web3
from omni_channel import OpportunitySignal, ExecutionModule

class LiquidationIntegration:
    """Integrate with LiquidationExecutor.sol"""
    
    def __init__(self, executor_address: str, w3: Web3):
        self.w3 = w3
        self.executor_address = executor_address
        
        # Load contract ABI
        with open('contracts/liquidation/LiquidationExecutor.json') as f:
            abi = json.load(f)
        
        self.executor_contract = w3.eth.contract(
            address=Web3.to_checksum_address(executor_address),
            abi=abi['abi']
        )
    
    async def execute(self, signal: OpportunitySignal):
        """Execute liquidation from signal"""
        
        # Build transaction
        tx = self.executor_contract.functions.liquidate(
            signal.user_address,
            signal.debt_asset,
            signal.collateral_asset,
            signal.debt_amount,
        ).build_transaction({
            'from': self.w3.eth.accounts[0],
            'gasPrice': signal.gas_price_gwei * 1e9,
            'gas': signal.gas_estimate,
        })
        
        # Sign and send
        signed = self.w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        
        return tx_hash.hex()
```

---

## 📊 Data Flow

### Complete Signal Flow

```
1. Mempool Radar detects transaction
         ↓
2. Advanced Filter identifies trigger
         ↓
3. Signal Merger deduplicates
         ↓
4. OpportunitySignal created
         ↓
5. Signal Queue (priority-based)
         ↓
6. ML Aggregator scores & ranks
         ↓
7. Execution Router directs to module
         ↓
8. Existing Liquidation Engine executes
         ↓
9. Treasury collects profit
```

---

## 🔍 Example: End-to-End Liquidation

```python
import asyncio
from omni_channel import (
    OmniOrchestrator, OpportunitySignal, SignalType, ExecutionModule
)
from liquidation_engine.calculator import ProfitabilityCalculator
from liquidation_engine.executor import LiquidationExecutor

async def main():
    # Configuration
    config = {
        'bloxroute_api_key': 'YOUR_KEY',
        'infura_api_key': 'YOUR_KEY',
    }
    
    # Initialize components
    orchestrator = OmniOrchestrator(config)
    calculator = ProfitabilityCalculator()
    executor = LiquidationExecutor()
    
    # Define signal handler
    async def handle_liquidation(signal: OpportunitySignal):
        print(f"🎯 Liquidation Opportunity Detected")
        print(f"   User: {signal.user_address}")
        print(f"   Debt: {signal.debt_amount / 1e6:.2f} USDC")
        print(f"   Collateral: {signal.collateral_amount / 1e18:.2f} WETH")
        print(f"   Health Factor: {signal.health_factor:.3f}")
        
        # Calculate profitability
        profit = calculator.calculate(
            debt_amount_usd=signal.debt_amount / 1e6,
            collateral_amount_usd=signal.collateral_amount / 1e18 * 2000,
            liquidation_bonus=0.05,
            flash_loan_provider='aave_v3',
            gas_price_gwei=signal.gas_price_gwei,
            eth_price_usd=2000,
        )
        
        print(f"   Expected Profit: ${profit.net_profit_usd:.2f}")
        print(f"   ROI: {profit.roi_percent:.1f}%")
        
        if profit.is_profitable:
            # Execute liquidation
            tx_hash = await executor.execute_liquidation(
                user=signal.user_address,
                debt_asset=signal.debt_asset,
                collateral_asset=signal.collateral_asset,
            )
            
            print(f"   ✅ Liquidation executed: {tx_hash}")
    
    # Register handler
    orchestrator.signal_router.register_module(
        ExecutionModule.LIQUIDATION_ENGINE
    )
    
    # Add routing rule
    def route_liquidations(signal: OpportunitySignal):
        if signal.signal_type == SignalType.LIQUIDATION:
            return ExecutionModule.LIQUIDATION_ENGINE
        return ExecutionModule.MANUAL_REVIEW
    
    orchestrator.signal_router.add_routing_rule(route_liquidations)
    
    # Start system
    await orchestrator.start()

asyncio.run(main())
```

---

## 🎯 Configuration

### Environment Variables

```bash
# Omni-Channel API Keys
export BLOXROUTE_API_KEY="your_bloxroute_key"
export INFURA_API_KEY="your_infura_key"
export BLOCKNATIVE_API_KEY="your_blocknative_key"

# Existing Cryo1 Configuration
export ALCHEMY_API_KEY="your_alchemy_key"
export PRIVATE_KEY="your_private_key"
export TREASURY_ADDRESS="0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4"
```

### Configuration File

```yaml
# config.yaml
omni_channel:
  enabled: true
  providers:
    bloxroute:
      api_key: ${BLOXROUTE_API_KEY}
      enabled: true
    infura:
      api_key: ${INFURA_API_KEY}
      enabled: true
    blocknative:
      api_key: ${BLOCKNATIVE_API_KEY}
      enabled: true
  
  filters:
    large_swap_threshold_usd: 100000
    flash_loan_threshold_usd: 500000
    min_confidence_score: 0.5
  
  execution:
    liquidation_engine:
      enabled: true
      min_profit_usd: 50
      max_gas_price_gwei: 100
    arbitrage:
      enabled: false
    backrun:
      enabled: true
```

---

## 🧪 Testing Integration

### Unit Test

```python
# test_integration.py

import pytest
from omni_channel import OpportunitySignal, SignalType
from liquidation_engine.calculator import ProfitabilityCalculator

def test_signal_to_profitability():
    """Test converting signal to profitability calculation"""
    
    # Create test signal
    signal = OpportunitySignal(
        signal_type=SignalType.LIQUIDATION,
        debt_amount=10000 * 10**6,  # $10k USDC
        collateral_amount=5 * 10**18,  # 5 WETH
        health_factor=0.95,
        gas_price_gwei=30,
    )
    
    # Calculate profitability
    calculator = ProfitabilityCalculator()
    result = calculator.calculate(
        debt_amount_usd=signal.debt_amount / 1e6,
        collateral_amount_usd=5 * 2000,  # 5 WETH @ $2000
        liquidation_bonus=0.05,
        flash_loan_provider='aave_v3',
        gas_price_gwei=signal.gas_price_gwei,
        eth_price_usd=2000,
    )
    
    assert result.is_profitable
    assert result.net_profit_usd > 0
```

### Integration Test

```python
# test_integration_e2e.py

import pytest
from omni_channel import OmniOrchestrator

@pytest.mark.asyncio
async def test_omni_channel_integration():
    """Test end-to-end signal flow"""
    
    config = {
        'enable_mempool_radar': False,  # Mock mode
        'enable_kafka': False,
    }
    
    orchestrator = OmniOrchestrator(config)
    
    # Start orchestrator
    task = asyncio.create_task(orchestrator.start())
    
    # Wait for initialization
    await asyncio.sleep(2)
    
    # Check stats
    stats = orchestrator.get_stats()
    assert stats['is_running']
    assert 'signals_processed' in stats
    
    # Stop
    orchestrator.is_running = False
    await task
```

---

## 📈 Performance Tuning

### Queue Configuration

```python
# Tune queue sizes based on load
config = {
    'signal_queue_max_size': 10000,  # Increase for high volume
    'processing_interval_ms': 50,    # Decrease for lower latency
}
```

### Provider Prioritization

```python
# Prioritize faster providers
provider_configs = {
    'bloxroute': ProviderConfig(
        api_key='key',
        timeout_ms=3000,  # 3s timeout
    ),
    'infura': ProviderConfig(
        api_key='key',
        timeout_ms=5000,  # 5s timeout
    ),
}
```

---

## 🔐 Security Best Practices

### API Key Management

```python
# Use environment variables
import os
from dotenv import load_dotenv

load_dotenv()

config = {
    'bloxroute_api_key': os.getenv('BLOXROUTE_API_KEY'),
    'infura_api_key': os.getenv('INFURA_API_KEY'),
}
```

### Rate Limiting

```python
from asyncio import Semaphore

class RateLimitedExecutor:
    def __init__(self, max_concurrent=5):
        self.semaphore = Semaphore(max_concurrent)
    
    async def execute(self, signal: OpportunitySignal):
        async with self.semaphore:
            # Execute with rate limiting
            pass
```

---

## 📊 Monitoring & Observability

### Metrics to Track

```python
# Key metrics
metrics = {
    'signals_detected': 0,
    'signals_processed': 0,
    'signals_executed': 0,
    'success_rate': 0.0,
    'avg_profit_per_signal': 0.0,
    'total_profit_usd': 0.0,
    'avg_latency_ms': 0.0,
}
```

### Logging

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger('omni_channel')
```

---

## 🎉 Success Checklist

- [ ] Omni-Channel installed and configured
- [ ] API keys set up for mempool providers
- [ ] Signal handlers registered
- [ ] Profit calculator integrated
- [ ] Executor integration tested
- [ ] End-to-end test passing
- [ ] Monitoring configured
- [ ] Production deployment ready

---

**🔌 OMNI-CHANNEL INTEGRATION GUIDE**

*Seamlessly integrate with your existing Cryo1 liquidation engine*

*For questions or support, refer to the main documentation*
