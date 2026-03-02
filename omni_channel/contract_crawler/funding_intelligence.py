#!/usr/bin/env python3
"""
Funding Intelligence
Track newly funded DeFi projects and investor activity

Sources:
- Coincarp API - New funding rounds
- Crypto Fundraising API - Seed/Private rounds
- Crunchbase API - Venture funding data
"""

import asyncio
import aiohttp
import time
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
import os


class FundingStage(Enum):
    """Funding round stages"""
    SEED = "seed"
    PRIVATE = "private"
    PRE_SEED = "pre_seed"
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C = "series_c"
    STRATEGIC = "strategic"
    GRANT = "grant"


@dataclass
class FundingRound:
    """Funding round data"""
    project_name: str
    amount_usd: float
    stage: FundingStage
    date: int  # timestamp
    investors: List[str] = field(default_factory=list)
    lead_investor: Optional[str] = None
    protocol_category: str = ""  # lending, dex, derivatives, etc.
    chain: str = ""  # target blockchain
    website: str = ""
    twitter: str = ""
    discord: str = ""
    github: str = ""
    team_wallets: List[str] = field(default_factory=list)
    investor_wallets: List[str] = field(default_factory=list)
    description: str = ""
    source: str = ""  # which API provided this


@dataclass
class InvestorProfile:
    """VC/Investor profile"""
    name: str
    type: str  # vc, angel, dao, accelerator
    portfolio: List[str] = field(default_factory=list)
    total_investments: int = 0
    recent_investments: List[FundingRound] = field(default_factory=list)
    wallet_addresses: List[str] = field(default_factory=list)
    focus_areas: List[str] = field(default_factory=list)  # DeFi, NFT, Infrastructure
    check_size_min: float = 0
    check_size_max: float = 0


class CoincarpAPI:
    """
    Coincarp API integration for funding data
    https://coincarp.com/
    """

    BASE_URL = "https://api.coincarp.com/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('COINCARP_API_KEY')
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limit_delay = 1.0  # 1 request per second
        self._last_request = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def _rate_limit(self):
        """Apply rate limiting"""
        now = time.time()
        if now - self._last_request < self.rate_limit_delay:
            await asyncio.sleep(self.rate_limit_delay - (now - self._last_request))
        self._last_request = time.time()

    async def get_recent_funding(self, days: int = 30, limit: int = 100) -> List[FundingRound]:
        """Get recent funding rounds"""
        await self._rate_limit()

        session = await self._get_session()
        url = f"{self.BASE_URL}/funding/recent"
        params = {'days': days, 'limit': limit}

        if self.api_key:
            params['api_key'] = self.api_key

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_funding_data(data.get('data', []))
        except Exception as e:
            print(f"   ⚠️  Coincarp API error: {e}")

        return []

    async def get_project_by_name(self, name: str) -> Optional[Dict]:
        """Get project details by name"""
        await self._rate_limit()

        session = await self._get_session()
        url = f"{self.BASE_URL}/project/search"
        params = {'q': name}

        if self.api_key:
            params['api_key'] = self.api_key

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get('data', [])
                    return results[0] if results else None
        except Exception as e:
            print(f"   ⚠️  Coincarp project search error: {e}")

        return None

    def _parse_funding_data(self, raw_data: List[Dict]) -> List[FundingRound]:
        """Parse raw funding data"""
        rounds = []

        for item in raw_data:
            try:
                stage_map = {
                    'seed': FundingStage.SEED,
                    'private': FundingStage.PRIVATE,
                    'pre-seed': FundingStage.PRE_SEED,
                    'series a': FundingStage.SERIES_A,
                    'series b': FundingStage.SERIES_B,
                    'strategic': FundingStage.STRATEGIC,
                }

                stage_str = item.get('stage', '').lower()
                stage = stage_map.get(stage_str, FundingStage.PRIVATE)

                round = FundingRound(
                    project_name=item.get('project_name', ''),
                    amount_usd=float(item.get('amount_usd', 0)),
                    stage=stage,
                    date=int(item.get('date', time.time())),
                    investors=item.get('investors', []),
                    lead_investor=item.get('lead_investor'),
                    protocol_category=item.get('category', ''),
                    chain=item.get('chain', ''),
                    website=item.get('website', ''),
                    twitter=item.get('twitter', ''),
                    description=item.get('description', ''),
                    source='coincarp'
                )
                rounds.append(round)
            except Exception as e:
                print(f"   ⚠️  Parse error: {e}")

        return rounds

    async def close(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()


class FundraisingAPI:
    """
    Crypto Fundraising API
    Tracks seed and private round investments
    """

    BASE_URL = "https://api.cryptofundraising.io/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('FUNDRAISING_API_KEY')
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limit_delay = 0.5

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get_rounds(self, stage: FundingStage = None, limit: int = 50) -> List[FundingRound]:
        """Get funding rounds with optional stage filter"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/rounds"
        params = {'limit': limit}

        if stage:
            params['stage'] = stage.value
        if self.api_key:
            params['api_key'] = self.api_key

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_rounds(data.get('data', []))
        except Exception as e:
            print(f"   ⚠️  FundraisingAPI error: {e}")

        return []

    async def get_investors(self) -> List[InvestorProfile]:
        """Get list of active investors"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/investors"

        if self.api_key:
            params = {'api_key': self.api_key}

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_investors(data.get('data', []))
        except Exception as e:
            print(f"   ⚠️  FundraisingAPI investors error: {e}")

        return []

    def _parse_rounds(self, raw_data: List[Dict]) -> List[FundingRound]:
        """Parse funding round data"""
        rounds = []

        for item in raw_data:
            try:
                stage_str = item.get('round_type', 'private').lower()
                stage = FundingStage(stage_str) if stage_str in [s.value for s in FundingStage] else FundingStage.PRIVATE

                round = FundingRound(
                    project_name=item.get('company_name', ''),
                    amount_usd=float(item.get('amount_raised_usd', 0)),
                    stage=stage,
                    date=int(item.get('announced_date', time.time())),
                    investors=[i.get('name') for i in item.get('investors', [])],
                    lead_investor=item.get('lead_investor', {}).get('name'),
                    protocol_category=item.get('industry', ''),
                    description=item.get('short_description', ''),
                    source='fundraising_api'
                )
                rounds.append(round)
            except Exception as e:
                print(f"   ⚠️  Parse error: {e}")

        return rounds

    def _parse_investors(self, raw_data: List[Dict]) -> List[InvestorProfile]:
        """Parse investor data"""
        profiles = []

        for item in raw_data:
            profile = InvestorProfile(
                name=item.get('name', ''),
                type=item.get('type', 'vc'),
                portfolio=item.get('portfolio_companies', []),
                total_investments=int(item.get('total_investments', 0)),
                focus_areas=item.get('focus_areas', []),
                check_size_min=float(item.get('min_check_size', 0)),
                check_size_max=float(item.get('max_check_size', 0)),
            )
            profiles.append(profile)

        return profiles

    async def close(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()


class FundingIntelligence:
    """
    Main Funding Intelligence class
    Aggregates data from multiple funding data sources
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Initialize API clients
        self.coincarp = CoincarpAPI(self.config.get('coincarp_api_key'))
        self.fundraising = FundraisingAPI(self.config.get('fundraising_api_key'))

        # Data storage
        self._funding_rounds: List[FundingRound] = []
        self._investors: Dict[str, InvestorProfile] = {}
        self._watchlist: Dict[str, FundingRound] = {}  # Projects to monitor

        # Callbacks
        self._new_round_callbacks: List[Callable[[FundingRound], Awaitable[None]]] = []

        # Statistics
        self.rounds_discovered = 0
        self.investors_tracked = 0
        self.projects_watchlisted = 0

        print("💰 Funding Intelligence initialized")

    async def start(self):
        """Start funding intelligence monitoring"""
        print("\n💰 Starting Funding Intelligence...")
        self.is_running = True

        # Initial data fetch
        await self._fetch_initial_data()

        # Start background monitoring
        asyncio.create_task(self._monitor_loop())

        print("   ✅ Funding Intelligence started")

    async def stop(self):
        """Stop monitoring"""
        self.is_running = False

        await self.coincarp.close()
        await self.fundraising.close()

        print("   💰 Funding Intelligence stopped")

    async def _fetch_initial_data(self):
        """Fetch initial funding data"""
        print("   📊 Fetching initial funding data...")

        # Get recent rounds
        rounds = await self.coincarp.get_recent_funding(days=30, limit=100)
        self._funding_rounds.extend(rounds)

        # Get investor data
        investors = await self.fundraising.get_investors()
        for inv in investors:
            self._investors[inv.name] = inv

        self.rounds_discovered = len(self._funding_rounds)
        self.investors_tracked = len(self._investors)

        print(f"   📈 Discovered {self.rounds_discovered} funding rounds")
        print(f"   👥 Tracking {self.investors_tracked} investors")

    async def _monitor_loop(self):
        """Background monitoring loop"""
        while self.is_running:
            try:
                # Check for new rounds every 5 minutes
                await self._check_new_rounds()
                await asyncio.sleep(300)

            except Exception as e:
                print(f"   ⚠️  Funding Intelligence error: {e}")
                await asyncio.sleep(60)

    async def _check_new_rounds(self):
        """Check for new funding rounds"""
        # Get most recent rounds
        new_rounds = await self.coincarp.get_recent_funding(days=1, limit=20)

        for round in new_rounds:
            # Check if we've seen this before
            if not any(r.project_name == round.project_name and r.date == round.date
                      for r in self._funding_rounds):
                self._funding_rounds.append(round)
                self.rounds_discovered += 1

                # Emit callback
                await self._emit_new_round(round)

                # Auto-watchlist if significant funding
                if round.amount_usd >= 1000000:  # $1M+
                    self.add_to_watchlist(round)

    def add_to_watchlist(self, round: FundingRound):
        """Add project to watchlist"""
        self._watchlist[round.project_name] = round
        self.projects_watchlisted += 1
        print(f"   👁️  Added {round.project_name} to watchlist (${round.amount_usd:,.0f} {round.stage.value})")

    def get_watchlist(self) -> List[FundingRound]:
        """Get current watchlist"""
        return list(self._watchlist.values())

    def get_recent_rounds(self, days: int = 7, min_amount: float = 0) -> List[FundingRound]:
        """Get recent funding rounds"""
        cutoff = time.time() - (days * 86400)

        return [
            r for r in self._funding_rounds
            if r.date >= cutoff and r.amount_usd >= min_amount
        ]

    def get_investor_activity(self, investor_name: str) -> List[FundingRound]:
        """Get recent activity for specific investor"""
        return [
            r for r in self._funding_rounds
            if investor_name in r.investors
        ]

    def get_defi_rounds(self) -> List[FundingRound]:
        """Get DeFi-specific funding rounds"""
        defi_keywords = ['defi', 'lending', 'dex', 'amm', 'yield', 'liquid', 'borrow']

        return [
            r for r in self._funding_rounds
            if any(k in r.protocol_category.lower() for k in defi_keywords)
        ]

    def on_new_round(self, callback: Callable[[FundingRound], Awaitable[None]]):
        """Register callback for new funding rounds"""
        self._new_round_callbacks.append(callback)

    async def _emit_new_round(self, round: FundingRound):
        """Emit new round to callbacks"""
        for callback in self._new_round_callbacks:
            try:
                await callback(round)
            except Exception as e:
                print(f"   ⚠️  Funding callback error: {e}")

    def get_stats(self) -> Dict:
        """Get statistics"""
        return {
            'rounds_discovered': self.rounds_discovered,
            'investors_tracked': self.investors_tracked,
            'projects_watchlisted': self.projects_watchlisted,
            'recent_defi_rounds': len(self.get_defi_rounds()),
        }
