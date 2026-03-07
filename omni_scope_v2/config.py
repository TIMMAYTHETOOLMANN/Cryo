#!/usr/bin/env python3
"""
omni_scope_v2.config — Unified Configuration
===============================================
Centralised settings for ALL 7 modules + data lake + signal bus.
Reads from environment (.env) and provides typed, immutable config objects.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv


# ── Data Lake ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class KafkaConfig:
    bootstrap_servers: str = "localhost:9092"
    consumer_group: str = "omni-scope-v2"
    auto_offset_reset: str = "latest"
    security_protocol: str = "PLAINTEXT"


@dataclass(frozen=True)
class RedisConfig:
    url: str = "redis://localhost:6379/0"
    max_connections: int = 20


@dataclass(frozen=True)
class TimescaleConfig:
    dsn: str = "postgresql://cryo:cryo@localhost:5432/cryo_ts"
    pool_min: int = 2
    pool_max: int = 10


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "cryo"


@dataclass(frozen=True)
class DataLakeConfig:
    kafka: KafkaConfig = field(default_factory=KafkaConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    timescale: TimescaleConfig = field(default_factory=TimescaleConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)


# ── Module 1: Deep-Crawl Archive Indexer ───────────────────────────

@dataclass(frozen=True)
class DeepCrawlConfig:
    graph_api_url: str = "https://gateway.thegraph.com/api"
    graph_api_key: str = ""
    nansen_api_key: str = ""
    oxppl_api_key: str = ""
    debank_api_key: str = ""
    clustering_interval_hours: int = 24
    subgraph_poll_interval_s: int = 15
    smart_money_tags: tuple = (
        "smart_money", "whale", "fund", "protocol_deployer",
    )
    max_wallet_clusters: int = 50_000
    governance_poll_interval_s: int = 60
    cascade_depth: int = 3


# ── Module 2: Mempool Microscope ──────────────────────────────────

@dataclass(frozen=True)
class MempoolMicroscopeConfig:
    bloxroute_api_key: str = ""
    bloxroute_ws_url: str = "wss://mev.api.blxrbdn.com/ws"
    blocknative_api_key: str = ""
    blocknative_ws_url: str = "wss://api.blocknative.com/v0"
    infura_ws_url: str = ""
    flashbots_rpc: str = "https://rpc.flashbots.net"
    min_swap_impact_usd: float = 50_000.0
    fork_node_url: str = ""
    gas_lstm_lookback_blocks: int = 200
    gas_lstm_predict_blocks: int = 5
    oracle_selectors: tuple = (
        "0xc9807539", "0x202ee0ed", "0xe4a30116",
    )
    max_pending_cache: int = 50_000


# ── Module 3: Yield & Arbitrage Hyper-Solver ──────────────────────

@dataclass(frozen=True)
class HyperSolverConfig:
    max_hops: int = 5
    max_paths_per_pair: int = 50
    min_arb_profit_usd: float = 10.0
    min_yield_spread_bps: int = 50
    dex_graph_refresh_s: int = 30
    defillama_api_url: str = "https://yields.llama.fi"
    lifi_api_url: str = "https://li.quest/v1"
    socket_api_url: str = "https://api.socket.tech/v2"
    oneinch_api_url: str = "https://api.1inch.dev"
    eigenlayer_subgraph: str = ""
    bellman_ford_timeout_ms: int = 500


# ── Module 4: Alpha-Seeker Sentiment ─────────────────────────────

@dataclass(frozen=True)
class AlphaSeekerConfig:
    twitter_bearer_token: str = ""
    kaito_api_key: str = ""
    dexu_api_key: str = ""
    coincarp_api_key: str = ""
    oxppl_api_key: str = ""
    debank_api_key: str = ""
    sentiment_model: str = "vader"
    min_sentiment_spike: float = 2.0
    governance_protocols: tuple = (
        "aave", "compound", "maker", "uniswap", "curve", "lido",
    )
    kol_wallets: tuple = ()
    contract_mirror_chains: tuple = (1, 42161, 10, 8453, 137, 56, 43114)
    poll_interval_s: int = 30


# ── Module 5: Static Analysis Engine ─────────────────────────────

@dataclass(frozen=True)
class StaticAnalysisConfig:
    enabled: bool = True
    decompile_unverified: bool = True
    max_contract_size_bytes: int = 50_000
    vulnerability_patterns: tuple = (
        "selfdestruct", "delegatecall", "tx.origin",
    )
    mev_function_patterns: tuple = (
        "swap", "flash", "liquidat", "borrow",
    )
    etherscan_api_key: str = ""
    bytecode_similarity_threshold: float = 0.85
    symbolic_execution_timeout_s: int = 30


# ── Module 6: ML Aggregator & Ranker ─────────────────────────────

@dataclass(frozen=True)
class MLAggregatorConfig:
    model_type: str = "gradient_boosting"
    min_quality_score: float = 0.3
    competition_decay_s: float = 2.0
    feature_weights: Dict[str, float] = field(default_factory=lambda: {
        "expected_value": 0.30,
        "competition": 0.25,
        "complexity": 0.15,
        "gas_cost": 0.15,
        "urgency": 0.15,
    })
    retrain_interval_hours: int = 6
    max_queue_size: int = 10_000
    strategy_routes: Dict[str, str] = field(default_factory=lambda: {
        "pending_liquidation": "liquidation_engine",
        "arbitrage": "arb_module",
        "cross_chain_arb": "cross_chain_executor",
        "sandwich": "mev_strategy",
        "backrun": "mev_strategy",
        "yield_opportunity": "yield_optimizer",
        "bridge_imbalance": "cross_chain_executor",
    })


# ── Module 7: Zero-Capital Layer ─────────────────────────────────

@dataclass(frozen=True)
class ZeroCapitalConfig:
    bootstrap_enabled: bool = True
    min_bootstrap_profit_usd: float = 50.0
    gas_self_fund_pct: float = 0.10
    target_gas_balance_usd: Dict[int, float] = field(default_factory=lambda: {
        1: 50.0, 42161: 5.0, 10: 5.0, 8453: 5.0,
        137: 2.0, 43114: 10.0, 56: 5.0,
    })
    flash_loan_surplus_min_usd: float = 100.0
    gas_reinvestment_enabled: bool = True
    treasury_address: str = ""


# ── Signal Bus ────────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalBusConfig:
    max_buffer: int = 50_000
    signal_ttl_s: float = 300.0
    dedup_window_s: float = 60.0
    ranked_queue_max: int = 10_000


# ── Module 8: Execution Router ───────────────────────────────────

@dataclass(frozen=True)
class ExecutionRouterConfig:
    max_queue_size: int = 5_000
    max_per_cycle: int = 20
    max_retries: int = 3
    default_deadline_s: float = 30.0
    max_gas_usd: float = 50.0
    default_slippage_bps: int = 50
    private_mempool_url: str = "https://rpc.flashbots.net"
    fork_node_url: str = ""


# ── Module 9: Performance Optimizer ──────────────────────────────

@dataclass(frozen=True)
class PerformanceConfig:
    pool_size_per_chain: int = 10
    batch_size: int = 100
    batch_flush_ms: int = 50
    cache_max_size: int = 10_000
    cache_ttl_s: float = 300.0
    max_memory_mb: float = 2048.0
    health_check_interval_s: float = 30.0
    auto_tune_interval_s: float = 120.0


# ── Master Config ─────────────────────────────────────────────────

class OmniScopeV2Config:
    """Unified configuration for the entire Omni-Scope v2 system."""

    def __init__(self, env_file: Optional[str] = None):
        self._load_env(env_file)

    @staticmethod
    def _load_env(env_file: Optional[str]):
        if env_file:
            load_dotenv(env_file)
            return
        for candidate in [
            Path(__file__).resolve().parents[1] / ".env",
            Path.cwd() / ".env",
        ]:
            if candidate.exists():
                load_dotenv(candidate)
                return

    # ── Subsystem configs ──────────────────────────────────────

    @property
    def data_lake(self) -> DataLakeConfig:
        return DataLakeConfig(
            kafka=KafkaConfig(
                bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            ),
            redis=RedisConfig(
                url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            ),
            timescale=TimescaleConfig(
                dsn=os.getenv("TIMESCALE_DSN", "postgresql://cryo:cryo@localhost:5432/cryo_ts"),
            ),
            neo4j=Neo4jConfig(
                uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                user=os.getenv("NEO4J_USER", "neo4j"),
                password=os.getenv("NEO4J_PASSWORD", "cryo"),
            ),
        )

    @property
    def deep_crawl(self) -> DeepCrawlConfig:
        return DeepCrawlConfig(
            graph_api_key=os.getenv("GRAPH_API_KEY", ""),
            nansen_api_key=os.getenv("NANSEN_API_KEY", ""),
            oxppl_api_key=os.getenv("OXPPL_API_KEY", ""),
            debank_api_key=os.getenv("DEBANK_API_KEY", ""),
        )

    @property
    def mempool_microscope(self) -> MempoolMicroscopeConfig:
        return MempoolMicroscopeConfig(
            bloxroute_api_key=os.getenv("BLOXROUTE_API_KEY", ""),
            blocknative_api_key=os.getenv("BLOCKNATIVE_API_KEY", ""),
            infura_ws_url=os.getenv("INFURA_WS_URL", ""),
            fork_node_url=os.getenv("FORK_NODE_URL", os.getenv("FORK_NODE_URL", "")),
        )

    @property
    def hyper_solver(self) -> HyperSolverConfig:
        return HyperSolverConfig(
            min_arb_profit_usd=float(os.getenv("HYPER_SOLVER_MIN_ARB_USD", "10")),
            max_hops=int(os.getenv("HYPER_SOLVER_MAX_HOPS", "5")),
        )

    @property
    def alpha_seeker(self) -> AlphaSeekerConfig:
        return AlphaSeekerConfig(
            twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN", ""),
            kaito_api_key=os.getenv("KAITO_API_KEY", ""),
            dexu_api_key=os.getenv("DEXU_API_KEY", ""),
            coincarp_api_key=os.getenv("COINCARP_API_KEY", ""),
            oxppl_api_key=os.getenv("OXPPL_API_KEY", ""),
            debank_api_key=os.getenv("DEBANK_API_KEY", ""),
        )

    @property
    def static_analysis(self) -> StaticAnalysisConfig:
        return StaticAnalysisConfig(
            etherscan_api_key=os.getenv("ETHERSCAN_API_KEY", ""),
            enabled=os.getenv("STATIC_ANALYSIS_ENABLED", "true").lower() == "true",
            decompile_unverified=os.getenv("DECOMPILE_UNVERIFIED", "true").lower() == "true",
        )

    @property
    def ml_aggregator(self) -> MLAggregatorConfig:
        return MLAggregatorConfig(
            min_quality_score=float(os.getenv("ML_MIN_QUALITY_SCORE", "0.3")),
        )

    @property
    def zero_capital(self) -> ZeroCapitalConfig:
        return ZeroCapitalConfig(
            bootstrap_enabled=os.getenv("BOOTSTRAP_ENABLED", "true").lower() == "true",
            min_bootstrap_profit_usd=float(os.getenv("BOOTSTRAP_MIN_PROFIT_USD", "50")),
            treasury_address=os.getenv("TREASURY_ADDRESS", ""),
        )

    @property
    def execution_router(self) -> ExecutionRouterConfig:
        return ExecutionRouterConfig(
            max_gas_usd=float(os.getenv("EXEC_MAX_GAS_USD", "50")),
            private_mempool_url=os.getenv("FLASHBOTS_RPC", "https://rpc.flashbots.net"),
            fork_node_url=os.getenv("FORK_NODE_URL", os.getenv("FORK_NODE_URL", "")),
        )

    @property
    def performance(self) -> PerformanceConfig:
        return PerformanceConfig(
            pool_size_per_chain=int(os.getenv("RPC_POOL_SIZE", "10")),
            batch_size=int(os.getenv("BATCH_SIZE", "100")),
            max_memory_mb=float(os.getenv("MAX_MEMORY_MB", "2048")),
        )

    @property
    def signal_bus(self) -> SignalBusConfig:
        return SignalBusConfig()


# ── Singleton ─────────────────────────────────────────────────────

_config: Optional[OmniScopeV2Config] = None


def get_config() -> OmniScopeV2Config:
    global _config
    if _config is None:
        _config = OmniScopeV2Config()
    return _config
