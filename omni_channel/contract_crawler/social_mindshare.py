#!/usr/bin/env python3
"""
Social Mindshare
Track social sentiment and protocol mentions using AI-powered tools

Sources:
- Kaito AI - Social sentiment and mindshare analytics
- Dexu AI - Protocol sentiment and trend detection
- LunarCrush - Social listening for crypto
"""

import asyncio
import aiohttp
import time
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
import os


class SentimentType(Enum):
    """Sentiment classification"""
    VERY_BULLISH = 2
    BULLISH = 1
    NEUTRAL = 0
    BEARISH = -1
    VERY_BEARISH = -2


@dataclass
class SocialMention:
    """Social media mention data"""
    source: str  # twitter, telegram, discord, reddit
    author: str
    content: str
    timestamp: int
    engagement_score: float  # likes + retweets + replies
    sentiment: SentimentType
    protocol_name: str
    url: str = ""
    is_kol: bool = False  # Is author a key opinion leader?
    follower_count: int = 0


@dataclass
class ProtocolMindshare:
    """Protocol social mindshare metrics"""
    protocol_name: str
    timestamp: int
    mention_count: int
    sentiment_score: float  # -1 to 1
    mindshare_rank: int  # rank among all protocols
    trending_score: float  # 0-100, viral coefficient
    top_topics: List[str] = field(default_factory=list)
    kol_mentions: int = 0
    social_volume_change: float = 0  # % change vs previous period


@dataclass
class TrendingProtocol:
    """Protocol that's currently trending"""
    protocol_name: str
    rank: int
    mindshare_score: float
    volume_24h: int
    volume_change_pct: float
    sentiment: SentimentType
    catalyst: str = ""  # Why is it trending?


class KaitoAI:
    """
    Kaito AI API integration
    AI-powered crypto research and sentiment analysis
    https://kaito.ai/
    """

    BASE_URL = "https://api.kaito.ai/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('KAITO_API_KEY')
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limit_delay = 0.5

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
            )
        return self.session

    async def get_mindshare(self, protocol: str, hours: int = 24) -> Optional[ProtocolMindshare]:
        """Get mindshare metrics for a protocol"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/mindshare/{protocol}"
        params = {'timeframe': f'{hours}h'}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_mindshare(data)
        except Exception as e:
            print(f"   ⚠️  Kaito API error: {e}")

        return None

    async def get_trending(self, limit: int = 20) -> List[TrendingProtocol]:
        """Get currently trending protocols"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/trending"
        params = {'limit': limit}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_trending(data.get('data', []))
        except Exception as e:
            print(f"   ⚠️  Kaito trending error: {e}")

        return []

    async def search_mentions(self, query: str, hours: int = 24) -> List[SocialMention]:
        """Search for protocol mentions"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/search"
        params = {'q': query, 'timeframe': f'{hours}h'}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_mentions(data.get('results', []))
        except Exception as e:
            print(f"   ⚠️  Kaito search error: {e}")

        return []

    async def get_kol_activity(self, protocol: str = None) -> List[SocialMention]:
        """Get KOL (key opinion leader) activity"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/kol/activity"
        params = {}

        if protocol:
            params['protocol'] = protocol

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    mentions = self._parse_mentions(data.get('mentions', []))
                    # Filter for KOLs only
                    return [m for m in mentions if m.is_kol]
        except Exception as e:
            print(f"   ⚠️  Kaito KOL activity error: {e}")

        return []

    def _parse_mindshare(self, data: Dict) -> ProtocolMindshare:
        """Parse mindshare data"""
        return ProtocolMindshare(
            protocol_name=data.get('protocol', ''),
            timestamp=int(data.get('timestamp', time.time())),
            mention_count=int(data.get('mention_count', 0)),
            sentiment_score=float(data.get('sentiment_score', 0)),
            mindshare_rank=int(data.get('rank', 0)),
            trending_score=float(data.get('trending_score', 0)),
            top_topics=data.get('top_topics', []),
            kol_mentions=int(data.get('kol_mentions', 0)),
            social_volume_change=float(data.get('volume_change', 0))
        )

    def _parse_trending(self, raw_data: List[Dict]) -> List[TrendingProtocol]:
        """Parse trending protocols"""
        protocols = []

        for item in raw_data:
            sentiment_str = item.get('sentiment', 'neutral').upper()
            sentiment = SentimentType[sentiment_str] if sentiment_str in SentimentType.__members__ else SentimentType.NEUTRAL

            protocols.append(TrendingProtocol(
                protocol_name=item.get('name', ''),
                rank=int(item.get('rank', 0)),
                mindshare_score=float(item.get('score', 0)),
                volume_24h=int(item.get('volume_24h', 0)),
                volume_change_pct=float(item.get('change_pct', 0)),
                sentiment=sentiment,
                catalyst=item.get('catalyst', '')
            ))

        return protocols

    def _parse_mentions(self, raw_data: List[Dict]) -> List[SocialMention]:
        """Parse social mentions"""
        mentions = []

        for item in raw_data:
            sentiment_str = item.get('sentiment', 'neutral').upper()
            sentiment = SentimentType[sentiment_str] if sentiment_str in SentimentType.__members__ else SentimentType.NEUTRAL

            mentions.append(SocialMention(
                source=item.get('source', 'twitter'),
                author=item.get('author', ''),
                content=item.get('content', ''),
                timestamp=int(item.get('timestamp', time.time())),
                engagement_score=float(item.get('engagement', 0)),
                sentiment=sentiment,
                protocol_name=item.get('protocol', ''),
                url=item.get('url', ''),
                is_kol=item.get('is_kol', False),
                follower_count=int(item.get('followers', 0))
            ))

        return mentions

    async def close(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()


class DexuAI:
    """
    Dexu AI API integration
    Protocol sentiment and trend detection
    """

    BASE_URL = "https://api.dexu.ai/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('DEXU_API_KEY')
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
            )
        return self.session

    async def get_protocol_sentiment(self, protocol: str) -> Optional[ProtocolMindshare]:
        """Get sentiment data for a protocol"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/sentiment/{protocol}"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_sentiment(data)
        except Exception as e:
            print(f"   ⚠️  Dexu API error: {e}")

        return None

    async def get_emerging_protocols(self, limit: int = 10) -> List[TrendingProtocol]:
        """Get emerging protocols before they trend"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/emerging"
        params = {'limit': limit}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_emerging(data.get('protocols', []))
        except Exception as e:
            print(f"   ⚠️  Dexu emerging error: {e}")

        return []

    async def get_alerts(self, min_score: float = 0.7) -> List[Dict]:
        """Get sentiment alerts for significant changes"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/alerts"
        params = {'min_score': min_score}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('alerts', [])
        except Exception as e:
            print(f"   ⚠️  Dexu alerts error: {e}")

        return []

    def _parse_sentiment(self, data: Dict) -> ProtocolMindshare:
        """Parse sentiment data"""
        return ProtocolMindshare(
            protocol_name=data.get('protocol', ''),
            timestamp=int(data.get('updated_at', time.time())),
            mention_count=int(data.get('mentions_24h', 0)),
            sentiment_score=float(data.get('sentiment_score', 0)),
            mindshare_rank=int(data.get('rank', 0)),
            trending_score=float(data.get('momentum_score', 0)),
            top_topics=data.get('trending_topics', []),
            kol_mentions=int(data.get('influencer_mentions', 0)),
            social_volume_change=float(data.get('volume_change_pct', 0))
        )

    def _parse_emerging(self, raw_data: List[Dict]) -> List[TrendingProtocol]:
        """Parse emerging protocols"""
        protocols = []

        for item in raw_data:
            protocols.append(TrendingProtocol(
                protocol_name=item.get('name', ''),
                rank=int(item.get('rank', 0)),
                mindshare_score=float(item.get('score', 0)),
                volume_24h=int(item.get('social_volume', 0)),
                volume_change_pct=float(item.get('growth_rate', 0)),
                sentiment=SentimentType.BULLISH,  # Emerging = bullish by default
                catalyst=item.get('narrative', '')
            ))

        return protocols

    async def close(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()


class SocialMindshare:
    """
    Main Social Mindshare class
    Aggregates data from Kaito AI, Dexu AI, and other sources
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Initialize API clients
        self.kaito = KaitoAI(self.config.get('kaito_api_key'))
        self.dexu = DexuAI(self.config.get('dexu_api_key'))

        # Data storage
        self._trending: List[TrendingProtocol] = []
        self._watchlist: Dict[str, ProtocolMindshare] = {}  # Protocols to monitor
        self._kol_activity: List[SocialMention] = []

        # Callbacks
        self._trending_callbacks: List[Callable[[TrendingProtocol], Awaitable[None]]] = []
        self._spike_callbacks: List[Callable[[ProtocolMindshare], Awaitable[None]]] = []

        # Statistics
        self.protocols_tracked = 0
        self.alerts_triggered = 0

        print("📱 Social Mindshare initialized")

    async def start(self):
        """Start social mindshare monitoring"""
        print("\n📱 Starting Social Mindshare...")
        self.is_running = True

        # Initial data fetch
        await self._fetch_initial_data()

        # Start background monitoring
        asyncio.create_task(self._monitor_loop())

        print("   ✅ Social Mindshare started")

    async def stop(self):
        """Stop monitoring"""
        self.is_running = False

        await self.kaito.close()
        await self.dexu.close()

        print("   📱 Social Mindshare stopped")

    async def _fetch_initial_data(self):
        """Fetch initial social data"""
        print("   📊 Fetching initial social data...")

        # Get trending protocols
        self._trending = await self.kaito.get_trending(limit=20)

        # Get emerging protocols from Dexu
        emerging = await self.dexu.get_emerging_protocols(limit=10)
        self._trending.extend(emerging)

        # Auto-watchlist top trending
        for protocol in self._trending[:10]:
            await self.add_to_watchlist(protocol.protocol_name)

        self.protocols_tracked = len(self._watchlist)

        print(f"   📈 Tracking {self.protocols_tracked} protocols")
        print(f"   🔥 Top trending: {self._trending[0].protocol_name if self._trending else 'N/A'}")

    async def _monitor_loop(self):
        """Background monitoring loop"""
        while self.is_running:
            try:
                # Check for trending changes every 2 minutes
                await self._check_trending_changes()
                await self._check_kol_activity()
                await asyncio.sleep(120)

            except Exception as e:
                print(f"   ⚠️  Social Mindshare error: {e}")
                await asyncio.sleep(60)

    async def _check_trending_changes(self):
        """Check for changes in trending protocols"""
        new_trending = await self.kaito.get_trending(limit=20)

        # Find new entries in top 10
        old_top_10 = {p.protocol_name for p in self._trending[:10]}
        new_top_10 = {p.protocol_name for p in new_trending[:10]}

        # New protocols entering top 10
        new_entries = new_top_10 - old_top_10
        for protocol_name in new_entries:
            protocol = next((p for p in new_trending if p.protocol_name == protocol_name), None)
            if protocol:
                await self._emit_trending(protocol)

        # Check for sentiment spikes in watchlist
        for protocol_name in self._watchlist:
            mindshare = await self.kaito.get_mindshare(protocol_name, hours=1)
            if mindshare and mindshare.trending_score > 80:  # Spike threshold
                await self._emit_spike(mindshare)

        self._trending = new_trending

    async def _check_kol_activity(self):
        """Check for KOL mentions of watchlisted protocols"""
        for protocol_name in list(self._watchlist.keys())[:5]:  # Limit to avoid rate limits
            kol_mentions = await self.kaito.get_kol_activity(protocol_name)

            for mention in kol_mentions:
                if mention.engagement_score > 1000:  # High-engagement KOL
                    self._kol_activity.append(mention)
                    # Could emit callback here

        # Keep only recent KOL activity
        cutoff = time.time() - 3600  # 1 hour
        self._kol_activity = [m for m in self._kol_activity if m.timestamp > cutoff]

    async def add_to_watchlist(self, protocol_name: str):
        """Add protocol to watchlist"""
        if protocol_name not in self._watchlist:
            mindshare = await self.kaito.get_mindshare(protocol_name, hours=24)
            if mindshare:
                self._watchlist[protocol_name] = mindshare
                self.protocols_tracked += 1
                print(f"   👁️  Added {protocol_name} to social watchlist")

    def get_watchlist(self) -> List[ProtocolMindshare]:
        """Get current watchlist"""
        return list(self._watchlist.values())

    def get_trending(self) -> List[TrendingProtocol]:
        """Get current trending protocols"""
        return self._trending

    def get_defi_trending(self) -> List[TrendingProtocol]:
        """Get DeFi-specific trending protocols"""
        defi_keywords = ['defi', 'lending', 'dex', 'yield', 'liquid', 'borrow', 'stake']

        return [
            p for p in self._trending
            if any(k in p.catalyst.lower() or k in p.protocol_name.lower() for k in defi_keywords)
        ]

    def on_trending(self, callback: Callable[[TrendingProtocol], Awaitable[None]]):
        """Register callback for new trending protocols"""
        self._trending_callbacks.append(callback)

    def on_spike(self, callback: Callable[[ProtocolMindshare], Awaitable[None]]):
        """Register callback for sentiment spikes"""
        self._spike_callbacks.append(callback)

    async def _emit_trending(self, protocol: TrendingProtocol):
        """Emit trending protocol to callbacks"""
        for callback in self._trending_callbacks:
            try:
                await callback(protocol)
            except Exception as e:
                print(f"   ⚠️  Trending callback error: {e}")

    async def _emit_spike(self, mindshare: ProtocolMindshare):
        """Emit sentiment spike to callbacks"""
        self.alerts_triggered += 1
        for callback in self._spike_callbacks:
            try:
                await callback(mindshare)
            except Exception as e:
                print(f"   ⚠️  Spike callback error: {e}")

    def get_stats(self) -> Dict:
        """Get statistics"""
        return {
            'protocols_tracked': self.protocols_tracked,
            'trending_count': len(self._trending),
            'alerts_triggered': self.alerts_triggered,
            'kol_activity_count': len(self._kol_activity),
        }
