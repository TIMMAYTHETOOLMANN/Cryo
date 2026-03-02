#!/usr/bin/env python3
"""
Alpha Seeker — Off-Chain & Social Sentiment Crawler
======================================================
Discovers opportunities from off-chain data before they materialize on-chain.

Capabilities:
  1. Social Sentiment Analysis — NLP on Twitter/Discord for token mentions
  2. Governance & Proposal Monitoring — detect LTV changes, parameter updates
  3. Whale Watching — track large wallet movements via on-chain analytics APIs
  4. Funding Round Tracking — new DeFi protocols from VC announcements

Data flow: Social/governance data → sentiment analysis → emit signals → DataBus
"""

import asyncio
import logging
import re
from collections import defaultdict
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from ..config import OmniScopeConfig, get_omni_config
from ..data_bus import DataBus, OpportunitySignal, SignalType, SignalSource

logger = logging.getLogger(__name__)


@dataclass
class SentimentSignal:
    """Sentiment analysis result for a token/protocol."""
    token: str
    mention_count: int
    sentiment_score: float  # -1=bearish, 0=neutral, +1=bullish
    volume_spike_ratio: float  # current / avg (>2 = spike)
    source: str  # "twitter", "discord", "telegram"
    timestamp: float = 0.0


@dataclass
class GovernanceProposal:
    """A detected governance proposal that may affect positions."""
    protocol: str
    proposal_id: str
    title: str
    summary: str
    status: str  # "pending", "active", "passed", "defeated"
    affected_assets: List[str] = field(default_factory=list)
    ltv_change: float = 0.0  # -0.05 means LTV decreasing by 5%
    liquidation_threshold_change: float = 0.0
    execution_timestamp: float = 0.0


# Simple keyword-based sentiment (production: use transformer model)
BULLISH_KEYWORDS = {
    "moon", "pump", "bullish", "ath", "breakout", "accumulate",
    "undervalued", "gem", "100x", "launch", "mainnet",
}
BEARISH_KEYWORDS = {
    "dump", "bearish", "crash", "scam", "rug", "hack", "exploit",
    "sell", "short", "overvalued", "ponzi",
}

# Governance forums to monitor
GOVERNANCE_ENDPOINTS = {
    "aave": "https://governance.aave.com/api/proposals",
    "compound": "https://api.compound.finance/api/v2/governance/proposals",
    "maker": "https://vote.makerdao.com/api/executive",
}


class AlphaSeeker:
    """
    Monitors social media and governance forums for alpha signals.
    Emits signals when sentiment spikes or governance changes risk parameters.
    """

    def __init__(self, bus: DataBus, config: Optional[OmniScopeConfig] = None):
        self.bus = bus
        self.config = config or get_omni_config()
        self._cfg = self.config.alpha_seeker
        self._running = False

        # Mention history per token (for spike detection)
        self._mention_history: Dict[str, List[int]] = defaultdict(lambda: [0] * 24)

        # Known proposals
        self._proposals: Dict[str, GovernanceProposal] = {}

        self.stats = {
            "sentiment_scans": 0,
            "sentiment_spikes_detected": 0,
            "governance_proposals_tracked": 0,
            "governance_signals_emitted": 0,
            "signals_emitted": 0,
        }

    async def start(self):
        """Start the alpha seeker."""
        self._running = True
        logger.info("🔮 Alpha Seeker starting…")

        tasks = [
            asyncio.create_task(self._sentiment_loop()),
            asyncio.create_task(self._governance_loop()),
        ]

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Sentiment Analysis
    # ------------------------------------------------------------------

    async def _sentiment_loop(self):
        """Periodically scan social feeds for sentiment signals."""
        while self._running:
            try:
                signals = await self._analyze_social_feeds()
                for sig in signals:
                    if sig.volume_spike_ratio >= self._cfg.min_sentiment_spike:
                        self._emit_sentiment_signal(sig)
                self.stats["sentiment_scans"] += 1
            except Exception as e:
                logger.debug(f"Sentiment scan error: {e}")

            await asyncio.sleep(120)  # Every 2 minutes

    async def _analyze_social_feeds(self) -> List[SentimentSignal]:
        """
        Analyze social media for token sentiment.
        In production: query Twitter API, Discord bots, Telegram scrapers.
        """
        results = []

        # Twitter API integration (requires bearer token)
        if self._cfg.twitter_bearer_token:
            # In production:
            # async with aiohttp.ClientSession() as session:
            #     headers = {"Authorization": f"Bearer {self._cfg.twitter_bearer_token}"}
            #     for token in ["AAVE", "UNI", "MKR", "COMP", ...]:
            #         resp = await session.get(
            #             f"https://api.twitter.com/2/tweets/search/recent?query={token} crypto",
            #             headers=headers
            #         )
            pass

        return results

    def analyze_text(self, text: str) -> float:
        """
        Simple keyword-based sentiment analysis.
        Returns score from -1 (bearish) to +1 (bullish).
        """
        words = set(re.findall(r'\w+', text.lower()))
        bullish = len(words & BULLISH_KEYWORDS)
        bearish = len(words & BEARISH_KEYWORDS)
        total = bullish + bearish
        if total == 0:
            return 0.0
        return (bullish - bearish) / total

    def _emit_sentiment_signal(self, sig: SentimentSignal):
        """Emit a sentiment spike signal to the DataBus."""
        self.stats["sentiment_spikes_detected"] += 1

        signal_type = SignalType.SOCIAL_SPIKE
        confidence = min(sig.volume_spike_ratio / 10.0, 0.9)

        self.bus.publish(OpportunitySignal(
            signal_type=signal_type,
            source=SignalSource.ALPHA_SEEKER,
            chain_id=1,
            confidence=confidence,
            estimated_profit_usd=100.0 * sig.volume_spike_ratio,
            gas_cost_estimate_usd=0,
            urgency_seconds=3600,
            target_asset=sig.token,
            competition_estimate=0.6,
            execution_complexity=0.2,
            metadata={
                "sentiment_score": sig.sentiment_score,
                "mention_count": sig.mention_count,
                "spike_ratio": sig.volume_spike_ratio,
                "source": sig.source,
            },
        ))
        self.stats["signals_emitted"] += 1

    # ------------------------------------------------------------------
    # Governance Monitoring
    # ------------------------------------------------------------------

    async def _governance_loop(self):
        """Monitor governance proposals for parameter changes."""
        while self._running:
            for protocol in self._cfg.governance_protocols:
                try:
                    proposals = await self._fetch_proposals(protocol)
                    for prop in proposals:
                        key = f"{protocol}:{prop.proposal_id}"
                        if key not in self._proposals:
                            self._proposals[key] = prop
                            self.stats["governance_proposals_tracked"] += 1

                            # Check if this proposal affects risk parameters
                            if prop.ltv_change != 0 or prop.liquidation_threshold_change != 0:
                                self._emit_governance_signal(prop)
                except Exception as e:
                    logger.debug(f"Governance scan error ({protocol}): {e}")

            await asyncio.sleep(300)  # Every 5 minutes

    async def _fetch_proposals(self, protocol: str) -> List[GovernanceProposal]:
        """Fetch governance proposals for a protocol."""
        # In production: query governance APIs
        # endpoint = GOVERNANCE_ENDPOINTS.get(protocol, "")
        return []

    def _emit_governance_signal(self, prop: GovernanceProposal):
        """Emit a signal when governance changes affect liquidation thresholds."""
        self.stats["governance_signals_emitted"] += 1

        self.bus.publish(OpportunitySignal(
            signal_type=SignalType.GOVERNANCE_CHANGE,
            source=SignalSource.ALPHA_SEEKER,
            chain_id=1,
            confidence=0.9 if prop.status == "passed" else 0.5,
            estimated_profit_usd=500.0,
            gas_cost_estimate_usd=0,
            urgency_seconds=3600 if prop.status == "passed" else 86400,
            target_protocol=prop.protocol,
            competition_estimate=0.3,
            execution_complexity=0.4,
            metadata={
                "proposal_id": prop.proposal_id,
                "title": prop.title,
                "ltv_change": prop.ltv_change,
                "threshold_change": prop.liquidation_threshold_change,
                "affected_assets": prop.affected_assets,
                "status": prop.status,
            },
        ))
        self.stats["signals_emitted"] += 1

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "tracked_proposals": len(self._proposals),
        }
