#!/usr/bin/env python3
"""
MODULE 9 — Configuration
==========================
Centralised settings for all Omni-Scope arrays.
Reads from root .env and provides typed access.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv


@dataclass
class MempoolRadarConfig:
    bloxroute_api_key: str = ""
    bloxroute_ws_url: str = "wss://mev.api.blxrbdn.com/ws"
    blocknative_api_key: str = ""
    blocknative_ws_url: str = "wss://api.blocknative.com/v0"
    infura_ws_url: str = ""
    min_swap_impact_usd: float = 50_000
    oracle_function_selectors: List[str] = field(default_factory=lambda: [
        "0xc9807539", "0x202ee0ed", "0xe4a30116",  # Chainlink transmit/submit
    ])
    max_pending_cache: int = 50_000


@dataclass
class ContractCrawlerConfig:
    etherscan_api_key: str = ""
    coincarp_api_key: str = ""
    kaito_api_key: str = ""
    debank_api_key: str = ""
    kol_wallets: List[str] = field(default_factory=list)
    scan_interval_seconds: int = 30
    bytecode_similarity_threshold: float = 0.85


@dataclass
class StaticAnalysisConfig:
    enabled: bool = True
    decompile_unverified: bool = True
    max_contract_size_bytes: int = 50_000
    vulnerability_patterns: List[str] = field(default_factory=lambda: [
        "selfdestruct", "delegatecall", "tx.origin",
    ])


@dataclass
class BridgeMonitorConfig:
    max_hops: int = 6
    min_arb_profit_usd: float = 2.0
    max_bridge_time_seconds: int = 600
    pathfinding_interval_seconds: int = 5


@dataclass
class MLRankerConfig:
    model_type: str = "gradient_boosting"  # or "logistic", "neural"
    min_quality_score: float = 0.15
    competition_decay_seconds: float = 1.0
    feature_weights: Dict[str, float] = field(default_factory=lambda: {
        "expected_value": 0.40,
        "competition": 0.20,
        "complexity": 0.15,
        "gas_cost": 0.25,
    })


@dataclass
class ArchiveIndexerConfig:
    graph_api_url: str = "https://gateway.thegraph.com/api"
    graph_api_key: str = ""
    nansen_api_key: str = ""
    clustering_interval_hours: int = 24
    smart_money_tags: List[str] = field(default_factory=lambda: [
        "smart_money", "whale", "fund", "protocol_deployer",
    ])


@dataclass
class AlphaSeekerConfig:
    twitter_bearer_token: str = ""
    sentiment_model: str = "vader"  # or "transformer"
    governance_protocols: List[str] = field(default_factory=lambda: [
        "aave", "compound", "maker", "uniswap", "curve",
    ])
    min_sentiment_spike: float = 2.0  # 2x normal volume


@dataclass
class ZeroCapitalConfig:
    bootstrap_enabled: bool = True
    min_bootstrap_profit_usd: float = 2.0
    gas_self_fund_pct: float = 0.05  # Reserve 5% of profit for gas (lean ops)


class OmniScopeConfig:
    """Centralised config for all Module 9 subsystems."""

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

    @property
    def mempool_radar(self) -> MempoolRadarConfig:
        return MempoolRadarConfig(
            bloxroute_api_key=os.getenv("BLOXROUTE_API_KEY", ""),
            blocknative_api_key=os.getenv("BLOCKNATIVE_API_KEY", ""),
            infura_ws_url=os.getenv("INFURA_WS_URL", ""),
            min_swap_impact_usd=float(os.getenv("MEMPOOL_MIN_SWAP_IMPACT_USD", "50000")),
        )

    @property
    def contract_crawler(self) -> ContractCrawlerConfig:
        return ContractCrawlerConfig(
            etherscan_api_key=os.getenv("ETHERSCAN_API_KEY", ""),
            coincarp_api_key=os.getenv("COINCARP_API_KEY", ""),
            kaito_api_key=os.getenv("KAITO_API_KEY", ""),
            debank_api_key=os.getenv("DEBANK_API_KEY", ""),
        )

    @property
    def static_analysis(self) -> StaticAnalysisConfig:
        return StaticAnalysisConfig(
            enabled=os.getenv("STATIC_ANALYSIS_ENABLED", "true").lower() == "true",
            decompile_unverified=os.getenv("DECOMPILE_UNVERIFIED", "true").lower() == "true",
        )

    @property
    def bridge_monitor(self) -> BridgeMonitorConfig:
        return BridgeMonitorConfig(
            max_hops=int(os.getenv("BRIDGE_MAX_HOPS", "4")),
            min_arb_profit_usd=float(os.getenv("BRIDGE_MIN_ARB_PROFIT_USD", "25")),
        )

    @property
    def ml_ranker(self) -> MLRankerConfig:
        return MLRankerConfig(
            min_quality_score=float(os.getenv("ML_MIN_QUALITY_SCORE", "0.3")),
        )

    @property
    def archive_indexer(self) -> ArchiveIndexerConfig:
        return ArchiveIndexerConfig(
            graph_api_key=os.getenv("GRAPH_API_KEY", ""),
            nansen_api_key=os.getenv("NANSEN_API_KEY", ""),
        )

    @property
    def alpha_seeker(self) -> AlphaSeekerConfig:
        return AlphaSeekerConfig(
            twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN", ""),
        )

    @property
    def zero_capital(self) -> ZeroCapitalConfig:
        return ZeroCapitalConfig(
            bootstrap_enabled=os.getenv("BOOTSTRAP_ENABLED", "true").lower() == "true",
            min_bootstrap_profit_usd=float(os.getenv("BOOTSTRAP_MIN_PROFIT_USD", "50")),
        )


_omni_config: Optional[OmniScopeConfig] = None


def get_omni_config() -> OmniScopeConfig:
    global _omni_config
    if _omni_config is None:
        _omni_config = OmniScopeConfig()
    return _omni_config
