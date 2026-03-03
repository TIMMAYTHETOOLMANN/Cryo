#!/usr/bin/env python3
"""
TRIANGULATED PROFIT ENGINE — Master Orchestrator
==================================================
The central nervous system connecting:

  Opportunity Scanner → Gas Optimizer → Flash Loan Router →
  Execution Router → Profit Ledger → Heat Map → Capital Multiplier → (loop)

Three-phase lifecycle:
  Phase 1 (Cold Start):  Flash-loan-only extraction, zero capital
  Phase 2 (Heat Map):    Pattern learning, frequency optimization
  Phase 3 (Multiplier):  Exponential compounding with capital reinvestment

Signal Flow:
  1. Scanner detects opportunity across 6 vectors
  2. Gas optimizer validates profitability after gas
  3. Flash loan router selects cheapest 0-capital provider
  4. Execution manager routes to the right executor
  5. Transaction submitted & confirmed
  6. Profit ledger records result
  7. Heat map updates pattern weights
  8. Capital multiplier adjusts position sizes
  9. Repeat

Target Projections:
  Hour 1:   $2,400 - $4,800
  Hour 12:  $45,000 - $65,000
  Hour 24:  $3.2M - $12.1M (with multiplier)
"""

import asyncio
import os
import sys
import time
import uuid
import json
from typing import Dict, List, Optional, Any
from datetime import datetime

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omni_channel.data_lake.data_models import (
    OpportunitySignal, SignalType, SignalSource, ExecutionModule,
)
from omni_channel.execution_router.execution_interface import (
    ExecutionRequest, ExecutionResult, ExecutionStatus, ExecutionType,
)
from omni_channel.execution_router.execution_manager import ExecutionManager
from omni_channel.execution_router.liquidation_executor import LiquidationExecutor

from .profit_ledger import ProfitLedger, ProfitEntry, PhaseState
from .heat_map import HeatMap
from .capital_multiplier import CapitalMultiplier, MultiplierState
from .gas_optimizer import GasOptimizer
from .flash_loan_router import FlashLoanRouter
from .opportunity_scanner import OpportunityScanner, OpportunityVector


class TriangulatedProfitEngine:
    """
    Master orchestrator for the complete profit extraction pipeline.
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False
        self.start_time: Optional[float] = None

        # ── Initialize all subsystems ──

        # 1. Profit Ledger (tracks P&L and phase transitions)
        self.ledger = ProfitLedger(self.config.get('ledger', {}))

        # 2. Heat Map (pattern learning)
        self.heat_map = HeatMap(self.config.get('heat_map', {}))

        # 3. Gas Optimizer (dynamic gas prediction)
        self.gas_optimizer = GasOptimizer(self.config.get('gas', {}))

        # 4. Flash Loan Router (zero-capital provider selection)
        self.flash_loan_router = FlashLoanRouter(self.config.get('flash_loan', {}))

        # 5. Capital Multiplier (Phase 3 compounding)
        self.capital_multiplier = CapitalMultiplier(
            self.ledger, self.heat_map, self.config.get('multiplier', {})
        )

        # 5.5. RPC Gateway (Module 10 — intelligent load balancing)
        from .rpc_gateway import RPCGateway, build_default_gateway
        self.rpc_gateway = build_default_gateway(self.config.get('rpc_gateway', {}))

        # 6. Opportunity Scanner (6-vector detection) — uses gateway
        self.scanner = OpportunityScanner(
            self.gas_optimizer, self.flash_loan_router, self.heat_map,
            self.config.get('scanner', {}),
            rpc_gateway=self.rpc_gateway,
        )

        # 7. Execution Manager (routes signals to executors)
        self.execution_manager = ExecutionManager(self.config.get('execution', {
            'liquidation': {},
            'arbitrage': {},
            'backrun': {},
            'cross_chain': {},
        }))

        # 8. Zero-Revert Pipeline (replaces JIT engine — oracle-driven precision execution)
        self.zero_revert = None  # Initialized after scanner connects to chains

        # Register phase transition callback
        self.ledger.on_phase_change(self._on_phase_transition)

        # Stats
        self.opportunities_received = 0
        self.opportunities_executed = 0
        self.opportunities_skipped = 0
        self.consecutive_errors = 0

        print("\n" + "=" * 70)
        print("  ⚡ TRIANGULATED PROFIT ENGINE")
        print("  ⚡ Multi-Vector Flash Loan Extraction System")
        print("=" * 70)
        print(f"  Phase:            {self.ledger.current_phase.value}")
        print(f"  Vectors:          6 (liquidation, preemptive, protocols, cross-chain, pools, NFT)")
        print(f"  Flash Providers:  {len(self.flash_loan_router.providers)}")
        print(f"  Gas Chains:       {len(self.gas_optimizer.chain_states)}")
        print(f"  Min Profit:       ${self.scanner.min_profit_usd}")
        print("=" * 70)

    # ──────────────────────────────────────────────
    # LIFECYCLE
    # ──────────────────────────────────────────────

    async def start(self):
        """Start the complete profit engine."""
        self.is_running = True
        self.start_time = time.time()

        print(f"\n🚀 STARTING TRIANGULATED PROFIT ENGINE")
        print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Phase: {self.ledger.current_phase.value}")
        print()

        # Initialize scanner (connects to all chains)
        await self.scanner.initialize()

        # Initialize Zero-Revert Pipeline — needs scanner's W3 providers
        from .zero_revert_pipeline import ZeroRevertPipeline
        self.zero_revert = ZeroRevertPipeline(
            self.scanner._w3, self.config
        )
        await self.zero_revert.start()

        # Initialize execution manager
        await self.execution_manager.start()

        # Register opportunity callback
        self.scanner.on_opportunity(self._handle_opportunity)

        # Register execution completion callback
        self.execution_manager.on_completion(self._handle_execution_result)

        # Start background tasks
        tasks = [
            asyncio.create_task(self.scanner.start()),
            asyncio.create_task(self._dashboard_loop()),
            asyncio.create_task(self._rebalance_loop()),
            asyncio.create_task(self._health_check_loop()),
            asyncio.create_task(self._watchlist_sync_loop()),
        ]

        print("✅ All systems online — scanning for profit opportunities...\n")

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except KeyboardInterrupt:
            await self.stop()

    async def stop(self):
        """Graceful shutdown."""
        print("\n🛑 Shutting down Triangulated Profit Engine...")
        self.is_running = False
        await self.scanner.stop()
        await self.execution_manager.stop()

        # Final dashboard
        self.print_full_dashboard()

        # Save state
        self.ledger._persist_state()
        print("   ✅ Engine stopped. State saved.")

    # ──────────────────────────────────────────────
    # SIGNAL HANDLING (core pipeline)
    # ──────────────────────────────────────────────

    async def _handle_opportunity(self, signal: OpportunitySignal):
        """
        Core pipeline: receive opportunity → validate → size → route → execute.
        This is called by the scanner for every detected opportunity.

        Enhancements:
        - Uses execution feedback to deprioritize failing vector/chain/protocol combos
        - Queues gas-rejected opportunities for retry when gas drops
        - Preserves confidence scores in execution metadata
        """
        self.opportunities_received += 1

        try:
            # ── Step 1: Validate signal quality ──
            # Ultra-aggressive: accept nearly everything, let simulate-before-send filter
            if signal.confidence < 0.01:
                self.opportunities_skipped += 1
                return
            if signal.net_profit_usd < 0.01:
                self.opportunities_skipped += 1
                return

            # ── Step 1b: Execution feedback check ──
            # If this vector+chain+protocol has a high failure rate, raise the confidence bar
            opp_type = signal.metadata.get('vector', signal.signal_type.value)
            protocol = signal.metadata.get('protocol', 'unknown')
            feedback_score = self.scanner.get_feedback_score(opp_type, signal.chain_id, protocol)
            # Skip if this combo fails >70% of the time AND signal confidence is below 50%:
            # wasting gas on low-confidence signals from historically failing combos is unprofitable.
            if feedback_score < 0.3 and signal.confidence < 0.5:
                # High failure rate AND low confidence → skip
                self.opportunities_skipped += 1
                return

            # ── Step 2: Capital Multiplier position sizing ──
            position_size = self.capital_multiplier.get_position_size(
                opp_type, signal.chain_id, protocol, signal.net_profit_usd
            )

            # ── Step 3: Flash loan routing (if no own capital or supplementing) ──
            flash_route = None
            total_capital_needed = signal.metadata.get('debt_amount', 0) / 1e8  # Approx USD
            if total_capital_needed <= 0:
                total_capital_needed = signal.expected_value_usd * 10  # Estimate

            if position_size < total_capital_needed:
                # Need flash loan for the difference (or all of it in Phase 1)
                flash_amount = total_capital_needed - position_size
                flash_route = self.flash_loan_router.find_best_route(
                    signal.chain_id,
                    signal.metadata.get('debt_asset_symbol', 'USDC'),
                    flash_amount,
                    signal.gross_profit_usd,
                )
                if flash_route:
                    signal.metadata['flash_provider'] = flash_route.provider.value
                    signal.metadata['flash_fee_usd'] = flash_route.fee_usd
                    signal.metadata['flash_pool'] = flash_route.pool_address

            # ── Step 4: Final profitability gate ──
            # Strip any scanner-baked flash fee from estimated_cost_usd to avoid double-counting
            # when the engine computes its own flash route. estimated_cost_usd should reflect
            # gas costs only; flash fees are added exclusively by the engine's route below.
            scanner_flash_fee = signal.metadata.get('flash_fee_usd', 0.0)
            total_cost = signal.estimated_cost_usd - scanner_flash_fee
            if flash_route:
                total_cost += flash_route.fee_usd

            final_profit = signal.gross_profit_usd - total_cost
            if final_profit < 0.01:  # Accept anything net-positive
                # Queue for gas retry if net loss is small (<$5) and there's real gross profit (>$1);
                # this captures opportunities that are only temporarily unprofitable due to gas spikes.
                if final_profit > -5.0 and signal.gross_profit_usd > 1.0:
                    # Persist the final net profit so the gas retry queue uses a consistent profitability metric
                    signal.net_profit_usd = final_profit
                    self.scanner.queue_gas_retry(signal)
                self.opportunities_skipped += 1
                return

            # ── Step 5: Build execution request ──
            exec_type = self._map_signal_to_exec_type(signal.signal_type)

            # Apply feedback-adjusted priority: boost for high-success combos, penalize low ones
            priority = self._compute_priority(signal)
            if feedback_score > 0.8:
                priority = min(10, priority + 1)  # Boost proven combos
            elif feedback_score < 0.4:
                priority = max(1, priority - 1)  # Penalize failing combos

            # Liquidation requests require a valid target_contract and user address;
            # preemptive/oracle signals lack these and must be skipped.
            if exec_type == ExecutionType.LIQUIDATION:
                if not signal.target_contract or not signal.metadata.get('user'):
                    self.opportunities_skipped += 1
                    return

            request = ExecutionRequest(
                request_id=signal.signal_id,
                execution_type=exec_type,
                chain_id=signal.chain_id,
                opportunity_data={
                    'signal_type': signal.signal_type.value,
                    'expected_value_usd': signal.expected_value_usd,
                    'confidence': signal.confidence,
                    'vector': opp_type,
                },
                target_contract=signal.target_contract or '',
                calldata=signal.metadata.get('calldata', '0x'),
                gas_limit=signal.gas_estimate or 500_000,
                gas_price=int(signal.gas_price_gwei * 1e9) if signal.gas_price_gwei else 0,
                deadline=int(time.time()) + 600,  # 10 min window
                metadata={
                    **signal.metadata,
                    'position_size': position_size,
                    'flash_route': flash_route.provider.value if flash_route else None,
                    'expected_profit_usd': final_profit,
                    'phase': self.ledger.current_phase.value,
                    'health_factor': getattr(signal, 'health_factor', 0),
                    'confidence': signal.confidence,
                    'feedback_score': feedback_score,
                },
            )

            # ── Step 6: Submit to execution manager ──
            await self.execution_manager.submit(request, priority=priority)
            self.opportunities_executed += 1
            self.consecutive_errors = 0

            print(f"   🎯 [{opp_type}] chain={signal.chain_id} profit=${final_profit:.2f} "
                  f"conf={signal.confidence:.0%} pri={priority} fb={feedback_score:.0%}")

        except Exception as e:
            self.consecutive_errors += 1
            if self.consecutive_errors <= 5:
                print(f"   ⚠️ Opportunity handling error: {e}")
            elif self.consecutive_errors == 6:
                print(f"   ⚠️ ADAPTIVE: 5 consecutive errors — adjusting parameters")
                self.scanner.min_profit_usd *= 1.5  # Raise threshold temporarily
                self.scanner.min_confidence *= 1.1

    async def _handle_execution_result(self, result: ExecutionResult):
        """
        Handle completed execution — update ledger, heat map, multiplier,
        and feed outcome back to scanner for closed-loop learning.
        """
        try:
            is_success = result.status == ExecutionStatus.CONFIRMED
            profit_usd = result.profit_usd if is_success else 0.0
            gas_cost = 0.0

            if result.gas_used > 0 and result.effective_gas_price > 0:
                gas_cost_eth = (result.gas_used * result.effective_gas_price) / 1e18
                gas_cost = gas_cost_eth * 2500  # Approximate

            # Determine metadata
            metadata = {}
            # Try to recover metadata from active executions
            queued = self.execution_manager._active_executions.get(result.request_id)
            if queued:
                metadata = queued.request.metadata

            opp_type = metadata.get('vector', 'unknown')
            protocol = metadata.get('protocol', 'unknown')
            chain_id = queued.request.chain_id if queued else 1
            flash_fee = metadata.get('flash_fee_usd', 0.0)

            # ── Record in Ledger ──
            entry = ProfitEntry(
                entry_id=result.request_id,
                timestamp=time.time(),
                chain_id=chain_id,
                opportunity_type=opp_type,
                protocol=protocol,
                tx_hash=result.tx_hash or '',
                gross_profit_usd=profit_usd + gas_cost + flash_fee,
                gas_cost_usd=gas_cost,
                flash_loan_fee_usd=flash_fee,
                net_profit_usd=profit_usd,
                capital_deployed_usd=metadata.get('position_size', 0),
                roi_percent=(profit_usd / max(metadata.get('position_size', 1), 1)) * 100,
                execution_time_ms=int((result.confirmed_at - result.executed_at) * 1000) if result.confirmed_at else 0,
                block_number=result.block_number or 0,
                phase=self.ledger.current_phase,
            )

            new_phase = await self.ledger.record(entry)

            # ── Update Heat Map ──
            self.heat_map.record_observation(
                opportunity_type=opp_type,
                chain_id=chain_id,
                protocol=protocol,
                was_executed=True,
                was_profitable=is_success,
                profit_usd=profit_usd,
                roi_percent=entry.roi_percent,
                competition=metadata.get('competition_estimate', 0.5),
                execution_time_ms=entry.execution_time_ms,
            )

            # ── Closed-Loop Feedback to Scanner ──
            self.scanner.record_execution_outcome(
                vector=opp_type,
                chain_id=chain_id,
                protocol=protocol,
                success=is_success,
                profit_usd=profit_usd,
            )

            # ── Update Flash Loan Router feedback ──
            flash_provider_name = metadata.get('flash_provider')
            if flash_provider_name:
                from .flash_loan_router import FlashLoanProvider
                try:
                    provider = FlashLoanProvider(flash_provider_name)
                    self.flash_loan_router.report_outcome(
                        provider, is_success, metadata.get('flash_amount', 0)
                    )
                except ValueError:
                    pass

            # ── Update Capital Multiplier ──
            if is_success and profit_usd > 0:
                heat_key = f"{opp_type}:{chain_id}:{protocol}"
                await self.capital_multiplier.process_profit(profit_usd, heat_key)
                print(f"   ✅ PROFIT: ${profit_usd:,.2f} [{opp_type}] chain={chain_id}")
            elif not is_success:
                await self.capital_multiplier.process_loss(gas_cost)

        except Exception as e:
            print(f"   ⚠️ Result handling error: {e}")

    # ──────────────────────────────────────────────
    # PHASE TRANSITIONS
    # ──────────────────────────────────────────────

    async def _on_phase_transition(self, old_phase: PhaseState, new_phase: PhaseState):
        """Handle phase transitions."""
        if new_phase == PhaseState.HEAT_MAP:
            print("\n" + "🗺️ " * 20)
            print("  PHASE 2: HEAT MAP ACTIVE")
            print("  System is now learning profitable patterns")
            print("  Optimizing opportunity frequency and margins")
            print("🗺️ " * 20 + "\n")

        elif new_phase == PhaseState.MULTIPLIER:
            print("\n" + "🔄 " * 20)
            print("  PHASE 3: CAPITAL MULTIPLIER ACTIVATED")
            print("  Compounding engine engaging...")
            print("🔄 " * 20 + "\n")

            # Activate multiplier with current capital
            await self.capital_multiplier.activate(self.ledger.cumulative_profit_usd)

    # ──────────────────────────────────────────────
    # BACKGROUND TASKS
    # ──────────────────────────────────────────────

    async def _dashboard_loop(self):
        """Print dashboard periodically."""
        while self.is_running:
            await asyncio.sleep(60)  # Every minute
            self._print_compact_status()

    async def _rebalance_loop(self):
        """Rebalance capital allocations periodically."""
        while self.is_running:
            await asyncio.sleep(300)  # Every 5 minutes
            if self.capital_multiplier.state != MultiplierState.INACTIVE:
                await self.capital_multiplier.rebalance_allocations()

    async def _health_check_loop(self):
        """Self-healing health checks."""
        last_opp_count = 0
        last_heal_time = 0.0

        while self.is_running:
            await asyncio.sleep(60)  # Check every 60s (not 30s)

            # Check scanner is producing opportunities
            now = time.time()
            if self.opportunities_received == last_opp_count and self.start_time:
                elapsed = now - self.start_time
                since_heal = now - last_heal_time
                # Only self-heal once every 10 minutes, not every 30 seconds
                if elapsed > 300 and since_heal > 600:
                    print("   ⚠️ SELF-HEAL: No new opportunities in 10 minutes — relaxing thresholds")
                    self.scanner.min_profit_usd = max(0.01, self.scanner.min_profit_usd * 0.5)
                    self.scanner.min_confidence = max(0.01, self.scanner.min_confidence * 0.5)
                    last_heal_time = now
            else:
                last_opp_count = self.opportunities_received

            # Check for adaptive error handling (5 consecutive identical errors)
            if self.consecutive_errors >= 5:
                print("   ⚠️ ADAPTIVE: Resetting after 5 consecutive errors")
                self.consecutive_errors = 0
                self.scanner.min_profit_usd = 0.01
                self.scanner.min_confidence = 0.01

    # ──────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────

    async def _watchlist_sync_loop(self):
        """Periodically sync scanner positions into Zero-Revert Pipeline."""
        while self.is_running:
            await asyncio.sleep(10)
            try:
                if not self.zero_revert:
                    continue
                # Feed watchlist (positions with known HF < 1.50)
                if self.scanner._watchlist:
                    self.zero_revert.feed_watchlist(self.scanner._watchlist)
                # Every 60s, feed ALL tracked positions for broader ZRP coverage
                if hasattr(self, '_last_full_sync'):
                    if time.time() - self._last_full_sync > 60:
                        self.zero_revert.feed_all_positions(self.scanner._tracked_positions)
                        self._last_full_sync = time.time()
                else:
                    self._last_full_sync = time.time()
                    self.zero_revert.feed_all_positions(self.scanner._tracked_positions)
            except Exception:
                pass

    def _map_signal_to_exec_type(self, signal_type: SignalType) -> ExecutionType:
        mapping = {
            SignalType.LIQUIDATION: ExecutionType.LIQUIDATION,
            SignalType.ARBITRAGE: ExecutionType.ARBITRAGE,
            SignalType.CROSS_CHAIN_ARB: ExecutionType.CROSS_CHAIN,
            SignalType.BACKRUN: ExecutionType.BACKRUN,
            SignalType.ORACLE_UPDATE: ExecutionType.LIQUIDATION,
            SignalType.NEW_PROTOCOL: ExecutionType.CUSTOM,
            SignalType.VULNERABILITY: ExecutionType.LIQUIDATION,
        }
        return mapping.get(signal_type, ExecutionType.CUSTOM)

    def _compute_priority(self, signal: OpportunitySignal) -> int:
        """Compute execution priority 1-10 (10 = highest)."""
        base = 5
        # Urgency boost
        if signal.urgency_score > 80:
            base += 2
        elif signal.urgency_score > 50:
            base += 1
        # Profit boost
        if signal.net_profit_usd > 500:
            base += 2
        elif signal.net_profit_usd > 100:
            base += 1
        # Competition penalty
        if signal.competition_estimate > 0.7:
            base -= 1
        # Confidence boost
        if signal.confidence > 0.8:
            base += 1
        return max(1, min(10, base))

    # ──────────────────────────────────────────────
    # DASHBOARD
    # ──────────────────────────────────────────────

    def _print_compact_status(self):
        elapsed = (time.time() - self.start_time) / 3600 if self.start_time else 0
        phase = self.ledger.current_phase.value
        profit = self.ledger.cumulative_profit_usd
        txns = self.ledger.total_transactions
        rate = self.ledger.hourly_run_rate()
        cap = self.capital_multiplier.capital_base
        mult_state = self.capital_multiplier.state.value

        print(f"\n  📊 [{phase}] {elapsed:.2f}h | "
              f"P&L: ${profit:,.2f} | Rate: ${rate:,.2f}/hr | "
              f"TXs: {txns} | Cap: ${cap:,.2f} | Mult: {mult_state}")

    def print_full_dashboard(self):
        """Print comprehensive dashboard."""
        print("\n" + "=" * 70)
        print("  ⚡ TRIANGULATED PROFIT ENGINE — FULL DASHBOARD")
        print("=" * 70)

        # Ledger
        self.ledger.print_dashboard()

        # Heat Map
        self.heat_map.print_heat_map(10)

        # Capital Multiplier
        if self.capital_multiplier.state != MultiplierState.INACTIVE:
            self.capital_multiplier.print_dashboard()

        # Scanner stats
        print(f"\n  🔍 SCANNER STATS")
        print(f"  Opportunities Received:  {self.opportunities_received}")
        print(f"  Opportunities Executed:  {self.opportunities_executed}")
        print(f"  Opportunities Skipped:   {self.opportunities_skipped}")
        print(f"  Recon Phase Active:      {self.scanner._recon_phase_active}")
        print(f"  Gas Retry Queue:         {len(self.scanner._gas_retry_queue)}")
        print(f"  Feedback Combos Tracked: {len(self.scanner._execution_feedback)}")
        for vector, stats in self.scanner.vector_stats.items():
            if stats['found'] > 0:
                sr = stats.get('success_rate', 1.0)
                print(f"    {vector}: found={stats['found']} exec={stats['executed']} "
                      f"fail={stats.get('failures', 0)} rate={sr:.0%}")

        # Flash Loan Router
        fl_status = self.flash_loan_router.status()
        print(f"\n  ⚡ FLASH LOAN ROUTER")
        print(f"  Routes Computed: {fl_status['routes_computed']}")
        print(f"  Fees Saved:      ${fl_status['total_fees_saved_usd']:,.2f}")

        # Gas Status
        print(f"\n  ⛽ GAS STATUS")
        for cid, gs in self.gas_optimizer.chain_status().items():
            print(f"    Chain {cid}: {gs['base_fee_gwei']:.2f} gwei ({gs['congestion']})")

        # Zero-Revert Pipeline Status
        if self.zero_revert:
            zr = self.zero_revert.get_stats()
            print(f"\n  ⚡ ZERO-REVERT PIPELINE")
            print(f"  Positions Indexed:   {zr['positions']}")
            print(f"  Block Checks:        {zr['checks']}")
            print(f"  TXs Fired:           {zr['fired']}")
            print(f"  TXs Confirmed:       {zr['confirmed']}")
            print(f"  TXs Reverted:        {zr['reverted']}")
            print(f"  Total Profit:        ${zr['profit_usd']:,.2f}")
            print(f"  Gas Spent:           ${zr['gas_spent_usd']:,.4f}")
            print(f"  Oracle Feeds:        {zr['oracle_feeds']}")

        # Projections
        if self.capital_multiplier.state != MultiplierState.INACTIVE:
            print(f"\n  📈 12-HOUR PROJECTION")
            projections = self.capital_multiplier.project_growth(12)
            for p in projections:
                print(f"    Hr {p['hour']:>2}: Capital ${p['capital_base']:>14,.2f} | "
                      f"Hourly ${p['hourly_profit']:>12,.2f} | "
                      f"Avg/TX ${p['avg_profit_per_trade']:>8,.2f}")

        print("=" * 70)


def build_default_config() -> Dict[str, Any]:
    """Build default configuration from environment variables."""
    return {
        'ledger': {
            'phase2_threshold': float(os.getenv('PHASE2_THRESHOLD', '2500')),
            'phase3_threshold': float(os.getenv('PHASE3_THRESHOLD', '45000')),
        },
        'scanner': {
            'scan_interval': float(os.getenv('SCAN_INTERVAL', '2')),
            'min_profit_usd': float(os.getenv('MIN_PROFIT_USD', '1')),
            'min_confidence': float(os.getenv('MIN_CONFIDENCE', '0.2')),
        },
        'gas': {
            'min_margin_percent': float(os.getenv('MIN_MARGIN_PCT', '0.03')),
            'min_margin_usd': float(os.getenv('MIN_MARGIN_USD', '0.50')),
        },
        'multiplier': {
            'reinvest_rate': float(os.getenv('REINVEST_RATE', '0.60')),
            'avg_return': float(os.getenv('AVG_RETURN', '0.0035')),
            'trades_per_hour': int(os.getenv('TRADES_PER_HOUR', '45')),
        },
        'execution': {
            'liquidation': {
                'min_profit_usd': float(os.getenv('MIN_PROFIT_USD', '1')),
            },
            'arbitrage': {},
            'backrun': {},
            'cross_chain': {},
        },
    }
