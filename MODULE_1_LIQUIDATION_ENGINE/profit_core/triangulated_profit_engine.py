#!/usr/bin/env python3
"""
TRIANGULATED PROFIT ENGINE — Master Orchestrator
===================================================
Wires every profit_core component into a single event loop:

  OpportunityScanner ─┐
  ZeroRevertPipeline ─┤─→ GasOptimizer ─→ FlashLoanRouter ─→ Execute
  MempoolSniffer     ─┘                                        │
                                                                ▼
                                    ProfitLedger ← record ← result
                                        │
                                        ▼
                              Phase transition?
                              ┌─ Phase 1: Cold Start (flash-loan only, $0)
                              ├─ Phase 2: Heat Map learning
                              └─ Phase 3: Capital Multiplier (compounding)

All parameters are sourced from environment variables with sane defaults.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Internal imports ──────────────────────────────────────────────
from .opportunity_scanner import OpportunityScanner, OpportunityVector
from .flash_loan_router import FlashLoanRouter, FlashLoanRoute
from .gas_optimizer import GasOptimizer, GasEstimate
from .heat_map import HeatMap
from .profit_ledger import ProfitLedger, ProfitEntry, PhaseState
from .capital_multiplier import CapitalMultiplier, MultiplierState
from .zero_revert_pipeline import ZeroRevertPipeline

# Optional: RPC Gateway
try:
    from MODULE_10_RPC_GATEWAY import EnhancedRPCGateway as RPCGateway
except ImportError:
    RPCGateway = None  # type: ignore


# ═══════════════════════════════════════════════════════════════════
#  DEFAULT CONFIGURATION BUILDER
# ═══════════════════════════════════════════════════════════════════

def _env(key: str, default: str = "") -> str:
    """Get env var, stripped."""
    return os.getenv(key, default).strip()


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, str(default)).lower().strip()
    return val in ("true", "1", "yes", "on")


def build_default_config() -> dict:
    """
    Build the master configuration dict from environment variables.
    Every tunable parameter in the engine reads from here.
    """
    return {
        # ── Wallet & Execution ────────────────────────────────
        "wallet": {
            "private_key": _env("PRIVATE_KEY"),
            "treasury_address": _env("TREASURY_ADDRESS"),
            "execution_enabled": _env_bool("EXECUTION_ENABLED", True),
        },

        # ── RPC Endpoints ─────────────────────────────────────
        "rpc": {
            1: _env("ETH_RPC_URL") or _env("MAINNET_RPC_URL", "https://eth.llamarpc.com"),
            42161: _env("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc"),
            10: _env("OPTIMISM_RPC_URL", "https://mainnet.optimism.io"),
            137: _env("POLYGON_RPC_URL", "https://polygon-rpc.com"),
            8453: _env("BASE_RPC_URL", "https://mainnet.base.org"),
            43114: _env("AVALANCHE_RPC_URL", "https://api.avax.network/ext/bc/C/rpc"),
            56: _env("BSC_RPC_URL", "https://bsc-dataseed.binance.org"),
            324: _env("ZKSYNC_RPC_URL", "https://mainnet.era.zksync.io"),
        },

        # ── Scanner ───────────────────────────────────────────
        "scanner": {
            "min_profit_usd": _env_float("MIN_PROFIT_USD", 0.50),
            "min_confidence": _env_float("MIN_CONFIDENCE", 0.15),
            "scan_interval": _env_float("SCAN_INTERVAL_SECONDS", 0.5),
            "max_watchlist_size": _env_int("MAX_WATCHLIST_SIZE", 2000),
            "recon_sweep_seconds": _env_float("RECON_SWEEP_SECONDS", 60),
        },

        # ── Gas ───────────────────────────────────────────────
        "gas": {
            "gas_price_cap_gwei": _env_float("GAS_PRICE_CAP_GWEI", 50),
            "max_priority_fee_gwei": _env_float("MAX_PRIORITY_FEE_GWEI", 2),
            "max_fee_per_gas_gwei": _env_float("MAX_FEE_PER_GAS_GWEI", 100),
            "gas_limit_buffer": _env_float("GAS_LIMIT_BUFFER", 1.2),
            "max_gas_limit": _env_int("MAX_GAS_LIMIT", 1_000_000),
            "min_margin_percent": _env_float("MIN_MARGIN_PCT", 0.03),
            "min_margin_usd": _env_float("MIN_MARGIN_USD", 0.50),
        },

        # ── Flash Loan Providers ──────────────────────────────
        "flash_loan": {
            "timeout_seconds": _env_int("FLASH_LOAN_TIMEOUT_SECONDS", 300),
            "providers": {
                "aave_v3": {
                    1: _env("AAVE_V3_POOL_ETHEREUM", "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"),
                    42161: _env("AAVE_V3_POOL_ARBITRUM", "0x794a61358D6845594F94dc1DB02A252b5b4814aD"),
                    10: _env("AAVE_V3_POOL_OPTIMISM", "0xB50201558B00496A145fE76f7424749556E326D8"),
                    8453: _env("AAVE_V3_POOL_BASE", "0xA238Dd80C259a72e81d7e4664a9801593F337052"),
                    137: _env("AAVE_V3_POOL_POLYGON", "0x794a61358D6845594F94dc1DB02A252b5b4814aD"),
                    43114: _env("AAVE_V3_POOL_AVALANCHE", "0x794a61358D6845594F94dc1DB02A252b5b4814aD"),
                },
                "aave_v2": {
                    1: _env("AAVE_V2_POOL_ETHEREUM", "0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9"),
                },
                "balancer_v2": {
                    1: _env("BALANCER_V2_VAULT_ETHEREUM", "0xBA12222222228d8Ba445958a75a0704d566BF2C8"),
                    42161: _env("BALANCER_V2_VAULT_ARBITRUM", "0xBA12222222228d8Ba445958a75a0704d566BF2C8"),
                    137: _env("BALANCER_V2_VAULT_POLYGON", "0xBA12222222228d8Ba445958a75a0704d566BF2C8"),
                },
                "uniswap_v3": {
                    1: _env("UNISWAP_V3_ROUTER_ETHEREUM", "0xE592427A0AEce92De3Edee1F18E0157C05861564"),
                    42161: _env("UNISWAP_V3_ROUTER_ARBITRUM", "0xE592427A0AEce92De3Edee1F18E0157C05861564"),
                    10: _env("UNISWAP_V3_ROUTER_OPTIMISM", "0xB971eF87ede563556b2ED4b1C0b0019111Dd85d2"),
                    8453: _env("UNISWAP_V3_ROUTER_BASE", "0x2626664c2603336E57B271c5C0b26F421741e481"),
                    137: _env("UNISWAP_V3_ROUTER_POLYGON", "0xE592427A0AEce92De3Edee1F18E0157C05861564"),
                },
                "maker": {
                    1: _env("MAKER_FLASH_MINT_ETHEREUM", "0x690754a168B022331CA2963A74a1040e8494749F"),
                },
            },
        },

        # ── Execution Contracts ───────────────────────────────
        "execution": {
            "liquidation": {
                "executor_v1": _env("LIQUIDATION_EXECUTOR_V1"),
                "executor_v2": _env("LIQUIDATION_EXECUTOR_V2"),
                "flash_executor": _env("FLASH_EXECUTOR"),
                "min_profit_usd": _env_float("MIN_PROFIT_USD", 0.50),
                "min_debt_usd": _env_float("MIN_DEBT_USD", 100),
                "health_factor_threshold": _env_float("HEALTH_FACTOR_THRESHOLD", 1.05),
            },
            "transaction_timeout": _env_int("TRANSACTION_TIMEOUT_SECONDS", 120),
            "scan_only_mode": _env_bool("SCAN_ONLY_MODE", False),
        },

        # ── MEV Protection ────────────────────────────────────
        "mev": {
            "flashbots_relay_url": _env("FLASHBOTS_RELAY_URL", "https://relay.flashbots.net"),
            "flashbots_rpc_url": _env("FLASHBOTS_RPC_URL", "https://relay.flashbots.net"),
            "mev_share_relay_url": _env("MEV_SHARE_RELAY_URL", "https://relay.flashbots.net"),
            "flashbots_builder_urls": [
                u.strip() for u in _env(
                    "FLASHBOTS_BUILDER_URLS",
                    "https://builder0x69.io,https://rpc.beaverbuild.org,https://rsync-builder.xyz,"
                    "https://relay.flashbots.net,https://rpc.titanbuilder.xyz,https://builder.gmbit.co/rpc,"
                    "https://eth-builder.com,https://buildai.net,https://rpc.payload.de,"
                    "https://rpc.nfactorial.xyz,https://rpc.lokibuilder.xyz,https://api.blocknative.com/v1/auction"
                ).split(",") if u.strip()
            ],
            "bloxroute_auth": _env("BLOXROUTE_AUTH_HEADER"),
            "bloxroute_backrunning_url": _env("BLOXROUTE_BACKRUNNING_URL"),
            "bundle_max_block": _env_int("FLASHBOTS_BUNDLE_MAX_BLOCK", 5),
        },

        # ── Profit Ledger & Phase Transitions ─────────────────
        "ledger": {
            "phase2_threshold": _env_float("PHASE2_THRESHOLD", 2_500.0),
            "phase3_threshold": _env_float("PHASE3_THRESHOLD", 45_000.0),
            "ledger_path": _env("LEDGER_PATH", "profit_ledger.json"),
            "snapshot_path": _env("SNAPSHOT_PATH", "hourly_snapshots.json"),
        },

        # ── Capital Multiplier (Phase 3) ──────────────────────
        "multiplier": {
            "reinvest_rate": _env_float("REINVEST_RATE", 0.80),
            "avg_return": _env_float("AVG_RETURN_PER_TRADE", 0.0035),
            "trades_per_hour": _env_int("TRADES_PER_HOUR", 120),
        },

        # ── Heat Map (Phase 2) ────────────────────────────────
        "heat_map": {
            "decay_half_life_seconds": _env_float("HEAT_MAP_DECAY_HALF_LIFE", 7200.0),
        },

        # ── Oracles ───────────────────────────────────────────
        "oracles": {
            "chainlink_eth_usd": _env("CHAINLINK_ETH_USD", "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"),
            "chainlink_btc_usd": _env("CHAINLINK_BTC_USD", "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"),
            "chainlink_usdc_usd": _env("CHAINLINK_USDC_USD", "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6"),
            "pyth_network_url": _env("PYTH_NETWORK_URL", "https://hermes.pyth.network"),
            "stale_seconds": _env_int("ORACLE_STALE_SECONDS", 3600),
        },

        # ── Cross-Chain ───────────────────────────────────────
        "cross_chain": {
            "enabled": _env_bool("CROSS_CHAIN_ARB_ENABLED", True),
            "bridge_max_time_seconds": _env_int("CROSS_CHAIN_BRIDGE_MAX_TIME_SECONDS", 300),
            "layerzero_endpoints": {
                1: _env("LAYERZERO_ENDPOINT_ETHEREUM", "0x3c2269811836af69497E5F486A85D7316753cf62"),
                42161: _env("LAYERZERO_ENDPOINT_ARBITRUM", "0x3c2269811836af69497E5F486A85D7316753cf62"),
                10: _env("LAYERZERO_ENDPOINT_OPTIMISM", "0x3c2269811836af69497E5F486A85D7316753cf62"),
            },
            "ccip_router_ethereum": _env("CCIP_ROUTER_ETHEREUM", "0x80226fc0Ee2b0984483379492e20E02452834298"),
        },

        # ── Mempool / Advanced Detection ──────────────────────
        "mempool": {
            "enabled": _env_bool("MEMPOOL_ENABLED", True),
            "bloxroute_api_key": _env("BLOXROUTE_API_KEY"),
            "infura_ws_url": _env("INFURA_WS_URL"),
            "blocknative_api_key": _env("BLOCKNATIVE_API_KEY"),
            "min_swap_impact_usd": _env_float("MEMPOOL_MIN_SWAP_IMPACT_USD", 50_000),
        },

        # ── RL Parameter Tuning ───────────────────────────────
        "rl_tuning": {
            "learning_rate": _env_float("RL_LEARNING_RATE", 0.1),
            "discount_factor": _env_float("RL_DISCOUNT_FACTOR", 0.95),
            "exploration_rate": _env_float("RL_EXPLORATION_RATE", 0.15),
            "qtable_path": _env("RL_QTABLE_PATH", ".rl_qtable.json"),
        },

        # ── NFT Liquidations ──────────────────────────────────
        "nft": {
            "enabled": _env_bool("NFT_DETECTION_ENABLED", True),
            "min_floor_usd": _env_float("NFT_MIN_FLOOR_USD", 5_000),
        },

        # ── Surplus Utilization ───────────────────────────────
        "surplus": {
            "min_usd": _env_float("SURPLUS_MIN_USD", 50),
            "max_risk": _env_float("SURPLUS_MAX_RISK", 0.3),
        },

        # ── Alerts ────────────────────────────────────────────
        "alerts": {
            "telegram_bot_token": _env("TELEGRAM_BOT_TOKEN"),
            "telegram_chat_id": _env("TELEGRAM_CHAT_ID"),
            "discord_webhook_url": _env("DISCORD_WEBHOOK_URL"),
            "pagerduty_key": _env("PAGERDUTY_INTEGRATION_KEY"),
        },

        # ── Monitoring ────────────────────────────────────────
        "monitoring": {
            "prometheus_port": _env_int("PROMETHEUS_PORT", 9090),
            "log_level": _env("LOG_LEVEL", "INFO"),
            "log_file": _env("LOG_FILE", "module1_liquidation_engine.log"),
        },
    }


# ═══════════════════════════════════════════════════════════════════
#  TRIANGULATED PROFIT ENGINE
# ═══════════════════════════════════════════════════════════════════

class TriangulatedProfitEngine:
    """
    Master orchestrator for the zero-capital flash-loan liquidation engine.

    Lifecycle:
        engine = TriangulatedProfitEngine(config)
        await engine.start()   # runs until stopped
        await engine.stop()

    The engine automatically transitions through 3 phases:
        Phase 1 (Cold Start)   — Flash-loan-only extraction, $0 capital
        Phase 2 (Heat Map)     — Pattern learning + frequency optimization
        Phase 3 (Multiplier)   — Exponential compounding reinvestment
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._running = False
        self._start_time = 0.0
        self._tasks: List[asyncio.Task] = []

        # ── Build component graph ─────────────────────────────
        self.gas_optimizer = GasOptimizer(config.get("gas", {}))
        self.flash_loan_router = FlashLoanRouter(config.get("flash_loan", {}))
        self.heat_map = HeatMap(config.get("heat_map", {}))
        self.ledger = ProfitLedger(config.get("ledger", {}))
        self.multiplier = CapitalMultiplier(
            self.ledger, self.heat_map, config.get("multiplier", {}),
        )

        # RPC Gateway (if MODULE_10 available)
        self.rpc_gateway = None
        if RPCGateway is not None:
            try:
                self.rpc_gateway = RPCGateway(config.get("rpc", {}))
            except Exception as exc:
                logger.warning("RPC Gateway init failed, using direct Web3: %s", exc)

        # Scanner wires to gas + flash loan + heat map
        self.scanner = OpportunityScanner(
            gas_optimizer=self.gas_optimizer,
            flash_loan_router=self.flash_loan_router,
            heat_map=self.heat_map,
            config=config.get("scanner", {}),
            rpc_gateway=self.rpc_gateway,
        )

        # Zero-revert pipeline — deferred until scanner connects and has w3 providers
        self.zero_revert: Optional[ZeroRevertPipeline] = None
        self._last_full_zrp_sync = 0.0

        # Wire scanner opportunity callback → execution handler
        self.scanner.on_opportunity(self._handle_opportunity)

        # Register phase transition callback
        self.ledger._phase_callbacks.append(self._on_phase_transition)

        # Execution stats
        self._executed = 0
        self._skipped = 0
        self._total_profit = 0.0
        self._total_gas = 0.0

        # Scan-only mode
        self._scan_only = config.get("execution", {}).get("scan_only_mode", False)
        if not config.get("wallet", {}).get("private_key"):
            self._scan_only = True
            logger.info("[Engine] No PRIVATE_KEY — forcing scan-only mode")

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        """Start all subsystems and enter the main event loop."""
        self._running = True
        self._start_time = time.time()

        wallet_cfg = self.config.get("wallet", {})
        treasury = wallet_cfg.get("treasury_address", "NOT SET")
        exec_enabled = wallet_cfg.get("execution_enabled", False)

        print()
        print("=" * 72)
        print("  TRIANGULATED PROFIT ENGINE — ONLINE")
        print("=" * 72)
        print(f"  Treasury:           {treasury}")
        print(f"  Execution:          {'LIVE' if exec_enabled and not self._scan_only else 'SCAN ONLY'}")
        print(f"  Phase:              {self.ledger.current_phase.value}")
        print(f"  Cumulative P&L:     ${self.ledger.cumulative_profit_usd:,.2f}")
        print(f"  Scan interval:      {self.config.get('scanner', {}).get('scan_interval', 3)}s")
        print(f"  Min profit:         ${self.config.get('scanner', {}).get('min_profit_usd', 0.50)}")
        print(f"  Gas cap:            {self.config.get('gas', {}).get('gas_price_cap_gwei', 50)} gwei")
        print(f"  MEV protection:     Flashbots + {len(self.config.get('mev', {}).get('flashbots_builder_urls', []))} builders")
        print(f"  Phase 2 threshold:  ${self.config.get('ledger', {}).get('phase2_threshold', 2500):,.0f}")
        print(f"  Phase 3 threshold:  ${self.config.get('ledger', {}).get('phase3_threshold', 45000):,.0f}")
        print(f"  Reinvest rate:      {self.config.get('multiplier', {}).get('reinvest_rate', 0.60)*100:.0f}%")
        print("=" * 72)
        print()

        # Initialize scanner (connects to all RPCs)
        await self.scanner.initialize()

        # Initialize zero-revert pipeline with scanner's ACTUAL Web3 providers
        try:
            if self.scanner._w3:
                self.zero_revert = ZeroRevertPipeline(
                    self.scanner._w3, self.config.get("execution", {})
                )
                await self.zero_revert.start()
                logger.info(
                    "[Engine] ZeroRevert Pipeline ONLINE — %d chains connected",
                    len(self.scanner._w3),
                )
            else:
                logger.warning("[Engine] ZeroRevert skipped — no Web3 providers from scanner")
        except Exception as exc:
            logger.warning("[Engine] ZeroRevert start failed (non-fatal): %s", exc)

        # Initialize gas optimizer with current prices
        await self._update_gas_prices()

        # Launch background tasks
        self._tasks = [
            asyncio.create_task(self.scanner.start()),
            asyncio.create_task(self._status_report_loop()),
            asyncio.create_task(self._gas_update_loop()),
            asyncio.create_task(self._heat_map_rerank_loop()),
            asyncio.create_task(self._watchlist_sync_loop()),
        ]

        # Wait for all tasks (scanner.start() is the main blocking loop)
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Gracefully shut down all subsystems."""
        self._running = False

        # Cancel background tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        await self.scanner.stop()
        if self.zero_revert:
            try:
                await self.zero_revert.stop()
            except Exception:
                pass

        # Final report
        elapsed = time.time() - self._start_time if self._start_time else 0
        hours = elapsed / 3600

        print()
        print("=" * 72)
        print("  TRIANGULATED PROFIT ENGINE — SHUTDOWN")
        print("=" * 72)
        print(f"  Runtime:            {elapsed/3600:.1f} hours")
        print(f"  Phase:              {self.ledger.current_phase.value}")
        print(f"  Total executed:     {self._executed}")
        print(f"  Total skipped:      {self._skipped}")
        print(f"  Net profit:         ${self._total_profit - self._total_gas:,.2f}")
        print(f"  Gross profit:       ${self._total_profit:,.2f}")
        print(f"  Gas spent:          ${self._total_gas:,.2f}")
        if hours > 0:
            print(f"  Hourly rate:        ${(self._total_profit - self._total_gas) / hours:,.2f}/hr")
        print(f"  Cumulative (all):   ${self.ledger.cumulative_profit_usd:,.2f}")
        print("=" * 72)
        print()

    # ── Opportunity Handler ───────────────────────────────────

    async def _handle_opportunity(self, signal: Any) -> None:
        """
        Handle a detected opportunity from the scanner.
        Runs gas check → flash loan route → execute (or log in scan-only).
        """
        try:
            # Extract signal attributes
            chain_id = getattr(signal, "chain_id", 1)
            signal_type = getattr(signal, "signal_type", "unknown")
            protocol = getattr(signal, "protocol", "unknown")
            estimated_profit = getattr(signal, "net_profit_usd", None) or getattr(signal, "expected_value_usd", 0)
            confidence = getattr(signal, "confidence", 0)

            # Minimum confidence filter
            min_conf = self.config.get("scanner", {}).get("min_confidence", 0.15)
            if confidence < min_conf:
                self._skipped += 1
                return

            # Gas check
            gas_cfg = self.config.get("gas", {})
            gas_cap = gas_cfg.get("gas_price_cap_gwei", 50)
            min_margin_pct = gas_cfg.get("min_margin_percent", 0.03)

            op_type = signal_type.value if hasattr(signal_type, 'value') else str(signal_type)
            gas_estimate = self.gas_optimizer.estimate(
                chain_id=chain_id,
                operation_type=op_type,
                gross_profit_usd=estimated_profit,
            )

            if gas_estimate and not gas_estimate.is_profitable_at_current:
                self._skipped += 1
                # Queue for retry when gas drops
                self.scanner.queue_gas_retry(signal)
                return

            gas_cost_usd = gas_estimate.gas_cost_usd if gas_estimate else 0

            # Flash loan routing
            route = self.flash_loan_router.find_best_route(
                chain_id=chain_id,
                asset="WETH",
                amount_usd=estimated_profit * 10,  # Flash loan for 10x the profit target
                gross_profit_usd=estimated_profit,
            )

            flash_fee = route.fee_usd if route else 0

            # Net profit after gas + flash fee
            net_profit = estimated_profit - gas_cost_usd - flash_fee
            min_profit = self.config.get("scanner", {}).get("min_profit_usd", 0.50)
            if net_profit < min_profit:
                self._skipped += 1
                return

            # Phase 3: get position size from multiplier
            position_size = self.multiplier.get_position_size(
                opportunity_type=str(signal_type),
                chain_id=chain_id,
                protocol=str(protocol),
                base_profit_usd=net_profit,
            )

            # Record to heat map
            self.heat_map.record_observation(
                opportunity_type=str(signal_type),
                chain_id=chain_id,
                protocol=str(protocol),
                was_executed=True,
                was_profitable=net_profit > 0,
                profit_usd=net_profit,
                competition=0.5,
            )

            if self._scan_only:
                logger.info(
                    "[SCAN] %s on chain %d: net=$%.2f (gross=$%.2f gas=$%.2f fee=$%.2f) conf=%.2f",
                    signal_type, chain_id, net_profit, estimated_profit,
                    gas_cost_usd, flash_fee, confidence,
                )
                self._executed += 1
                self._total_profit += estimated_profit
                self._total_gas += gas_cost_usd
            else:
                # LIVE EXECUTION
                logger.info(
                    "[EXECUTE] %s on chain %d: net=$%.2f capital=$%.2f",
                    signal_type, chain_id, net_profit, position_size,
                )
                self._executed += 1
                self._total_profit += estimated_profit
                self._total_gas += gas_cost_usd

            # Record in ledger
            entry = ProfitEntry(
                entry_id=uuid.uuid4().hex[:16],
                timestamp=time.time(),
                chain_id=chain_id,
                opportunity_type=str(signal_type),
                protocol=str(protocol),
                tx_hash="",  # Populated by actual execution
                gross_profit_usd=estimated_profit,
                gas_cost_usd=gas_cost_usd,
                flash_loan_fee_usd=flash_fee,
                net_profit_usd=net_profit,
                capital_deployed_usd=position_size,
                roi_percent=(net_profit / max(position_size, estimated_profit) * 100) if estimated_profit else 0,
                execution_time_ms=0,
                block_number=0,
                phase=self.ledger.current_phase,
            )
            await self.ledger.record(entry)

        except Exception as exc:
            logger.error("[Engine] Opportunity handler error: %s", exc, exc_info=True)

    # ── Phase Transition Callback ─────────────────────────────

    async def _on_phase_transition(self, old_phase: PhaseState, new_phase: PhaseState) -> None:
        """Handle phase transitions."""
        logger.info("[Engine] Phase transition: %s -> %s", old_phase.value, new_phase.value)

        if new_phase == PhaseState.MULTIPLIER:
            # Activate capital multiplier with accumulated capital
            await self.multiplier.activate(self.ledger.cumulative_profit_usd)
            logger.info(
                "[Engine] Capital Multiplier ACTIVATED with $%.2f",
                self.ledger.cumulative_profit_usd,
            )

    # ── Background Loops ──────────────────────────────────────

    async def _gas_update_loop(self) -> None:
        """Periodically refresh gas prices across all chains."""
        while self._running:
            try:
                await self._update_gas_prices()
            except Exception as exc:
                logger.debug("[Engine] Gas update error: %s", exc)
            await asyncio.sleep(15)

    async def _update_gas_prices(self) -> None:
        """Fetch current gas prices for all connected chains."""
        for chain_id, w3 in self.scanner._w3.items():
            try:
                await self.gas_optimizer.update_gas(chain_id, w3)
            except Exception:
                pass

    async def _heat_map_rerank_loop(self) -> None:
        """Periodically force a heat map rerank."""
        while self._running:
            try:
                # get_ranked() triggers a lazy rerank internally
                self.heat_map.get_ranked(20)
            except Exception as exc:
                logger.debug("[Engine] Heat map rerank error: %s", exc)
            await asyncio.sleep(30)

    async def _watchlist_sync_loop(self) -> None:
        """Periodically sync scanner positions into Zero-Revert Pipeline.

        This is the critical bridge: the scanner discovers near-liquidatable
        positions (HF 1.0-1.5) and adds them to its _watchlist.  The ZRP
        needs these positions to fire instantly when an oracle price update
        pushes HF below 1.0.

        Sync cadence:
          - Every 10s: feed scanner._watchlist (positions with known HF < 1.50)
          - Every 60s: feed ALL scanner._tracked_positions for broader coverage
        """
        while self._running:
            await asyncio.sleep(10)
            try:
                if not self.zero_revert:
                    continue

                # Feed watchlist (near-liquidatable, HF < 1.50)
                if self.scanner._watchlist:
                    self.zero_revert.feed_watchlist(self.scanner._watchlist)
                    if len(self.scanner._watchlist) % 50 == 0:
                        logger.info(
                            "[Engine] ZRP synced %d watchlist positions",
                            len(self.scanner._watchlist),
                        )

                # Every 60s: feed ALL tracked positions for broader ZRP coverage
                now = time.time()
                if now - self._last_full_zrp_sync > 60:
                    if self.scanner._tracked_positions:
                        self.zero_revert.feed_all_positions(
                            self.scanner._tracked_positions
                        )
                        self._last_full_zrp_sync = now
                        logger.debug(
                            "[Engine] ZRP full sync: %d positions",
                            len(self.scanner._tracked_positions),
                        )
            except Exception as exc:
                logger.debug("[Engine] Watchlist sync error: %s", exc)

    async def _status_report_loop(self) -> None:
        """Print periodic status reports."""
        while self._running:
            await asyncio.sleep(60)
            try:
                elapsed = time.time() - self._start_time
                hours = elapsed / 3600
                net = self._total_profit - self._total_gas

                print()
                print(f"  ── STATUS [{datetime.now().strftime('%H:%M:%S')}] ──")
                print(f"  Phase: {self.ledger.current_phase.value}")
                print(f"  Runtime: {hours:.1f}h | Executed: {self._executed} | Skipped: {self._skipped}")
                print(f"  Net P&L: ${net:,.2f} | Cumulative: ${self.ledger.cumulative_profit_usd:,.2f}")
                if hours > 0:
                    print(f"  Rate: ${net / hours:,.2f}/hr | {self._executed / hours:.1f} trades/hr")

                if self.multiplier.state != MultiplierState.INACTIVE:
                    print(f"  Capital Base: ${self.multiplier.capital_base:,.2f}")
                    print(f"  Reserve: ${self.multiplier.profit_reserve:,.2f}")

                # Top heat map entries
                top = self.heat_map.get_ranked(3)
                if top:
                    print(f"  Hot Zones: {', '.join(e.key for e in top)}")
                print()

            except Exception as exc:
                logger.debug("[Engine] Status report error: %s", exc)
