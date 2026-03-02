#!/usr/bin/env python3
"""
KOL Wallet Tracker
Track smart money and key opinion leader wallet movements

Sources:
- 0xPPL - KOL and smart money wallet tracking
- DeBank - Portfolio and transaction history
- Nansen-style smart money labels
"""

import asyncio
import aiohttp
import time
from typing import Dict, List, Optional, Any, Callable, Awaitable, Set
from dataclasses import dataclass, field
from enum import Enum
import os


class WalletType(Enum):
    """Wallet classification"""
    KOL = "kol"  # Key opinion leader
    VC = "vc"  # Venture capital
    WHALE = "whale"  # Large holder
    TRADER = "trader"  # Active trader
    DEV = "developer"  # Protocol developer
    CONTRACT = "contract"  # Smart contract
    UNKNOWN = "unknown"


@dataclass
class WalletProfile:
    """Wallet profile and metadata"""
    address: str
    label: str
    wallet_type: WalletType
    net_worth_usd: float = 0
    total_transactions: int = 0
    first_seen: int = 0
    last_active: int = 0
    protocols_used: List[str] = field(default_factory=list)
    favorite_chains: List[int] = field(default_factory=list)
    pnl_30d: float = 0  # 30-day profit/loss
    win_rate: float = 0  # Win rate percentage
    twitter: str = ""
    discord: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class WalletTransaction:
    """Transaction from tracked wallet"""
    tx_hash: str
    wallet_address: str
    timestamp: int
    chain_id: int
    from_address: str
    to_address: str
    value_usd: float
    method: str  # Function called
    protocol: str  # Protocol interacted with
    action_type: str  # swap, add_liquidity, borrow, etc.
    gas_used: int
    gas_price: int
    status: str  # success, failed
    input_data: str = ""


@dataclass
class PositionChange:
    """Change in wallet's protocol position"""
    wallet_address: str
    protocol: str
    chain_id: int
    timestamp: int
    action: str  # enter, exit, increase, decrease
    token: str
    amount_change: float
    value_usd: float
    position_before: float
    position_after: float


class ZeroXPPL:
    """
    0xPPL API integration
    KOL and smart money wallet tracking
    https://0xppl.com/
    """

    BASE_URL = "https://api.0xppl.com/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('ZEROXPPL_API_KEY')
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
            )
        return self.session

    async def get_kol_wallets(self, category: str = "defi") -> List[WalletProfile]:
        """Get list of KOL wallets"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/kol/wallets"
        params = {'category': category}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_wallets(data.get('wallets', []))
        except Exception as e:
            print(f"   ⚠️  0xPPL KOL wallets error: {e}")

        return []

    async def get_wallet_profile(self, address: str) -> Optional[WalletProfile]:
        """Get detailed wallet profile"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/wallet/{address}"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_profile(data)
        except Exception as e:
            print(f"   ⚠️  0xPPL wallet profile error: {e}")

        return None

    async def get_recent_transactions(self, address: str, limit: int = 50) -> List[WalletTransaction]:
        """Get recent transactions for a wallet"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/wallet/{address}/transactions"
        params = {'limit': limit}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_transactions(data.get('transactions', []))
        except Exception as e:
            print(f"   ⚠️  0xPPL transactions error: {e}")

        return []

    async def get_smart_money_moves(self, hours: int = 24) -> List[PositionChange]:
        """Get smart money position changes"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/smart-money/moves"
        params = {'hours': hours}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_moves(data.get('moves', []))
        except Exception as e:
            print(f"   ⚠️  0xPPL smart money moves error: {e}")

        return []

    def _parse_wallets(self, raw_data: List[Dict]) -> List[WalletProfile]:
        """Parse KOL wallet list"""
        wallets = []

        for item in raw_data:
            wallet_type = WalletType(item.get('type', 'kol')) if item.get('type') in [w.value for w in WalletType] else WalletType.KOL

            wallets.append(WalletProfile(
                address=item.get('address', ''),
                label=item.get('label', ''),
                wallet_type=wallet_type,
                net_worth_usd=float(item.get('net_worth_usd', 0)),
                total_transactions=int(item.get('tx_count', 0)),
                twitter=item.get('twitter', ''),
                tags=item.get('tags', []),
            ))

        return wallets

    def _parse_profile(self, data: Dict) -> WalletProfile:
        """Parse detailed wallet profile"""
        return WalletProfile(
            address=data.get('address', ''),
            label=data.get('label', ''),
            wallet_type=WalletType(data.get('type', 'unknown')),
            net_worth_usd=float(data.get('net_worth_usd', 0)),
            total_transactions=int(data.get('total_transactions', 0)),
            first_seen=int(data.get('first_seen', 0)),
            last_active=int(data.get('last_active', time.time())),
            protocols_used=data.get('protocols_used', []),
            favorite_chains=data.get('favorite_chains', []),
            pnl_30d=float(data.get('pnl_30d', 0)),
            win_rate=float(data.get('win_rate', 0)),
            twitter=data.get('twitter', ''),
            discord=data.get('discord', ''),
            tags=data.get('tags', []),
        )

    def _parse_transactions(self, raw_data: List[Dict]) -> List[WalletTransaction]:
        """Parse wallet transactions"""
        txs = []

        for item in raw_data:
            txs.append(WalletTransaction(
                tx_hash=item.get('hash', ''),
                wallet_address=item.get('wallet_address', ''),
                timestamp=int(item.get('timestamp', time.time())),
                chain_id=int(item.get('chain_id', 1)),
                from_address=item.get('from', ''),
                to_address=item.get('to', ''),
                value_usd=float(item.get('value_usd', 0)),
                method=item.get('method', ''),
                protocol=item.get('protocol', ''),
                action_type=item.get('action_type', ''),
                gas_used=int(item.get('gas_used', 0)),
                gas_price=int(item.get('gas_price', 0)),
                status=item.get('status', 'success'),
                input_data=item.get('input_data', ''),
            ))

        return txs

    def _parse_moves(self, raw_data: List[Dict]) -> List[PositionChange]:
        """Parse position changes"""
        moves = []

        for item in raw_data:
            moves.append(PositionChange(
                wallet_address=item.get('wallet_address', ''),
                protocol=item.get('protocol', ''),
                chain_id=int(item.get('chain_id', 1)),
                timestamp=int(item.get('timestamp', time.time())),
                action=item.get('action', ''),
                token=item.get('token', ''),
                amount_change=float(item.get('amount_change', 0)),
                value_usd=float(item.get('value_usd', 0)),
                position_before=float(item.get('position_before', 0)),
                position_after=float(item.get('position_after', 0)),
            ))

        return moves

    async def close(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()


class DeBankAPI:
    """
    DeBank API integration
    Portfolio tracking and transaction history
    https://debank.com/
    """

    BASE_URL = "https://pro-openapi.debank.com"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('DEBANK_API_KEY')
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
            )
        return self.session

    async def get_protocol(self, address: str) -> Optional[Dict]:
        """Get wallet's protocol positions"""
        session = await self._get_session()
        url = f"{BASE_URL}/v1/user/protos"
        params = {'id': address}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
        except Exception as e:
            print(f"   ⚠️  DeBank protocol error: {e}")

        return None

    async def get_txlist(self, address: str, start_time: int = 0) -> List[WalletTransaction]:
        """Get wallet transaction list"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/v1/user/txlist"
        params = {'id': address, 'start_time': start_time}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_txlist(data)
        except Exception as e:
            print(f"   ⚠️  DeBank txlist error: {e}")

        return []

    async def get_wallets(self, id_list: List[str]) -> List[WalletProfile]:
        """Get batch wallet data"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/v1/user/batch_get"
        params = {'id': ','.join(id_list)}

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_batch_wallets(data)
        except Exception as e:
            print(f"   ⚠️  DeBank batch error: {e}")

        return []

    def _parse_txlist(self, data: List[Dict]) -> List[WalletTransaction]:
        """Parse DeBank transaction list"""
        txs = []

        for item in data:
            txs.append(WalletTransaction(
                tx_hash=item.get('id', ''),
                wallet_address=item.get('user_addr', ''),
                timestamp=int(item.get('time_at', time.time())),
                chain_id=int(item.get('chain', 1)),
                from_address=item.get('from_addr', ''),
                to_address=item.get('to_addr', ''),
                value_usd=float(item.get('tx_value', 0)),
                method=item.get('name', ''),
                protocol=item.get('project_id', ''),
                action_type=item.get('cate_id', ''),
                gas_used=int(item.get('gas_used', 0)),
                gas_price=int(item.get('gas_price', 0)),
                status='success' if item.get('status') == 1 else 'failed',
            ))

        return txs

    def _parse_batch_wallets(self, data: List[Dict]) -> List[WalletProfile]:
        """Parse batch wallet data"""
        wallets = []

        for item in data:
            wallets.append(WalletProfile(
                address=item.get('id', ''),
                label=item.get('name', ''),
                wallet_type=WalletType.UNKNOWN,
                net_worth_usd=float(item.get('usd_value', 0)),
                total_transactions=0,
                last_active=int(item.get('last_active_ts', time.time())),
                tags=item.get('tags', []),
            ))

        return wallets

    async def close(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()


class KOLWalletTracker:
    """
    Main KOL Wallet Tracker class
    Monitors smart money and KOL wallet movements
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Initialize API clients
        self.zeroppl = ZeroXPPL(self.config.get('zeroppl_api_key'))
        self.debank = DeBankAPI(self.config.get('debank_api_key'))

        # Data storage
        self._tracked_wallets: Dict[str, WalletProfile] = {}
        self._recent_transactions: List[WalletTransaction] = []
        self._position_changes: List[PositionChange] = []

        # Callbacks
        self._tx_callbacks: List[Callable[[WalletTransaction], Awaitable[None]]] = []
        self._position_callbacks: List[Callable[[PositionChange], Awaitable[None]]] = []

        # Statistics
        self.wallets_tracked = 0
        self.transactions_processed = 0
        self.alerts_triggered = 0

        print("👛 KOL Wallet Tracker initialized")

    async def start(self):
        """Start wallet tracking"""
        print("\n👛 Starting KOL Wallet Tracker...")
        self.is_running = True

        # Initial data fetch
        await self._fetch_initial_data()

        # Start background monitoring
        asyncio.create_task(self._monitor_loop())

        print("   ✅ KOL Wallet Tracker started")

    async def stop(self):
        """Stop tracking"""
        self.is_running = False

        await self.zeroppl.close()
        await self.debank.close()

        print("   👛 KOL Wallet Tracker stopped")

    async def _fetch_initial_data(self):
        """Fetch initial wallet data"""
        print("   📊 Fetching initial wallet data...")

        # Get KOL wallets
        kol_wallets = await self.zeroppl.get_kol_wallets(category="defi")

        for wallet in kol_wallets[:20]:  # Limit to top 20
            self._tracked_wallets[wallet.address] = wallet

        self.wallets_tracked = len(self._tracked_wallets)

        # Get recent smart money moves
        moves = await self.zeroppl.get_smart_money_moves(hours=24)
        self._position_changes = moves

        print(f"   👥 Tracking {self.wallets_tracked} KOL wallets")
        print(f"   📈 Recent smart money moves: {len(moves)}")

    async def _monitor_loop(self):
        """Background monitoring loop"""
        while self.is_running:
            try:
                # Check wallet transactions every minute
                await self._check_wallet_transactions()
                await self._check_smart_money_moves()
                await asyncio.sleep(60)

            except Exception as e:
                print(f"   ⚠️  Wallet Tracker error: {e}")
                await asyncio.sleep(30)

    async def _check_wallet_transactions(self):
        """Check for new transactions from tracked wallets"""
        for address in list(self._tracked_wallets.keys())[:10]:  # Limit to avoid rate limits
            txs = await self.zeroppl.get_recent_transactions(address, limit=10)

            for tx in txs:
                # Check if we've seen this transaction
                if not any(t.tx_hash == tx.tx_hash for t in self._recent_transactions):
                    self._recent_transactions.append(tx)
                    self.transactions_processed += 1

                    # Emit callback for significant transactions
                    if tx.value_usd > 10000:  # $10k+ transactions
                        await self._emit_transaction(tx)

                    # Keep only recent transactions
                    if len(self._recent_transactions) > 1000:
                        self._recent_transactions = self._recent_transactions[-1000:]

    async def _check_smart_money_moves(self):
        """Check for smart money position changes"""
        moves = await self.zeroppl.get_smart_money_moves(hours=1)

        for move in moves:
            if not any(m.wallet_address == move.wallet_address and
                      m.protocol == move.protocol and
                      m.timestamp == move.timestamp for m in self._position_changes):
                self._position_changes.append(move)
                self.alerts_triggered += 1

                # Emit callback for large position changes
                if move.value_usd > 50000:  # $50k+ position change
                    await self._emit_position_change(move)

        # Keep only recent moves
        cutoff = time.time() - 86400  # 24 hours
        self._position_changes = [m for m in self._position_changes if m.timestamp > cutoff]

    def add_wallet(self, address: str, label: str = "", wallet_type: WalletType = WalletType.KOL):
        """Add wallet to tracking list"""
        if address not in self._tracked_wallets:
            profile = WalletProfile(
                address=address,
                label=label,
                wallet_type=wallet_type,
            )
            self._tracked_wallets[address] = profile
            self.wallets_tracked += 1
            print(f"   👁️  Added {label or address[:10]}... to tracking")

    def get_tracked_wallets(self) -> List[WalletProfile]:
        """Get all tracked wallets"""
        return list(self._tracked_wallets.values())

    def get_recent_transactions(self, limit: int = 50) -> List[WalletTransaction]:
        """Get recent transactions"""
        return self._recent_transactions[-limit:]

    def get_protocol_activity(self, protocol_name: str) -> List[PositionChange]:
        """Get activity for specific protocol"""
        return [m for m in self._position_changes if protocol_name.lower() in m.protocol.lower()]

    def on_transaction(self, callback: Callable[[WalletTransaction], Awaitable[None]]):
        """Register transaction callback"""
        self._tx_callbacks.append(callback)

    def on_position_change(self, callback: Callable[[PositionChange], Awaitable[None]]):
        """Register position change callback"""
        self._position_callbacks.append(callback)

    async def _emit_transaction(self, tx: WalletTransaction):
        """Emit transaction to callbacks"""
        for callback in self._tx_callbacks:
            try:
                await callback(tx)
            except Exception as e:
                print(f"   ⚠️  Transaction callback error: {e}")

    async def _emit_position_change(self, move: PositionChange):
        """Emit position change to callbacks"""
        for callback in self._position_callbacks:
            try:
                await callback(move)
            except Exception as e:
                print(f"   ⚠️  Position callback error: {e}")

    def get_stats(self) -> Dict:
        """Get statistics"""
        return {
            'wallets_tracked': self.wallets_tracked,
            'transactions_processed': self.transactions_processed,
            'alerts_triggered': self.alerts_triggered,
            'recent_moves': len(self._position_changes),
        }
