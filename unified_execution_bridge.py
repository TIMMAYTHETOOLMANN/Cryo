#!/usr/bin/env python3
"""
UNIFIED EXECUTION BRIDGE
Connects opportunity signals to smart contract execution

This is the production-ready bridge that:
1. Receives OpportunitySignal from Omni-Channel system
2. Converts signal to ExecutionRequest with proper calldata
3. Executes via deployed smart contracts (LiquidationExecutor.sol, FlashLoanArbitrageExecutor.sol)
4. Monitors transaction and reports profit

Usage:
    python unified_execution_bridge.py
"""

import asyncio
import logging
import os
import sys
import time
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from omni_channel.data_lake.data_models import OpportunitySignal, SignalType, ExecutionModule, SignalSource
from omni_channel.execution_router.execution_interface import ExecutionRequest, ExecutionType
from omni_channel.execution_router.liquidation_executor import LiquidationExecutor
from omni_channel.execution_router.execution_manager import ExecutionManager, create_execution_request

# Also import detectors' SignalType for isinstance checks across modules
try:
    from MODULE_1_LIQUIDATION_ENGINE.detectors.data_lake.data_models import SignalType as DetectorSignalType
except ImportError:
    DetectorSignalType = SignalType

logger = logging.getLogger(__name__)

# Load environment
load_dotenv()


class UnifiedExecutionBridge:
    """
    Production bridge connecting opportunity signals to smart contract execution
    
    Signal Flow:
    1. OpportunitySignal detected (from Mempool Radar, Contract Crawler, etc.)
    2. Convert to ExecutionRequest with proper calldata encoding
    3. Execute via LiquidationExecutor or FlashLoanArbitrageExecutor contract
    4. Monitor transaction and collect profit
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.execution_manager: Optional[ExecutionManager] = None
        self.liquidation_executor: Optional[LiquidationExecutor] = None
        
        # Configuration from environment
        self.private_key = os.getenv('PRIVATE_KEY')
        self.treasury_address = os.getenv('TREASURY_ADDRESS', '0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4')
        self.min_profit_usd = float(os.getenv('MIN_PROFIT_USD', '0.01'))
        
        print("\n🔗 UNIFIED EXECUTION BRIDGE INITIALIZED")
        print("=" * 60)
        print(f"   Treasury: {self.treasury_address}")
        print(f"   Min Profit: ${self.min_profit_usd}")
        print(f"   Private Key Set: {'✅ Yes' if self.private_key else '❌ No'}")
        print("=" * 60)
    
    async def initialize(self):
        """Initialize execution components"""
        print("\n🔧 Initializing execution bridge...")
        
        # Initialize liquidation executor
        self.liquidation_executor = LiquidationExecutor({
            'liquidation': {
                'min_profit_usd': self.min_profit_usd,
            }
        })
        await self.liquidation_executor.initialize()
        
        # Initialize execution manager
        self.execution_manager = ExecutionManager({
            'liquidation': {},
            'arbitrage': {},
            'backrun': {},
            'cross_chain': {},
        })
        await self.execution_manager.start()
        
        print("   ✅ Execution bridge ready")
    
    async def shutdown(self):
        """Shutdown execution bridge"""
        print("\n🛑 Shutting down execution bridge...")
        
        if self.execution_manager:
            await self.execution_manager.stop()
        
        if self.liquidation_executor:
            await self.liquidation_executor.shutdown()
        
        print("   ✅ Execution bridge stopped")
    
    # Signal type values that have on-chain execution paths
    _EXECUTABLE_TYPE_VALUES = {'liquidation', 'arbitrage', 'LIQUIDATION', 'ARBITRAGE'}

    async def process_signal(self, signal) -> Dict[str, Any]:
        """
        Process opportunity signal and execute if profitable.

        Accepts both OpportunitySignal (omni_channel or detectors variant)
        and any duck-typed object with signal_type, expected_value_usd,
        confidence, and metadata attributes.

        Returns:
            Execution result dictionary
        """
        # ── Safely extract attributes (duck-typing for cross-module compat) ──
        sig_type = getattr(signal, 'signal_type', None)
        sig_type_val = sig_type.value if hasattr(sig_type, 'value') else str(sig_type)
        expected_usd = float(getattr(signal, 'expected_value_usd', 0) or getattr(signal, 'net_profit_usd', 0))
        confidence = float(getattr(signal, 'confidence', 0))

        # ── Profit gate ──
        if expected_usd < self.min_profit_usd:
            return {'status': 'skipped', 'reason': 'below_min_profit'}

        # ── Confidence gate (very low bar — flash loans = risk-free except gas) ──
        if confidence < 0.10:
            return {'status': 'skipped', 'reason': 'low_confidence'}

        # ── Check if this signal type has an on-chain executor ──
        if sig_type_val not in self._EXECUTABLE_TYPE_VALUES:
            # Not an error — just not yet routable on-chain.
            return {'status': 'skipped', 'reason': f'no_executor_for_{sig_type_val}'}

        # Convert signal to execution request
        execution_request = self._convert_signal_to_request(signal)

        # Validate request
        if not await self.liquidation_executor.validate_request(execution_request):
            return {'status': 'failed', 'reason': 'invalid_request'}

        # Execute
        result = await self.liquidation_executor.execute(execution_request)

        # Report result
        if result.status.value == 'confirmed':
            print(f"   ✅ EXEC OK | {sig_type_val} chain={getattr(signal, 'chain_id', '?')} "
                  f"tx={result.tx_hash} profit=${result.profit_usd}")
            return {
                'status': 'success',
                'tx_hash': result.tx_hash,
                'block': result.block_number,
                'profit_usd': result.profit_usd,
            }
        else:
            return {
                'status': 'failed',
                'reason': result.error_message,
            }
    
    def _convert_signal_to_request(self, signal) -> ExecutionRequest:
        """
        Convert OpportunitySignal to ExecutionRequest with proper calldata.

        Uses duck-typed attribute access so both omni_channel and detectors
        variants of OpportunitySignal work interchangeably.
        """
        from omni_channel.execution_router.execution_interface import ExecutionType
        
        sig_type = getattr(signal, 'signal_type', SignalType.LIQUIDATION)
        metadata = getattr(signal, 'metadata', {}) or {}

        # Determine execution type from signal
        exec_type_map = {
            SignalType.LIQUIDATION: ExecutionType.LIQUIDATION,
            SignalType.ARBITRAGE: ExecutionType.ARBITRAGE,
            SignalType.BACKRUN: ExecutionType.BACKRUN,
            SignalType.CROSS_CHAIN_ARB: ExecutionType.CROSS_CHAIN,
        }
        exec_type = exec_type_map.get(sig_type, ExecutionType.CUSTOM)

        # Build calldata based on signal type
        calldata = '0x'
        if sig_type == SignalType.LIQUIDATION:
            # Build liquidation calldata
            calldata = self.liquidation_executor.build_liquidation_calldata(
                protocol=metadata.get('protocol', 'aave_v3'),
                debt_asset=metadata.get('debt_asset', '0x' + '0' * 40),
                debt_amount=metadata.get('debt_amount', 0),
                user=metadata.get('user', '0x' + '0' * 40),
                collateral_asset=metadata.get('collateral_asset', '0x' + '0' * 40),
                min_collateral=metadata.get('min_collateral', 0),
            )
        elif sig_type == SignalType.ARBITRAGE:
            # Check if Reserve Protocol arbitrage
            if metadata.get('protocol') == 'ReserveProtocol':
                calldata = self.liquidation_executor.build_reserve_arbitrage_calldata(
                    rToken=metadata.get('rToken', '0x' + '0' * 40),
                    flash_loan_amount=metadata.get('flash_loan_amount', 0),
                )
        
        # Determine target contract:
        #  - Liquidations → Aave pool address from signal (direct call)
        #  - Flash loan liquidations → our LiquidationExecutor contract
        #  - Reserve arb → FlashLoanArbitrageExecutor
        is_flash_loan = metadata.get('is_flash_loan', False)
        sig_target = getattr(signal, 'target_contract', None)

        if sig_type == SignalType.ARBITRAGE and metadata.get('protocol') == 'ReserveProtocol':
            target = self.liquidation_executor.flash_executor or self.liquidation_executor.liquidation_executor_v1
        elif sig_type == SignalType.LIQUIDATION and not is_flash_loan and sig_target:
            # Direct Aave pool liquidation — target is the pool address from the signal
            target = sig_target
        else:
            # Flash loan liquidation or fallback → our executor contract
            target = sig_target or self.liquidation_executor.liquidation_executor_v1

        expected_usd = float(getattr(signal, 'expected_value_usd', 0))
        gas_est = int(getattr(signal, 'gas_estimate', 0) or 500000)
        gas_gwei = float(getattr(signal, 'gas_price_gwei', 0) or 0)
        src_module = getattr(signal, 'source_module', None)
        src_val = src_module.value if hasattr(src_module, 'value') else str(src_module)

        # Create execution request
        request = ExecutionRequest(
            request_id=getattr(signal, 'signal_id', '') or '',
            execution_type=exec_type,
            chain_id=getattr(signal, 'chain_id', 1),
            opportunity_data={
                'signal_type': sig_type.value if hasattr(sig_type, 'value') else str(sig_type),
                'expected_value_usd': expected_usd,
                'confidence': float(getattr(signal, 'confidence', 0)),
            },
            target_contract=target,
            calldata=calldata,
            value=0,  # No ETH sent with transaction
            gas_limit=gas_est,
            gas_price=int(gas_gwei * 1e9) if gas_gwei > 0 else int(0.5e9),  # 0.5 gwei default (not 30!)
            deadline=int(time.time()) + 300,  # 5 minute timeout
            metadata={
                'expected_profit_usd': expected_usd,
                'source': src_val,
                'debt_asset': metadata.get('debt_asset'),
                'debt_amount': metadata.get('debt_amount'),
                'collateral_asset': metadata.get('collateral_asset'),
                'user': metadata.get('user'),
                'min_collateral': metadata.get('min_collateral', 0),
                'protocol': metadata.get('protocol'),
                'is_flash_loan': metadata.get('is_flash_loan', True),
                'rToken': metadata.get('rToken'),
                'flash_loan_amount': metadata.get('flash_loan_amount', 0),
                'flash_executor': metadata.get('flash_executor'),
            }
        )
        
        return request


async def main():
    """
    Production entry point — loads targets.json and executes real opportunities.
    """
    import json
    from pathlib import Path

    TARGETS_FILE = Path(__file__).resolve().parent / "targets.json"

    print("\n" + "=" * 70)
    print("  UNIFIED EXECUTION BRIDGE — LIVE TARGET PROCESSOR")
    print("=" * 70)

    # ── Load targets ──────────────────────────────────────────────
    if not TARGETS_FILE.exists():
        print("\n  ❌ targets.json not found. Run target_finder.py first.")
        print("     python target_finder.py")
        return

    with open(TARGETS_FILE) as f:
        target_data = json.load(f)

    targets = target_data.get("targets", [])
    eth_price = target_data.get("eth_price_usd", 2000.0)
    block = target_data.get("block", 0)

    print(f"\n  Loaded {len(targets)} targets from targets.json")
    print(f"  ETH/USD: ${eth_price:,.2f} | Block: {block:,}")

    # ── Classify targets ──────────────────────────────────────────
    executable = []      # HF < 1.0 liquidations + Reserve Protocol arb
    watchlist = []       # HF 1.0-1.05 (not yet liquidatable)

    for t in targets:
        ttype = t.get("type", "")
        if ttype == "LIQUIDATION":
            hf = t.get("health_factor", 999)
            is_exec = t.get("executable_now", hf < 1.0)
            profit = t.get("net_profit_usd", 0)
            if is_exec and profit > 0:
                executable.append(t)
            else:
                watchlist.append(t)
        elif ttype == "RESERVE_ARB":
            profit = t.get("estimated_profit_usd", 0)
            if profit > 0:
                executable.append(t)
        else:
            watchlist.append(t)

    print(f"\n  Executable NOW:  {len(executable)} targets")
    print(f"  Watchlist:       {len(watchlist)} targets (HF > 1.0, monitoring)")

    # ── Print watchlist summary ───────────────────────────────────
    if watchlist:
        print(f"\n  {'─' * 66}")
        print(f"  WATCHLIST — positions approaching liquidation (HF > 1.0)")
        print(f"  {'─' * 66}")
        for i, w in enumerate(watchlist[:10], 1):
            hf = w.get("health_factor", "?")
            debt = w.get("debt_usd", 0)
            user = w.get("user", "?")[:16]
            urgency = w.get("urgency", "?")
            pct_from_liq = ((hf - 1.0) * 100) if isinstance(hf, (int, float)) else 0
            print(f"    {i:2d}. HF={hf:.4f} ({pct_from_liq:.2f}% from liq) | "
                  f"Debt=${debt:>14,.2f} | {urgency:12s} | {user}...")

    # ── Print executable targets ──────────────────────────────────
    if not executable:
        print(f"\n  ⚠️  No immediately executable targets.")
        print(f"  All {len(watchlist)} liquidation targets have HF > 1.0 (not yet liquidatable).")
        print(f"  Aave V3 only allows liquidation when Health Factor < 1.0.")
        print()
        print(f"  Reserve Protocol targets require flash loan executor deployment.")
        print()
        print(f"  OPTIONS:")
        print(f"    1. Run continuous monitoring:  python main.py --master")
        print(f"       (Watches for HF drops in real-time)")
        print(f"    2. Re-scan for fresh targets:  python target_finder.py")
        print(f"    3. Deploy Reserve arb executor:")
        print(f"       forge script script/DeployFlashLoanArbitrage.s.sol \\")
        print(f"         --rpc-url $MAINNET_RPC_URL --private-key $PRIVATE_KEY --broadcast")
        print()
        print("=" * 70)

        # Even without executable liquidations, launch continuous monitoring
        print("\n  🔄 Launching continuous monitoring mode...")
        print("     Will auto-execute when any position drops below HF 1.0\n")
        await _run_continuous_monitor(target_data, watchlist)
        return

    # ── Execute ───────────────────────────────────────────────────
    print(f"\n  {'─' * 66}")
    print(f"  EXECUTING {len(executable)} TARGETS")
    print(f"  {'─' * 66}")

    # Initialize bridge
    bridge = UnifiedExecutionBridge()

    try:
        await bridge.initialize()

        if not bridge.private_key:
            print("\n  ⚠️  PRIVATE_KEY not set — live execution DISABLED")
            print("     To enable live execution, set PRIVATE_KEY in .env file")
            print("     Bridge will build + validate calldata but NOT sign/send tx.")

        results = []
        for i, target in enumerate(executable, 1):
            ttype = target.get("type", "")
            profit = target.get("net_profit_usd", target.get("estimated_profit_usd", 0))

            print(f"\n  [{i}/{len(executable)}] {ttype} — est. profit ${profit:,.2f}")

            # Convert target to OpportunitySignal
            signal = _target_to_signal(target, target_data)

            if signal is None:
                print(f"    ⚠️  Could not convert target to signal, skipping")
                continue

            try:
                result = await bridge.process_signal(signal)
                results.append(result)
            except Exception as e:
                print(f"    ❌ Execution error: {e}")
                results.append({"status": "error", "reason": str(e)})

        # ── Summary ───────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("  EXECUTION SUMMARY")
        print("=" * 70)

        succeeded = sum(1 for r in results if r.get("status") == "success")
        failed = sum(1 for r in results if r.get("status") in ("failed", "error"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        total_profit = sum(r.get("profit_usd", 0) for r in results if r.get("status") == "success")

        print(f"  Succeeded:  {succeeded}")
        print(f"  Failed:     {failed}")
        print(f"  Skipped:    {skipped}")
        print(f"  Total P&L:  ${total_profit:,.2f}")
        print("=" * 70)

    except KeyboardInterrupt:
        print("\n\n  🛑 Received interrupt")
    except Exception as e:
        print(f"\n  ❌ Bridge error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bridge.shutdown()


def _target_to_signal(target: Dict[str, Any], target_data: Dict[str, Any]) -> Optional['OpportunitySignal']:
    """Convert a targets.json entry to an OpportunitySignal."""
    import uuid

    ttype = target.get("type", "")

    if ttype == "LIQUIDATION":
        return OpportunitySignal(
            signal_id=f"target_{uuid.uuid4().hex[:12]}",
            signal_type=SignalType.LIQUIDATION,
            source_module=SignalSource.ENHANCED_DETECTOR,
            chain_id=target.get("chain_id", 1),
            target_contract=target.get("pool_address", "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"),
            user_address=target.get("user"),
            expected_value_usd=target.get("net_profit_usd", 0),
            gross_profit_usd=target.get("gross_profit_usd", 0),
            net_profit_usd=target.get("net_profit_usd", 0),
            confidence=0.85 if target.get("health_factor", 999) < 1.0 else 0.50,
            urgency_score=90 if target.get("urgency") == "CRITICAL" else 70,
            execution_complexity=3,
            gas_estimate=600000,
            gas_price_gwei=30,
            health_factor=target.get("health_factor", 0),
            metadata={
                "protocol": target.get("protocol", "aave_v3"),
                "user": target.get("user"),
                "debt_asset": target.get("debt_asset", ""),
                "collateral_asset": target.get("collateral_asset", ""),
                "debt_amount": int(target.get("debt_usd", 0) * 1e6),  # USDC scale
                "min_collateral": 0,
                "is_flash_loan": True,
                "health_factor": target.get("health_factor"),
                "close_factor": target.get("close_factor", 0.5),
            },
        )
    elif ttype == "RESERVE_ARB":
        return OpportunitySignal(
            signal_id=f"target_{uuid.uuid4().hex[:12]}",
            signal_type=SignalType.ARBITRAGE,
            source_module=SignalSource.ENHANCED_DETECTOR,
            chain_id=1,
            target_contract=target.get("rToken_address", ""),
            expected_value_usd=target.get("estimated_profit_usd", 0),
            gross_profit_usd=target.get("estimated_profit_usd", 0),
            net_profit_usd=target.get("estimated_profit_usd", 0),
            confidence=0.70,
            urgency_score=85,
            execution_complexity=5,
            gas_estimate=800000,
            gas_price_gwei=30,
            metadata={
                "protocol": "ReserveProtocol",
                "rToken": target.get("rToken_address", ""),
                "rToken_name": target.get("rToken", ""),
                "collateral_ratio": target.get("collateral_ratio", 1.0),
                "flash_loan_amount": int(float(target.get("total_supply_raw", "0")) * 0.1),
                "is_flash_loan": True,
                "flash_executor": target.get("flash_executor", ""),
            },
        )

    return None


async def _run_continuous_monitor(target_data: Dict[str, Any], watchlist: list):
    """
    Continuously monitor watchlist positions for HF drops below 1.0.
    When a position becomes liquidatable, execute immediately.
    """
    from web3 import Web3
    import json

    rpc_url = os.getenv("ETH_RPC_URL", os.getenv("MAINNET_RPC_URL", ""))
    if not rpc_url:
        print("  ❌ No RPC URL configured — cannot monitor")
        return

    AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
    AAVE_ABI = json.loads('[{"inputs":[{"name":"user","type":"address"}],'
                          '"name":"getUserAccountData","outputs":['
                          '{"name":"totalCollateralBase","type":"uint256"},'
                          '{"name":"totalDebtBase","type":"uint256"},'
                          '{"name":"availableBorrowsBase","type":"uint256"},'
                          '{"name":"currentLiquidationThreshold","type":"uint256"},'
                          '{"name":"ltv","type":"uint256"},'
                          '{"name":"healthFactor","type":"uint256"}],'
                          '"stateMutability":"view","type":"function"}]')

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
    pool = w3.eth.contract(address=Web3.to_checksum_address(AAVE_POOL), abi=AAVE_ABI)

    # Sort watchlist by HF (closest to liquidation first)
    watchlist.sort(key=lambda x: x.get("health_factor", 999))

    print(f"  Monitoring {len(watchlist)} positions (refresh every 12s)...")
    print(f"  Press Ctrl+C to stop.\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            block = w3.eth.block_number
            now_str = __import__("datetime").datetime.now().strftime("%H:%M:%S")
            alerts = []

            for pos in watchlist[:20]:  # Check top 20 closest to liquidation
                user = pos.get("user", "")
                try:
                    data = pool.functions.getUserAccountData(
                        Web3.to_checksum_address(user)
                    ).call()
                    hf = data[5] / 1e18 if data[5] > 0 else 999.0
                    debt_usd = data[1] / 1e8
                    pos["health_factor"] = hf  # Update live

                    if hf < 1.0:
                        alerts.append(pos)
                except Exception:
                    pass

            # Print status line
            closest_hf = min((p.get("health_factor", 999) for p in watchlist[:20]), default=999)
            print(f"  [{now_str}] Block {block:,} | Cycle {cycle} | "
                  f"Closest HF: {closest_hf:.6f} | "
                  f"{'🔴 LIQUIDATABLE!' if alerts else '🟢 All safe'}")

            if alerts:
                print(f"\n  🚨 {len(alerts)} POSITIONS NOW LIQUIDATABLE!")
                for a in alerts:
                    print(f"    → HF={a['health_factor']:.6f} | "
                          f"Debt=${a.get('debt_usd', 0):,.2f} | "
                          f"User: {a['user'][:16]}...")

                # Execute immediately
                print(f"\n  ⚡ Triggering immediate execution...")
                bridge = UnifiedExecutionBridge()
                try:
                    await bridge.initialize()
                    for alert_target in alerts:
                        signal = _target_to_signal(alert_target, target_data)
                        if signal:
                            signal.confidence = 0.90  # High confidence — HF confirmed < 1.0
                            result = await bridge.process_signal(signal)
                            print(f"    Result: {result}")
                except Exception as e:
                    print(f"    ❌ Execution error: {e}")
                finally:
                    await bridge.shutdown()

                # Remove executed from watchlist
                executed_users = {a["user"] for a in alerts}
                watchlist = [w for w in watchlist if w["user"] not in executed_users]

                if not watchlist:
                    print("  ✅ All watchlist positions processed.")
                    break

            await asyncio.sleep(12)  # ~1 block on Ethereum

    except KeyboardInterrupt:
        print("\n\n  🛑 Monitor stopped.")
    except Exception as e:
        print(f"\n  ❌ Monitor error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
