#!/usr/bin/env python3
"""
Module 4 — Alpha-Seeker Off-Chain & Social Sentiment Crawler
===============================================================
Discovers opportunities from off-chain data before they hit the chain.

Capabilities:
  4.1  Social Sentiment & Whale Watching (Twitter, Discord, Telegram NLP)
  4.2  KOL & Mindshare Tracking (Kaito, Dexu AI, 0xPPL/DeBank)
  4.3  Programmatic Contract Discovery from Funding Data (CoinCarp, VC wallets)
  4.4  Cross-Chain Contract Mirroring (bytecode clone detection)

Data flow:
  Social feeds + funding data → NLP sentiment → flag assets
  → cross-reference lending markets → emit signals → SignalBus
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import aiohttp

from .config import AlphaSeekerConfig, get_config
from .data_lake import DataLake, Topic
from .signal_bus import SignalBus, SignalSource, SignalType, TriangulatedSignal

logger = logging.getLogger(__name__)


# ── Sentiment Keywords ───────────────────────────────────────────

BULLISH_KEYWORDS = frozenset({
    "moon", "pump", "bullish", "ath", "breakout", "accumulate",
    "undervalued", "gem", "100x", "launch", "mainnet", "listing",
    "partnership", "institutional", "adoption",
})

BEARISH_KEYWORDS = frozenset({
    "dump", "bearish", "crash", "scam", "rug", "hack", "exploit",
    "sell", "short", "overvalued", "ponzi", "liquidation", "depeg",
    "bankrupt", "insolvent",
})

GOVERNANCE_ENDPOINTS: Dict[str, str] = {
    "aave": "https://governance.aave.com/api/proposals",
    "compound": "https://api.compound.finance/api/v2/governance/proposals",
    "maker": "https://vote.makerdao.com/api/executive",
    "uniswap": "https://api.uniswap.org/v1/governance/proposals",
    "curve": "https://dao.curve.fi/api/proposals",
    "lido": "https://vote.lido.fi/api/proposals",
}

# Mapping of chain IDs for contract mirroring
MIRROR_CHAINS = {
    1: "ethereum", 42161: "arbitrum", 10: "optimism",
    8453: "base", 137: "polygon", 56: "bsc", 43114: "avalanche",
}


# ── Data Models ──────────────────────────────────────────────────

@dataclass
class SentimentSignal:
    token: str
    mention_count: int
    sentiment_score: float       # -1 to +1
    volume_spike_ratio: float    # current / avg (>2 = spike)
    source: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class KOLActivity:
    kol_address: str
    action: str                  # "deploy", "interact", "transfer"
    contract_address: str
    chain_id: int
    value_usd: float = 0.0
    is_verified_contract: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass
class FundingRound:
    project_name: str
    amount_usd: float
    investors: List[str] = field(default_factory=list)
    deployer_address: str = ""
    chain_ids: List[int] = field(default_factory=list)
    category: str = "defi"
    timestamp: float = field(default_factory=time.time)


@dataclass
class ContractClone:
    original_chain: int
    original_address: str
    clone_chain: int
    clone_address: str
    bytecode_similarity: float
    timestamp: float = field(default_factory=time.time)


# ── Module ───────────────────────────────────────────────────────

class AlphaSeekerCrawler:
    """
    Module 4: Alpha-Seeker Off-Chain & Social Sentiment Crawler.

    Scans social media, KOL wallets, funding rounds, and cross-chain
    contract clones to discover alpha before it materialises on-chain.
    """

    def __init__(
        self,
        bus: SignalBus,
        lake: DataLake,
        config: Optional[AlphaSeekerConfig] = None,
    ):
        self.bus = bus
        self.lake = lake
        self._cfg = config or get_config().alpha_seeker
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None

        # State
        self._sentiment_history: Dict[str, List[SentimentSignal]] = defaultdict(list)
        self._kol_activities: List[KOLActivity] = []
        self._funding_rounds: List[FundingRound] = []
        self._contract_clones: List[ContractClone] = []
        self._watchlist_assets: Set[str] = set()

        # Stats
        self._stats = {
            "sentiment_scans": 0,
            "sentiment_spikes_detected": 0,
            "kol_activities_tracked": 0,
            "funding_rounds_discovered": 0,
            "contract_clones_found": 0,
            "signals_emitted": 0,
        }

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("[AlphaSeeker] Starting off-chain intelligence crawler")

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[AlphaSeeker] Stopped")

    async def run_cycle(self):
        """Execute one full alpha-seeking cycle."""
        if not self._running:
            return

        await asyncio.gather(
            self._scan_social_sentiment(),
            self._track_kol_wallets(),
            self._discover_funding_rounds(),
            self._detect_contract_clones(),
            return_exceptions=True,
        )

    # ── 4.1 Social Sentiment & Whale Watching ────────────────

    async def _scan_social_sentiment(self):
        """Scan Twitter/social APIs for token sentiment spikes."""
        self._stats["sentiment_scans"] += 1

        if not self._session:
            return

        # Twitter API v2
        if self._cfg.twitter_bearer_token:
            try:
                headers = {"Authorization": f"Bearer {self._cfg.twitter_bearer_token}"}
                # Search for DeFi-related mentions
                search_terms = ["$ETH liquidation", "$BTC dump", "DeFi exploit",
                                "flash loan", "depeg", "airdrop", "TVL surge"]
                for term in search_terms[:3]:
                    async with self._session.get(
                        "https://api.twitter.com/2/tweets/search/recent",
                        params={"query": term, "max_results": 100},
                        headers=headers, timeout=10,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            tweets = data.get("data", [])
                            self._analyse_tweets(tweets, term)
            except Exception as exc:
                logger.debug("[AlphaSeeker] Twitter API error: %s", exc)

        # Kaito mindshare API
        if self._cfg.kaito_api_key:
            try:
                headers = {"Authorization": f"Bearer {self._cfg.kaito_api_key}"}
                async with self._session.get(
                    "https://api.kaito.ai/v1/mindshare/trending",
                    headers=headers, timeout=10,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data.get("trending", []):
                            token = item.get("symbol", "")
                            spike_ratio = item.get("change_24h", 0) / 100.0 + 1.0
                            if spike_ratio >= self._cfg.min_sentiment_spike:
                                self._emit_sentiment_signal(
                                    token, spike_ratio, "kaito_mindshare", 0.6,
                                )
            except Exception:
                pass

    def _analyse_tweets(self, tweets: List[Dict], search_term: str):
        """Analyse tweets for sentiment signals."""
        token_mentions: Dict[str, int] = defaultdict(int)
        token_sentiment: Dict[str, float] = defaultdict(float)

        for tweet in tweets:
            text = tweet.get("text", "").lower()

            # Extract token mentions (e.g., $ETH, $LINK)
            symbols = re.findall(r'\$([A-Za-z]{2,10})', tweet.get("text", ""))
            for sym in symbols:
                sym = sym.upper()
                token_mentions[sym] += 1

                # Simple sentiment scoring
                bullish_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
                bearish_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
                total = bullish_count + bearish_count
                if total > 0:
                    token_sentiment[sym] += (bullish_count - bearish_count) / total

        # Detect spikes
        for token, count in token_mentions.items():
            history = self._sentiment_history.get(token, [])
            avg_count = (sum(s.mention_count for s in history[-24:]) / max(1, len(history[-24:]))) or 1.0
            spike_ratio = count / avg_count

            signal = SentimentSignal(
                token=token,
                mention_count=count,
                sentiment_score=token_sentiment.get(token, 0.0) / max(1, count),
                volume_spike_ratio=spike_ratio,
                source="twitter",
            )
            self._sentiment_history[token].append(signal)

            if spike_ratio >= self._cfg.min_sentiment_spike:
                self._stats["sentiment_spikes_detected"] += 1
                self._watchlist_assets.add(token)
                self._emit_sentiment_signal(
                    token, spike_ratio, "twitter",
                    signal.sentiment_score,
                )

    def _emit_sentiment_signal(self, token: str, spike_ratio: float,
                                source: str, sentiment: float):
        """Emit a social sentiment signal."""
        signal = TriangulatedSignal(
            signal_type=SignalType.SOCIAL_SPIKE,
            source=SignalSource.ALPHA_SEEKER,
            chain_id=1,
            confidence=min(0.8, 0.4 + spike_ratio * 0.1),
            estimated_profit_usd=0.0,
            gas_cost_estimate_usd=0.0,
            urgency_seconds=300.0,
            target_asset=token,
            sentiment_score=sentiment,
            mention_spike_ratio=spike_ratio,
            competition_estimate=0.2,
            metadata={
                "source": source,
                "spike_ratio": spike_ratio,
                "sentiment": sentiment,
            },
        )
        self.bus.publish(signal)
        self._stats["signals_emitted"] += 1

    # ── 4.2 KOL & Mindshare Tracking ────────────────────────

    async def _track_kol_wallets(self):
        """Track KOL wallet activities via DeBank / 0xPPL."""
        if not self._session:
            return

        kol_wallets = list(self._cfg.kol_wallets)[:20]
        if not kol_wallets:
            return

        for addr in kol_wallets:
            # DeBank API
            if self._cfg.debank_api_key:
                try:
                    headers = {"AccessKey": self._cfg.debank_api_key}
                    async with self._session.get(
                        f"https://pro-openapi.debank.com/v1/user/history_list?id={addr}&chain_id=eth&page_count=5",
                        headers=headers, timeout=10,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for tx in data.get("history_list", []):
                                self._process_kol_tx(addr, tx)
                except Exception:
                    pass

            # 0xPPL API
            if self._cfg.oxppl_api_key:
                try:
                    headers = {"Authorization": f"Bearer {self._cfg.oxppl_api_key}"}
                    async with self._session.get(
                        f"https://api.0xppl.com/v1/wallets/{addr}/activity",
                        headers=headers, timeout=10,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for activity in data.get("activities", []):
                                if activity.get("type") == "contract_interaction":
                                    contract = activity.get("contract", "")
                                    if contract:
                                        kol_act = KOLActivity(
                                            kol_address=addr,
                                            action="interact",
                                            contract_address=contract,
                                            chain_id=1,
                                            is_verified_contract=activity.get("is_verified", True),
                                        )
                                        self._kol_activities.append(kol_act)
                                        self._stats["kol_activities_tracked"] += 1

                                        # Unverified contract = potential alpha
                                        if not kol_act.is_verified_contract:
                                            self._emit_kol_signal(kol_act)
                except Exception:
                    pass

    def _process_kol_tx(self, kol_addr: str, tx: Dict):
        """Process a KOL transaction from DeBank."""
        cate = tx.get("cate_id", "")
        if cate in ("approve", "receive"):
            return

        contract = tx.get("other_addr", "")
        value_usd = float(tx.get("tx", {}).get("usd_gas_fee", 0))

        kol_act = KOLActivity(
            kol_address=kol_addr,
            action=cate,
            contract_address=contract,
            chain_id=1,
            value_usd=value_usd,
        )
        self._kol_activities.append(kol_act)
        self._stats["kol_activities_tracked"] += 1

    def _emit_kol_signal(self, activity: KOLActivity):
        """Emit signal when KOL interacts with unverified contract."""
        signal = TriangulatedSignal(
            signal_type=SignalType.NEW_PROTOCOL,
            source=SignalSource.ALPHA_SEEKER,
            chain_id=activity.chain_id,
            confidence=0.55,
            estimated_profit_usd=0.0,
            gas_cost_estimate_usd=0.0,
            urgency_seconds=600.0,
            target_user=activity.kol_address,
            target_contract=activity.contract_address,
            competition_estimate=0.1,
            metadata={
                "kol_address": activity.kol_address,
                "action": activity.action,
                "is_verified": activity.is_verified_contract,
            },
        )
        self.bus.publish(signal)
        self._stats["signals_emitted"] += 1

    # ── 4.3 Funding Round Discovery ──────────────────────────

    async def _discover_funding_rounds(self):
        """Discover recently funded DeFi projects via CoinCarp."""
        if not self._session or not self._cfg.coincarp_api_key:
            return

        try:
            headers = {"Authorization": f"Bearer {self._cfg.coincarp_api_key}"}
            async with self._session.get(
                "https://api.coincarp.com/v1/fundraising/recent",
                headers=headers, timeout=10,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for round_data in data.get("data", []):
                        amount = float(round_data.get("amount_usd", 0))
                        if amount < 1_000_000:
                            continue

                        funding = FundingRound(
                            project_name=round_data.get("project", ""),
                            amount_usd=amount,
                            investors=round_data.get("investors", []),
                            category=round_data.get("category", "defi"),
                        )
                        self._funding_rounds.append(funding)
                        self._stats["funding_rounds_discovered"] += 1

                        # Watch for contract deployments from this project
                        deployer = round_data.get("deployer_address", "")
                        if deployer:
                            funding.deployer_address = deployer
                            signal = TriangulatedSignal(
                                signal_type=SignalType.NEW_PROTOCOL,
                                source=SignalSource.ALPHA_SEEKER,
                                chain_id=1,
                                confidence=0.50,
                                estimated_profit_usd=0.0,
                                gas_cost_estimate_usd=0.0,
                                urgency_seconds=86400.0,
                                target_user=deployer,
                                target_protocol=funding.project_name,
                                competition_estimate=0.1,
                                metadata={
                                    "funding_usd": amount,
                                    "investors": funding.investors[:5],
                                    "category": funding.category,
                                },
                            )
                            self.bus.publish(signal)
                            self._stats["signals_emitted"] += 1

        except Exception as exc:
            logger.debug("[AlphaSeeker] CoinCarp error: %s", exc)

    # ── 4.4 Cross-Chain Contract Mirroring ───────────────────

    async def _detect_contract_clones(self):
        """Detect cross-chain protocol clones by bytecode similarity."""
        if not self._session:
            return

        # For newly discovered contracts, check bytecode on other chains
        recent_signals = self.bus.get_recent(20)
        new_contracts = [
            s for s in recent_signals
            if s.signal_type == SignalType.NEW_PROTOCOL and s.target_contract
        ]

        for sig in new_contracts[:5]:
            original_chain = sig.chain_id
            original_addr = sig.target_contract
            original_code = await self._get_bytecode(original_chain, original_addr)
            if not original_code or len(original_code) < 100:
                continue

            # Check all mirror chains
            for chain_id in self._cfg.contract_mirror_chains:
                if chain_id == original_chain:
                    continue
                # In production: scan recent contract creations on this chain
                # and compare bytecode. For now, emit a watch signal.

            # TODO: if we find a clone, emit signal
            # This would involve scanning block explorers for similar bytecode

    async def _get_bytecode(self, chain_id: int, address: str) -> Optional[str]:
        """Get contract bytecode via Etherscan-like API."""
        if not self._session:
            return None

        api_urls = {
            1: "https://api.etherscan.io/api",
            42161: "https://api.arbiscan.io/api",
            10: "https://api-optimistic.etherscan.io/api",
            137: "https://api.polygonscan.com/api",
            8453: "https://api.basescan.org/api",
        }
        url = api_urls.get(chain_id)
        if not url:
            return None

        try:
            params = {
                "module": "proxy",
                "action": "eth_getCode",
                "address": address,
                "tag": "latest",
                "apikey": self._cfg.coincarp_api_key or "",
            }
            async with self._session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", "")
        except Exception:
            pass
        return None

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "watchlist_assets": len(self._watchlist_assets),
            "kol_activities": len(self._kol_activities),
            "funding_rounds": len(self._funding_rounds),
        }
