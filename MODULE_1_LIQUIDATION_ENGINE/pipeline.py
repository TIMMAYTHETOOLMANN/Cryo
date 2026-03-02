#!/usr/bin/env python3
"""
MODULE 1 — Enhanced Master Pipeline (Script 4 Upgraded)
=========================================================
Fully wired with Scripts 1-3 + Module 9 Omni-Scope Triangulation Engine
+ Module 11 JIT Liquidation Timing Optimizer.

$0 Initial Capital → Exponential Profit:
  Stage 0  Pre-Flight       │ Validate system (zero capital)
  Stage 1  Detection         │ ML-scored + NFT + mempool + OMNI-SCOPE signals
  Stage 2  Analysis          │ Multi-exit + surplus + cross-chain arb + RL tuning
  ────── GAS GATE ────────── │ GasManager + GasAbstraction (Gelato/Biconomy)
  Stage 3  Execution         │ Flash loan → liquidation → surplus utilization
  Stage 4  MEV Protection    │ Flashbots + mempool backrunning
  Stage 5  Cross-Chain       │ Dynamic exit chain + atomic cross-chain liquidations
  Stage 6  Profit Collection │ Treasury monitoring
  Stage 7  Analytics         │ A/B testing + RL parameter tuning + anomaly detection
  Module 9 Omni-Scope        │ 5 detector arrays + ML ranker (predictive intel)
  Module 11 JIT Engine       │ Oracle-triggered, simulate-before-send, JIT execution
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional

from web3 import Web3

from .config.settings import ConfigManager, get_config
from .stage_0_preflight.system_validator import SystemValidator, PreFlightReport
from .stage_1_detection.opportunity_detector import (
    OpportunityDetector, LiquidatablePosition, ScanPriority,
)
from .stage_1_detection.nft_detector import NFTLiquidationDetector
from .stage_1_detection.mempool_monitor import MempoolMonitor
from .stage_1_detection.dex_arb_scanner import DexArbScanner, ArbOpportunity
from .stage_1_detection.omni_channel_bridge import OmniChannelBridge, OmniSignal
from .stage_2_analysis.profitability_calculator import (
    ProfitabilityCalculator, get_calculator, ProfitabilityResult,
)
from .stage_2_analysis.risk_manager import RiskManager, RiskAssessment
from .stage_3_execution.gas_manager import GasManager, GasReport
from .stage_3_execution.gas_abstraction import GasAbstractionLayer, GasPaymentMethod
from .stage_3_execution.surplus_strategies import SurplusStrategyEngine
from .stage_3_execution.liquidation_executor import (
    LiquidationExecutor,
    LiquidationRequest,
    LiquidationResult,
)
from .stage_5_cross_chain.orchestrator import CrossChainOrchestrator
from .stage_6_profit_collection.treasury_manager import TreasuryManager
from .stage_7_analytics.analytics_engine import AnalyticsEngine, ExecutionRecord
from .stage_7_analytics.rl_tuner import RLParameterTuner

# Module 9 — Omni-Scope Triangulation Engine
try:
    from MODULE_9_OMNI_SCOPE.engine import OmniScopeEngine
    from MODULE_9_OMNI_SCOPE.data_bus import OpportunitySignal as OmniSignal, SignalType, SignalSource
    OMNI_SCOPE_AVAILABLE = True
except ImportError:
    OmniScopeEngine = None  # type: ignore
    OMNI_SCOPE_AVAILABLE = False

# Module 11 — JIT Liquidation Timing Optimizer
try:
    from profit_engine.jit_liquidation_engine import JITLiquidationEngine
    JIT_ENGINE_AVAILABLE = True
except ImportError:
    JITLiquidationEngine = None  # type: ignore
    JIT_ENGINE_AVAILABLE = False

# Zero-Revert Execution Pipeline (oracle reactor + block watcher + mempool sniffer)
try:
    from profit_engine.zero_revert_pipeline import ZeroRevertPipeline
    ZRP_AVAILABLE = True
except ImportError:
    ZeroRevertPipeline = None  # type: ignore
    ZRP_AVAILABLE = False

logger = logging.getLogger(__name__)

# Chain IDs supported by the JIT engine for Web3 provider construction.
_JIT_CHAIN_IDS = [1, 42161, 10, 8453, 137, 43114, 56, 324]


class Pipeline:
    """
    Master execution pipeline — Script 4 upgraded.

    New in Script 4:
    - Module 9 Omni-Scope Triangulation Engine integration
    - 5 detector arrays feeding ranked signals into detection
    - ML-scored quality ranking across all opportunity types
    - Zero-capital gas bootstrapping from profits
    - Continuous ML learning from execution outcomes
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()

        # Core stages (Scripts 1+2)
        self.validator = SystemValidator(self.config)
        self.detector = OpportunityDetector(self.config)
        self.calculator = get_calculator()
        self.risk_manager = RiskManager(self.config)
        self.gas_manager = GasManager(self.config)
        self.treasury = TreasuryManager(self.config)
        self.analytics = AnalyticsEngine(self.config)

        # Script 3 additions
        self.nft_detector = NFTLiquidationDetector(self.config)
        self.mempool_monitor = MempoolMonitor(self.config)
        self.surplus_engine = SurplusStrategyEngine()
        self.gas_abstraction = GasAbstractionLayer(self.config)
        self.cross_chain = CrossChainOrchestrator(self.config)
        self.rl_tuner = RLParameterTuner(self.config)

        # DEX Arbitrage Scanner (additional profit vector)
        self.dex_arb = DexArbScanner(self.config)

        # Omni-Channel Bridge (cross-chain arb, backrun, contract discovery)
        self.omni_bridge = OmniChannelBridge(self.config)

        # Execution engine (real on-chain TX submission)
        self.executor: Optional[LiquidationExecutor] = None
        try:
            self.executor = LiquidationExecutor()
        except Exception as e:
            logger.warning(f"LiquidationExecutor init failed (stub mode): {e}")

        # Script 4: Module 9 Omni-Scope
        self.omni_scope: Optional["OmniScopeEngine"] = None
        if OMNI_SCOPE_AVAILABLE:
            self.omni_scope = OmniScopeEngine()

        # Module 11: JIT Liquidation Timing Optimizer
        self.jit_engine: Optional["JITLiquidationEngine"] = None
        if JIT_ENGINE_AVAILABLE:
            try:
                self.jit_engine = JITLiquidationEngine(self._build_w3_providers())
            except Exception as _jit_err:
                logger.warning(f"JITLiquidationEngine init failed (stub mode): {_jit_err}")

        # Zero-Revert Execution Pipeline (oracle + block + mempool sniffer)
        self.zero_revert_pipeline: Optional["ZeroRevertPipeline"] = None
        if ZRP_AVAILABLE:
            try:
                self.zero_revert_pipeline = ZeroRevertPipeline(self._build_w3_providers())
            except Exception as _zrp_err:
                logger.warning(f"ZeroRevertPipeline init failed (stub mode): {_zrp_err}")

        # State
        self._preflight: Optional[PreFlightReport] = None
        self._gas_report: Optional[GasReport] = None
        self._running = False
        self._mode = "UNKNOWN"
        self._mempool_task: Optional[asyncio.Task] = None

        # Statistics
        self.stats = {
            "start_time": 0.0,
            "scan_cycles": 0,
            "opportunities_found": 0,
            "nft_opportunities_found": 0,
            "omni_scope_signals_consumed": 0,
            "profitable_opportunities": 0,
            "executions_attempted": 0,
            "executions_succeeded": 0,
            "total_profit_usd": 0.0,
            "surplus_profit_usd": 0.0,
            "gas_token_savings_usd": 0.0,
            "fee_rebates_usd": 0.0,
            "cross_chain_arb_profit_usd": 0.0,
            "dex_arb_opportunities": 0,
            "dex_arb_profit_usd": 0.0,
            "omni_bridge_signals": 0,
            "gas_gate_blocks": 0,
            "gas_abstraction_used": 0,
            "preemptive_detections": 0,
            "cross_protocol_cascades": 0,
            "risk_rejections": 0,
            "twap_rejections": 0,
            "rl_episodes": 0,
            # Module 11: JIT execution stats
            "jit_positions_tracked": 0,
            "jit_simulations_run": 0,
            "jit_simulations_passed": 0,
            "jit_txs_broadcast": 0,
            "jit_txs_confirmed": 0,
            "jit_txs_reverted": 0,
            "jit_profit_usd": 0.0,
            # Zero-Revert Pipeline stats
            "zrp_positions": 0,
            "zrp_checks": 0,
            "zrp_fired": 0,
            "zrp_confirmed": 0,
            "zrp_reverted": 0,
            "zrp_profit_usd": 0.0,
            "zrp_mempool_txs_inspected": 0,
            "zrp_mempool_impacts_detected": 0,
            "zrp_mempool_bundles_prepared": 0,
        }

    # ------------------------------------------------------------------
    # Module 11 helpers
    # ------------------------------------------------------------------

    def _build_w3_providers(self) -> Dict[int, Web3]:
        """Build Web3 instances from configured RPC URLs for all known chains."""
        providers: Dict[int, Web3] = {}
        for chain_id in _JIT_CHAIN_IDS:
            chain_cfg = self.config.get_chain(chain_id)
            if chain_cfg and chain_cfg.rpc_url:
                try:
                    providers[chain_id] = Web3(
                        Web3.HTTPProvider(chain_cfg.rpc_url, request_kwargs={"timeout": 10})
                    )
                except Exception as exc:
                    logger.debug("Failed to create Web3 provider for chain %d: %s", chain_id, exc)
        return providers

    def _sync_jit_watchlist(self, positions: List[LiquidatablePosition]) -> None:
        """Feed detected positions into the JIT engine watchlist.

        Note: ``debt_usd`` is estimated as ``debt_amount_wei / 1e18 * 2500`` — a
        placeholder using a rough ETH price.  The JIT engine uses this value only
        for initial profit-proximity scoring; it re-fetches actual prices via
        on-chain oracles during active monitoring.
        """
        if not self.jit_engine:
            return
        watchlist = {
            pos.user: {
                "chain_id": pos.chain_id,
                "last_hf": pos.health_factor,
                "debt_usd": pos.debt_amount / 1e18 * 2500,  # rough ETH price estimate
                "collateral_asset": pos.collateral_asset,
                "debt_asset": pos.debt_asset,
                "pool": getattr(pos, "pool_address", ""),
            }
            for pos in positions
        }
        self.jit_engine.feed_watchlist(watchlist)

    def _sync_zrp_watchlist(self, positions: List[LiquidatablePosition]) -> None:
        """Feed detected positions into the Zero-Revert Pipeline's position index."""
        if not self.zero_revert_pipeline:
            return
        watchlist = {
            pos.user: {
                "chain_id": pos.chain_id,
                "last_hf": pos.health_factor,
                "debt_usd": pos.debt_amount / 1e18 * 2500,  # rough ETH price estimate
                "collateral_asset": pos.collateral_asset,
                "debt_asset": pos.debt_asset,
                "pool": getattr(pos, "pool_address", ""),
            }
            for pos in positions
        }
        self.zero_revert_pipeline.feed_watchlist(watchlist)

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    async def run(self):
        """Main entry point."""
        self.stats["start_time"] = time.time()
        self._running = True

        logger.info("\n" + "=" * 80)
        logger.info("  MODULE 1 — ENHANCED FLASH LOAN LIQUIDATION ENGINE")
        logger.info("  Zero-Capital Profit Pipeline (Script 4 — Omni-Scope)")
        logger.info("=" * 80)

        # ── STAGE 0: Pre-Flight ──
        logger.info("\n▸ STAGE 0: Pre-Flight Validation…")
        self._preflight = await self.validator.run()
        SystemValidator.print_report(self._preflight)

        if self._preflight.verdict == "NO-GO":
            logger.error("❌ Pre-flight FAILED — cannot proceed")
            return

        # ── Initialize detector + NFT detector + DEX arb scanner ──
        logger.info("\n▸ STAGE 1: Initializing Enhanced Detector + NFT + DEX Arb Scanner…")
        await self.detector.initialize()
        await self.dex_arb.initialize()
        await self.omni_bridge.initialize()

        # ── Start mempool monitor (background) ──
        logger.info("▸ STAGE 1: Starting Mempool Monitor (background)…")
        self.mempool_monitor.on_event(self._on_mempool_event)
        self._mempool_task = asyncio.create_task(self.mempool_monitor.start())

        # ── GAS CHECK (enhanced with gas abstraction) ──
        logger.info("\n▸ GAS GATE: Checking execution readiness (+ gas abstraction)…")
        self._gas_report = self.gas_manager.full_report()
        GasManager.print_report(self._gas_report)

        abs_report = self.gas_abstraction.full_report()
        GasAbstractionLayer.print_report(abs_report)

        # Determine execution mode
        native_chains = set(self._gas_report.affordable_chains)
        abstract_chains = {
            cid for cid, s in abs_report.items()
            if s.can_use_gelato or s.can_use_biconomy or s.can_self_bridge
        }
        all_executable = native_chains | abstract_chains

        if self._preflight.can_execute and all_executable:
            self._mode = "FULL"
            logger.info(
                f"\n🟢 MODE: FULL EXECUTION — "
                f"{len(native_chains)} native + {len(abstract_chains - native_chains)} abstracted chain(s)"
            )
        else:
            self._mode = "SCAN-ONLY"
            logger.info("\n🟡 MODE: SCAN-ONLY — deposit gas or configure Gelato to unlock execution")

        # ── STAGE 6: Initial Treasury Snapshot ──
        logger.info("\n▸ STAGE 6: Initial Treasury Snapshot…")
        self.treasury.print_snapshot()

        # ── MODULE 9: Omni-Scope Triangulation Engine ──
        if self.omni_scope:
            logger.info("\n▸ MODULE 9: Starting Omni-Scope Triangulation Engine…")
            await self.omni_scope.start()
            logger.info("  ✅ 5 detector arrays active + ML ranker subscribed")
        else:
            logger.info("\n▸ MODULE 9: Omni-Scope not available (install MODULE_9_OMNI_SCOPE)")

        # ── MODULE 11: JIT Liquidation Timing Optimizer ──
        if self.jit_engine:
            logger.info("\n▸ MODULE 11: Starting JIT Liquidation Timing Optimizer…")
            await self.jit_engine.start()
            logger.info("  ✅ Oracle watcher + simulate-before-send + JIT executor active")
        else:
            logger.info("\n▸ MODULE 11: JIT engine not available")

        # ── ZERO-REVERT PIPELINE: Oracle + Block + Mempool Sniffer ──
        if self.zero_revert_pipeline:
            logger.info("\n▸ ZERO-REVERT PIPELINE: Starting oracle reactor + mempool sniffer…")
            await self.zero_revert_pipeline.start()
            logger.info("  ✅ Oracle reactor + block watcher + mempool backrun sniffer active")
        else:
            logger.info("\n▸ ZERO-REVERT PIPELINE: not available")

        # ── MAIN LOOP ──
        logger.info("\n" + "─" * 80)
        logger.info("  ENTERING MAIN LOOP (Script 4 Enhanced Pipeline)")
        logger.info("─" * 80)

        while self._running:
            try:
                await self._cycle()
                await asyncio.sleep(self.config.execution.scan_interval_seconds)
            except KeyboardInterrupt:
                logger.info("\n⏹️  Pipeline stopped by operator")
                break
            except Exception as e:
                logger.error(f"Pipeline cycle error: {e}")
                await asyncio.sleep(5)

        # ── SHUTDOWN ──
        await self._shutdown()

    async def stop(self):
        self._running = False
        await self.mempool_monitor.stop()

    # ------------------------------------------------------------------
    # Mempool event handler
    # ------------------------------------------------------------------

    def _on_mempool_event(self, event):
        """Handle a detected mempool event (potential backrun opportunity)."""
        logger.debug(
            f"🔍 Mempool: {event.event_type.value} — "
            f"${event.value_usd:,.0f} impact={event.estimated_price_impact_pct:.2f}%"
        )

    # ------------------------------------------------------------------
    # Main cycle (fully wired — all 9 enhancements)
    # ------------------------------------------------------------------

    async def _cycle(self):
        """One iteration of the enhanced pipeline."""
        self.stats["scan_cycles"] += 1
        cycle = self.stats["scan_cycles"]

        # Heartbeat log every cycle
        if cycle % 6 == 1 or cycle <= 3:
            uptime = (time.time() - self.stats["start_time"]) / 60
            tracked = len(self.detector.tracked_users) if hasattr(self.detector, 'tracked_users') else '?'
            logger.info(
                f"💓 Cycle {cycle} | {uptime:.1f}min | users={tracked} | "
                f"found={self.stats['opportunities_found']} "
                f"arbs={self.stats['dex_arb_opportunities']} "
                f"omni={self.stats['omni_bridge_signals']} "
                f"profitable={self.stats['profitable_opportunities']} "
                f"executed={self.stats['executions_succeeded']}/{self.stats['executions_attempted']} "
                f"profit=${self.stats['total_profit_usd']:.2f}"
            )

        # ── STAGE 1: Detection (ML-scored + NFT + mempool) ──
        opportunities = await self._stage_1_detect()

        if not opportunities:
            return

        self.stats["opportunities_found"] += len(opportunities)

        # ── MODULE 11: Feed watchlist into JIT Timing Optimizer ──
        self._sync_jit_watchlist(opportunities)

        # ── ZERO-REVERT PIPELINE: Feed position index for oracle/mempool tracking ──
        self._sync_zrp_watchlist(opportunities)

        # ── STAGE 2: Analysis (multi-exit + surplus + RL-tuned) ──
        profitable = await self._stage_2_analyse(opportunities)

        if not profitable:
            return

        self.stats["profitable_opportunities"] += len(profitable)

        # ── GAS GATE (enhanced: native + abstraction) ──
        if self._mode != "FULL":
            self._gas_report = self.gas_manager.full_report()
            abs_report = self.gas_abstraction.full_report()
            native = set(self._gas_report.affordable_chains)
            abstract = {
                cid for cid, s in abs_report.items()
                if s.can_use_gelato or s.can_use_biconomy or s.can_self_bridge
            }
            if native or abstract:
                self._mode = "FULL"
                logger.info("🟢 Gas detected — switching to FULL EXECUTION mode")
            else:
                self.stats["gas_gate_blocks"] += 1
                if self.stats["gas_gate_blocks"] % 20 == 1:
                    logger.warning(
                        f"⛽ GAS GATE: {len(profitable)} profitable opps — BLOCKED"
                    )
                return

        # ── STAGE 3+4+5: Execution + MEV + Cross-Chain ──
        for opp_pos, opp_profit, opp_risk in profitable:
            chain_id = opp_pos.chain_id

            # Enhanced gas check: try native first, then abstraction
            can_exec, reason = self.gas_manager.can_execute(chain_id)
            gas_method = GasPaymentMethod.NATIVE
            if not can_exec:
                can_abstract, gas_method = self.gas_abstraction.can_pay_gas(chain_id)
                if can_abstract:
                    self.stats["gas_abstraction_used"] += 1
                    logger.info(f"⛽ Using {gas_method.value} for chain {chain_id}")
                else:
                    logger.warning(f"⛽ Skipping chain {chain_id}: {reason}")
                    continue

            self.stats["executions_attempted"] += 1
            exec_start = time.time()

            result = await self._stage_3_execute(opp_pos, opp_profit, opp_risk, gas_method)
            exec_latency = (time.time() - exec_start) * 1000

            # ── STAGE 7: Analytics + RL recording ──
            profit = result.get("profit_usd", 0)
            self.analytics.record(ExecutionRecord(
                timestamp=time.time(),
                chain_id=chain_id,
                protocol=opp_pos.protocol,
                collateral_asset=opp_pos.collateral_asset,
                debt_amount_usd=opp_pos.debt_amount / 1e18 * 2500,
                profit_usd=profit,
                gas_cost_usd=opp_profit.gas_cost_usd,
                flash_loan_fee_usd=opp_profit.flash_loan_fee_usd,
                flash_loan_provider=opp_profit.best_provider,
                exit_strategy=opp_profit.best_exit_strategy.value,
                success=result.get("success", False),
                latency_ms=exec_latency,
            ))

            # RL tuning
            self.rl_tuner.observe_and_act(
                chain_id=chain_id,
                gas_gwei=opp_risk.gas_price_gwei,
                volatility=opp_pos.volatility_index,
                recent_success_rate=self._recent_success_rate(),
                reward=profit if result.get("success") else -opp_profit.gas_cost_usd,
            )
            self.stats["rl_episodes"] += 1

            if result.get("success"):
                self.stats["executions_succeeded"] += 1
                self.stats["total_profit_usd"] += profit
                self.stats["surplus_profit_usd"] += opp_profit.surplus_profit_usd
                self.stats["gas_token_savings_usd"] += opp_profit.gas_token_savings_usd
                self.stats["fee_rebates_usd"] += opp_profit.fee_rebate_usd
                self.stats["cross_chain_arb_profit_usd"] += opp_profit.cross_chain_advantage_usd
                self.risk_manager.record_success(profit)
                self.detector.record_liquidation(opp_pos.collateral_asset)
                logger.info(
                    f"🎉 PROFIT: ${profit:.2f} on {opp_pos.protocol} "
                    f"chain {chain_id} (exit: {opp_profit.best_exit_strategy.value}, "
                    f"surplus: +${opp_profit.surplus_profit_usd:.2f}, "
                    f"gas savings: +${opp_profit.gas_token_savings_usd:.2f})"
                )
            else:
                self.risk_manager.record_failure(opp_pos.user)

        # Periodic analytics
        if self.stats["scan_cycles"] % 100 == 0:
            self.analytics.print_summary()

    # ------------------------------------------------------------------
    # Stage 1: Detection (ML + NFT + Mempool)
    # ------------------------------------------------------------------

    async def _stage_1_detect(self) -> List[LiquidatablePosition]:
        """Stage 1: ML-scored detection + NFT scanning + Omni-Scope signals."""
        positions = await self.detector.scan_once()

        # NFT liquidation scanning
        nft_opps = await self.nft_detector.scan()
        self.stats["nft_opportunities_found"] += len(nft_opps)

        # DEX Arbitrage scanning (cross-fee-tier + triangular)
        arb_opps = await self.dex_arb.scan_once()
        self.stats["dex_arb_opportunities"] += len(arb_opps)

        # Convert DEX arb opportunities to LiquidatablePosition format
        # so they pass through the same pipeline (gas gate, execution)
        for arb in arb_opps:
            positions.append(LiquidatablePosition(
                chain_id=arb.chain_id,
                protocol="dex_arb",
                user="0x" + "0" * 40,  # no specific user — arb target
                debt_asset=arb.path[0],  # flash loan asset
                collateral_asset=arb.path[1] if len(arb.path) > 1 else arb.path[0],
                debt_amount=arb.input_amount,
                collateral_amount=arb.expected_output,
                health_factor=0.0,  # always actionable
                liquidation_bonus=0.0,
                estimated_profit_usd=arb.net_profit_usd,
                max_gas_price=int(self.config.execution.gas_price_cap_gwei * 1e9),
                timestamp=arb.timestamp,
                block_number=arb.block_number,
                liquidation_probability=0.95,  # high confidence
                priority=ScanPriority.HIGH,
            ))

        # Omni-Channel Bridge signals (cross-chain arb, bridge discrepancies)
        omni_signals = await self.omni_bridge.scan_once()
        self.stats["omni_bridge_signals"] += len(omni_signals)

        for sig in omni_signals:
            positions.append(LiquidatablePosition(
                chain_id=sig.chain_id,
                protocol=f"omni_{sig.source}",
                user="0x" + "0" * 40,
                debt_asset="auto",
                collateral_asset="auto",
                debt_amount=int(sig.metadata.get("input_amount", 0)),
                collateral_amount=int(sig.metadata.get("output_amount", 0)),
                health_factor=0.0,
                liquidation_bonus=0.0,
                estimated_profit_usd=sig.net_profit_usd,
                max_gas_price=int(self.config.execution.gas_price_cap_gwei * 1e9),
                timestamp=sig.timestamp,
                liquidation_probability=sig.confidence,
                priority=ScanPriority.HIGH if sig.confidence > 0.7 else ScanPriority.MEDIUM,
            ))

        # Convert NFT opportunities to LiquidatablePosition format
        for nft in nft_opps:
            positions.append(LiquidatablePosition(
                chain_id=nft.chain_id,
                protocol=f"nft_{nft.protocol}",
                user=nft.borrower,
                debt_asset=nft.debt_asset,
                collateral_asset=nft.nft_contract,
                debt_amount=int(nft.debt_amount_usd * 1e18 / 2500),
                collateral_amount=int(nft.floor_price_usd * 1e18 / 2500),
                health_factor=nft.health_factor,
                liquidation_bonus=nft.liquidation_bonus,
                estimated_profit_usd=nft.estimated_profit_usd,
                max_gas_price=int(self.config.execution.gas_price_cap_gwei * 1e9),
                timestamp=nft.timestamp,
            ))

        # Module 9: Consume ranked signals from Omni-Scope
        if self.omni_scope and OMNI_SCOPE_AVAILABLE:
            omni_signals = self.omni_scope.consume(limit=20)
            omni_converted = 0
            for sig in omni_signals:
                # Only convert liquidation-type signals to positions
                if sig.signal_type.value in (
                    "pending_liquidation", "nft_liquidation"
                ):
                    positions.append(LiquidatablePosition(
                        chain_id=sig.chain_id,
                        protocol=sig.target_protocol or f"omni_{sig.source.value}",
                        user=sig.target_user or "0x" + "0" * 40,
                        debt_asset=sig.target_asset or "unknown",
                        collateral_asset=sig.target_asset or "unknown",
                        debt_amount=int(sig.debt_amount_usd * 1e18 / 2500) if sig.debt_amount_usd else 0,
                        collateral_amount=int(sig.collateral_amount_usd * 1e18 / 2500) if sig.collateral_amount_usd else 0,
                        health_factor=sig.health_factor if sig.health_factor else 1.0,
                        liquidation_bonus=sig.liquidation_bonus if sig.liquidation_bonus else 0.05,
                        estimated_profit_usd=sig.estimated_profit_usd,
                        max_gas_price=int(self.config.execution.gas_price_cap_gwei * 1e9),
                    ))
                    omni_converted += 1

            self.stats["omni_scope_signals_consumed"] += len(omni_signals)
            if omni_signals:
                logger.info(
                    f"🕸️ Omni-Scope: {len(omni_signals)} signals consumed, "
                    f"{omni_converted} converted to positions"
                )

        if positions:
            critical = sum(1 for p in positions if p.priority == ScanPriority.CRITICAL)
            high = sum(1 for p in positions if p.priority == ScanPriority.HIGH)
            nft_count = len(nft_opps)
            preemptive = sum(1 for p in positions if p.health_factor > self.detector.hf_threshold)
            self.stats["preemptive_detections"] += preemptive

            logger.info(
                f"📡 Stage 1: {len(positions)} opportunities "
                f"({critical} critical, {high} high, {nft_count} NFT, {preemptive} preemptive)"
            )

        return positions

    # ------------------------------------------------------------------
    # Stage 2: Analysis (multi-exit + surplus + RL params)
    # ------------------------------------------------------------------

    async def _stage_2_analyse(
        self, positions: List[LiquidatablePosition]
    ) -> List[tuple]:
        """Stage 2: Multi-exit + surplus + RL-tuned profitability."""
        approved: List[tuple] = []

        for pos in positions:
            # Quick reject: if Stage 1 already estimates negative profit, skip
            if hasattr(pos, 'estimated_profit_usd') and pos.estimated_profit_usd < 0:
                logger.debug(f"⏭️ Skip {pos.user[:12]}…: negative estimated profit ${pos.estimated_profit_usd:.2f}")
                continue

            # Get RL-tuned parameters for this chain
            rl_params = self.rl_tuner.get_params(pos.chain_id)

            debt_usd = pos.debt_amount / 1e18 * 2500
            coll_usd = pos.collateral_amount / 1e18 * 2500

            # Skip if below RL-tuned min_debt
            if debt_usd < rl_params.min_debt_usd:
                continue

            prof = self.calculator.calculate(
                debt_amount_usd=debt_usd,
                collateral_amount_usd=coll_usd,
                liquidation_bonus=pos.liquidation_bonus,
                flash_loan_provider="auto",
                chain_id=pos.chain_id,
                volatility_class="volatile" if pos.volatility_index > 0.05 else "default",
            )

            # Use RL-tuned min_profit threshold
            if prof.net_profit_usd < rl_params.min_profit_usd:
                continue

            # Surplus analysis
            surplus = self.surplus_engine.analyze(
                borrowed_usd=debt_usd,
                debt_to_repay_usd=debt_usd,
                flash_loan_fee_usd=prof.flash_loan_fee_usd,
                collateral_seized_usd=coll_usd * (1 + pos.liquidation_bonus),
                chain_id=pos.chain_id,
            )
            if surplus.estimated_extra_profit_usd > 0:
                logger.debug(
                    f"💰 Surplus: +${surplus.estimated_extra_profit_usd:.2f} "
                    f"via {surplus.best_action.value}"
                )

            # Cross-chain exit check
            exit_route = self.cross_chain.find_best_exit_chain(
                pos.chain_id, pos.collateral_asset,
                coll_usd * pos.liquidation_bonus,
            )
            if exit_route:
                self.calculator.update_cross_chain_price(
                    exit_route.source_chain,
                    exit_route.exit_chain,
                    exit_route.net_advantage_usd,
                )

            # Risk assessment
            w3 = self.detector.w3_providers.get(pos.chain_id)
            risk = self.risk_manager.assess(
                chain_id=pos.chain_id,
                debt_amount_usd=debt_usd,
                collateral_amount_usd=coll_usd,
                liquidation_bonus=pos.liquidation_bonus,
                flash_loan_provider=prof.best_provider,
                w3=w3,
                user=pos.user,
                collateral_asset=pos.collateral_asset,
                volatility_index=pos.volatility_index,
            )

            if not risk.approved:
                self.stats["risk_rejections"] += 1
                if risk.twap_check and not risk.twap_check.passed:
                    self.stats["twap_rejections"] += 1
                continue

            approved.append((pos, prof, risk))

        if approved:
            logger.info(
                f"✅ Stage 2: {len(approved)}/{len(positions)} approved "
                f"(exit: {approved[0][1].best_exit_strategy.value}, "
                f"provider: {approved[0][1].best_provider})"
            )

        return approved

    # ------------------------------------------------------------------
    # Stage 3: Execution (gas-gated + surplus + gas abstraction)
    # ------------------------------------------------------------------

    async def _stage_3_execute(
        self,
        pos: LiquidatablePosition,
        prof: ProfitabilityResult,
        risk: RiskAssessment,
        gas_method: GasPaymentMethod = GasPaymentMethod.NATIVE,
    ) -> Dict:
        """Stage 3+4+5: Flash loan → liquidation → surplus → MEV."""
        logger.info(
            f"🔧 Executing: {pos.user[:12]}… on {pos.protocol} "
            f"chain {pos.chain_id} | provider={prof.best_provider} "
            f"exit={prof.best_exit_strategy.value} "
            f"gas={gas_method.value} "
            f"slippage={risk.suggested_slippage:.1%} "
            f"surplus=+${prof.surplus_profit_usd:.2f}"
        )

        # Guard: executor must be available (not stub)
        if self.executor is None or not hasattr(self.executor, 'execute'):
            logger.warning("⚠️ LiquidationExecutor not available — scan-only mode")
            return {"success": False, "profit_usd": 0}

        # Map protocol string → LiquidationProtocol enum
        from .stage_3_execution.liquidation_executor import LiquidationProtocol
        protocol_map = {
            "aave_v3": LiquidationProtocol.AAVE_V3,
            "aave_v2": LiquidationProtocol.AAVE_V2,
            "compound_v2": LiquidationProtocol.COMPOUND_V2,
            "compound_v3": LiquidationProtocol.COMPOUND_V3,
            "maker_dao": LiquidationProtocol.MAKER_DAO,
            "maker": LiquidationProtocol.MAKER_DAO,
        }
        liq_protocol = protocol_map.get(
            pos.protocol.lower().replace("-", "_").replace(" ", "_"),
            LiquidationProtocol.AAVE_V3,
        )

        # Build the on-chain liquidation request
        request = LiquidationRequest(
            chain_id=pos.chain_id,
            protocol=liq_protocol,
            user=pos.user,
            debt_asset=pos.debt_asset,
            debt_amount=pos.debt_amount,
            collateral_asset=pos.collateral_asset,
            min_collateral_amount=0,
            max_gas_price=pos.max_gas_price,
            use_flashbots=(pos.chain_id == 1),  # Flashbots on mainnet only
        )

        try:
            result: LiquidationResult = await self.executor.execute(request)

            if result.success:
                logger.info(
                    f"🎉 ON-CHAIN TX CONFIRMED: {result.tx_hash} "
                    f"block={result.block_number} "
                    f"profit=${result.profit_usd:.2f} "
                    f"gas={result.gas_used} (${result.gas_cost_usd:.2f})"
                )

                # Feed outcome to Omni-Scope ML for continuous learning
                if self.omni_scope:
                    self.omni_scope.record_execution_outcome(
                        signal=None,  # Not from omni-scope
                        profit=result.profit_usd,
                        success=True,
                    ) if hasattr(self.omni_scope, 'record_execution_outcome') else None

                return {
                    "success": True,
                    "profit_usd": result.profit_usd,
                    "tx_hash": result.tx_hash,
                    "block_number": result.block_number,
                    "gas_used": result.gas_used,
                    "gas_cost_usd": result.gas_cost_usd,
                    "debt_covered": result.debt_covered,
                    "collateral_seized": result.collateral_seized,
                }
            else:
                logger.warning(
                    f"❌ Execution failed: {result.error_message}"
                )
                return {
                    "success": False,
                    "profit_usd": 0,
                    "error": result.error_message,
                }

        except Exception as e:
            logger.error(f"❌ Execution exception: {e}")
            return {"success": False, "profit_usd": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _recent_success_rate(self) -> float:
        total = self.stats["executions_attempted"]
        if total == 0:
            return 1.0
        return self.stats["executions_succeeded"] / total

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _shutdown(self):
        if self._mempool_task:
            await self.mempool_monitor.stop()
            self._mempool_task.cancel()

        # Stop Omni-Scope
        if self.omni_scope:
            await self.omni_scope.stop()

        # Stop Module 11 JIT engine
        if self.jit_engine:
            await self.jit_engine.stop()
            jit_stats = self.jit_engine.get_stats()
            self.stats["jit_positions_tracked"] = jit_stats.get("jit_positions", 0)
            self.stats["jit_simulations_run"] = jit_stats.get("simulations_run", 0)
            self.stats["jit_simulations_passed"] = jit_stats.get("simulations_passed", 0)
            self.stats["jit_txs_broadcast"] = jit_stats.get("txs_broadcast", 0)
            self.stats["jit_txs_confirmed"] = jit_stats.get("txs_confirmed", 0)
            self.stats["jit_txs_reverted"] = jit_stats.get("txs_reverted", 0)
            self.stats["jit_profit_usd"] = jit_stats.get("total_profit_usd", 0.0)

        # Stop Zero-Revert Pipeline
        if self.zero_revert_pipeline:
            await self.zero_revert_pipeline.stop()
            zrp_stats = self.zero_revert_pipeline.get_stats()
            self.stats["zrp_positions"] = zrp_stats.get("positions", 0)
            self.stats["zrp_checks"] = zrp_stats.get("checks", 0)
            self.stats["zrp_fired"] = zrp_stats.get("fired", 0)
            self.stats["zrp_confirmed"] = zrp_stats.get("confirmed", 0)
            self.stats["zrp_reverted"] = zrp_stats.get("reverted", 0)
            self.stats["zrp_profit_usd"] = zrp_stats.get("profit_usd", 0.0)
            self.stats["zrp_mempool_txs_inspected"] = zrp_stats.get("mempool_txs_inspected", 0)
            self.stats["zrp_mempool_impacts_detected"] = zrp_stats.get("mempool_impacts_detected", 0)
            self.stats["zrp_mempool_bundles_prepared"] = zrp_stats.get("mempool_bundles_prepared", 0)

        uptime = time.time() - self.stats["start_time"]
        logger.info("\n" + "=" * 80)
        logger.info("  PIPELINE SHUTDOWN — FINAL REPORT (Script 4 + Omni-Scope + JIT + ZRP)")
        logger.info("=" * 80)
        logger.info(f"  Uptime:                  {uptime/3600:.2f} hours")
        logger.info(f"  Mode:                    {self._mode}")
        logger.info(f"  Scan cycles:             {self.stats['scan_cycles']}")
        logger.info(f"  Opportunities found:     {self.stats['opportunities_found']}")
        logger.info(f"    ↳ NFT opportunities:   {self.stats['nft_opportunities_found']}")
        logger.info(f"    ↳ DEX arb opps:        {self.stats['dex_arb_opportunities']}")
        logger.info(f"    ↳ Omni bridge signals: {self.stats['omni_bridge_signals']}")
        logger.info(f"    ↳ Omni-Scope signals:  {self.stats['omni_scope_signals_consumed']}")
        logger.info(f"    ↳ Preemptive:          {self.stats['preemptive_detections']}")
        logger.info(f"  Profitable:              {self.stats['profitable_opportunities']}")
        logger.info(f"    ↳ Risk rejected:       {self.stats['risk_rejections']}")
        logger.info(f"    ↳ TWAP rejected:       {self.stats['twap_rejections']}")
        logger.info(f"  Executions attempted:    {self.stats['executions_attempted']}")
        logger.info(f"  Executions succeeded:    {self.stats['executions_succeeded']}")
        logger.info(f"  ─── Profit Breakdown ───")
        logger.info(f"  Total profit:            ${self.stats['total_profit_usd']:.2f}")
        logger.info(f"    ↳ Surplus utilization: +${self.stats['surplus_profit_usd']:.2f}")
        logger.info(f"    ↳ Gas token savings:   +${self.stats['gas_token_savings_usd']:.2f}")
        logger.info(f"    ↳ Fee rebates:         +${self.stats['fee_rebates_usd']:.2f}")
        logger.info(f"    ↳ Cross-chain arb:     +${self.stats['cross_chain_arb_profit_usd']:.2f}")
        logger.info(f"  Gas gate blocks:         {self.stats['gas_gate_blocks']}")
        logger.info(f"  Gas abstraction used:    {self.stats['gas_abstraction_used']}")
        logger.info(f"  RL tuning episodes:      {self.stats['rl_episodes']}")

        # RL diagnostics
        rl_diag = self.rl_tuner.get_diagnostics()
        logger.info(f"\n  ─── RL Tuner ───")
        logger.info(f"  Q-table: {rl_diag['q_table_states']} states, {rl_diag['q_table_entries']} entries")
        for cid, params in rl_diag.get("chain_params", {}).items():
            logger.info(f"    Chain {cid}: {params}")

        # Cross-chain stats
        xc = self.cross_chain.get_stats()
        logger.info(f"\n  ─── Cross-Chain ───")
        logger.info(f"  Arbs found:              {xc['cross_chain_arbs_found']}")
        logger.info(f"  Bridge advantage:        ${xc['total_bridge_advantage_usd']:.2f}")

        # Module 9 Omni-Scope stats
        if self.omni_scope:
            logger.info(f"\n  ─── Module 9: Omni-Scope ───")
            self.omni_scope.print_status()

        # Module 11 JIT stats
        if self.jit_engine:
            logger.info(f"\n  ─── Module 11: JIT Timing Optimizer ───")
            logger.info(f"  JIT positions tracked:   {self.stats['jit_positions_tracked']}")
            logger.info(f"  Simulations run:         {self.stats['jit_simulations_run']}")
            logger.info(f"  Simulations passed:      {self.stats['jit_simulations_passed']}")
            logger.info(f"  TXs broadcast:           {self.stats['jit_txs_broadcast']}")
            logger.info(f"  TXs confirmed:           {self.stats['jit_txs_confirmed']}")
            logger.info(f"  TXs reverted:            {self.stats['jit_txs_reverted']}")
            logger.info(f"  JIT profit:              ${self.stats['jit_profit_usd']:.2f}")

        # Zero-Revert Pipeline stats
        if self.zero_revert_pipeline:
            logger.info(f"\n  ─── Zero-Revert Pipeline ───")
            logger.info(f"  ZRP positions indexed:   {self.stats['zrp_positions']}")
            logger.info(f"  On-chain checks:         {self.stats['zrp_checks']}")
            logger.info(f"  TXs fired:               {self.stats['zrp_fired']}")
            logger.info(f"  TXs confirmed:           {self.stats['zrp_confirmed']}")
            logger.info(f"  TXs reverted:            {self.stats['zrp_reverted']}")
            logger.info(f"  ZRP profit:              ${self.stats['zrp_profit_usd']:.2f}")
            logger.info(f"  Mempool txs inspected:   {self.stats['zrp_mempool_txs_inspected']}")
            logger.info(f"  Price impacts detected:  {self.stats['zrp_mempool_impacts_detected']}")
            logger.info(f"  Backrun bundles queued:  {self.stats['zrp_mempool_bundles_prepared']}")

        # Analytics summary
        logger.info("\n  ─── Analytics ───")
        self.analytics.print_summary()

        # Treasury
        logger.info("\n  ─── Treasury ───")
        self.treasury.print_snapshot()

        # Tuning recommendations
        recs = self.analytics.get_tuning_recommendations()
        if recs:
            logger.info("\n  ─── Auto-Tuning Recommendations ───")
            for r in recs:
                logger.info(f"    💡 {r}")

        logger.info("=" * 80)
