#!/usr/bin/env python3
"""
Blocknative Provider Implementation
Mempool data and transaction insights via Blocknative API
"""

import asyncio
import json
import time
import aiohttp
from typing import Dict, List, Optional, Any

from .base_provider import BaseMempoolProvider, ProviderConfig, MempoolTransaction, OracleUpdate, LargeSwap


class BlocknativeProvider(BaseMempoolProvider):
    """
    Blocknative Mempool API
    Provides transaction insights and mempool data
    
    API: https://docs.blocknative.com/
    """
    
    @property
    def name(self) -> str:
        return "blocknative"
    
    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
    
    async def connect(self):
        """Connect to Blocknative WebSocket"""
        ws_url = self.config.ws_endpoint or "wss://api.blocknative.com/v0"
        
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(ws_url)
        
        # Send initialization message with API key
        init_msg = {
            "action": "init",
            "version": "1",
            "blockchain": {
                "system": "ethereum",
                "network": "main",
                "account": {"address": "0x0000000000000000000000000000000000000000"}
            },
            "auth": {
                "apikey": self.config.api_key
            }
        }
        
        await self.ws.send_json(init_msg)
        
        # Wait for initialization confirmation
        response = await self.ws.receive_json()
        if response.get('status') == 'ok':
            print(f"   📡 Blocknative WebSocket: {ws_url}")
            print(f"   ✅ Blocknative initialized")
        else:
            raise Exception(f"Blocknative init failed: {response}")
    
    async def disconnect(self):
        """Close Blocknative connection"""
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
    
    async def subscribe_transactions(self):
        """Subscribe to pending transaction stream"""
        # Blocknative uses address-based subscriptions
        # Subscribe to mempool for all addresses (wide net)
        subscribe_msg = {
            "action": "mempool",
            "version": "1",
            "blockchain": {
                "system": "ethereum",
                "network": "main"
            }
        }
        
        await self.ws.send_json(subscribe_msg)
        print(f"   📡 Blocknative subscribed to mempool")
    
    async def subscribe_logs(self, contract_address: str, topics: List[str]):
        """Subscribe to specific contract address"""
        subscribe_msg = {
            "action": "config",
            "version": "1",
            "blockchain": {
                "system": "ethereum",
                "network": "main"
            },
            "address": contract_address
        }
        
        await self.ws.send_json(subscribe_msg)
    
    async def get_pending_transactions(self, limit: int = 100) -> List[MempoolTransaction]:
        """Blocknative doesn't support direct polling, use REST API"""
        try:
            url = "https://api.blocknative.com/v0/transactions"
            headers = {
                'X-API-Key': self.config.api_key,
                'Content-Type': 'application/json'
            }
            
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    transactions = []
                    
                    raw_txs = data.get('transactions', [])[:limit]
                    for raw_tx in raw_txs:
                        if raw_tx.get('status') == 'pending':
                            tx = self._parse_blocknative_tx(raw_tx)
                            transactions.append(tx)
                    
                    return transactions
        except Exception as e:
            print(f"   ⚠️  Blocknative polling error: {e}")
        
        return []
    
    async def _process_stream(self):
        """Process Blocknative transaction stream"""
        async for msg in self.ws:
            if not self.is_running:
                break
            
            receive_time = time.time()
            self._record_message()
            
            try:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_message(data, receive_time)
                
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.stats.errors += 1
                    break
                    
            except json.JSONDecodeError:
                self.stats.errors += 1
            except Exception as e:
                self.stats.errors += 1
                print(f"   ⚠️  Blocknative processing error: {e}")
    
    async def _handle_message(self, data: Dict[str, Any], receive_time: float):
        """Process Blocknative message"""
        event_type = data.get('event', {}).get('type')
        
        # Handle transaction events
        if event_type == 'pending':
            transaction = data.get('transaction', {})
            await self._handle_transaction(transaction, receive_time)
        
        # Handle mined transactions
        elif event_type == 'confirmed':
            pass  # Ignore confirmed for mempool purposes
        
        # Handle dropped transactions
        elif event_type == 'dropped':
            pass
        
        # Handle initialization
        elif data.get('status') == 'ok' and data.get('action') == 'init':
            print(f"   ✅ Blocknative connection confirmed")
    
    async def _handle_transaction(self, raw_tx: Dict[str, Any], receive_time: float):
        """Process transaction from Blocknative"""
        tx = self._parse_blocknative_tx(raw_tx)
        
        # Calculate latency
        if 'timestamp' in raw_tx:
            latency_ms = (receive_time - raw_tx['timestamp']) * 1000
            self._record_latency(latency_ms)
        else:
            self._record_latency(80)  # Blocknative avg ~80ms
        
        await self._emit_transaction(tx)
        
        # Analyze for patterns
        await self._analyze_transaction(tx, raw_tx)
    
    def _parse_blocknative_tx(self, raw_tx: Dict[str, Any]) -> MempoolTransaction:
        """Parse Blocknative-specific transaction format"""
        # Blocknative has enriched transaction data
        tx_data = raw_tx.get('transaction', raw_tx)
        
        return MempoolTransaction(
            hash=tx_data.get('hash', ''),
            from_address=tx_data.get('from', ''),
            to_address=tx_data.get('to'),
            value=int(tx_data.get('value', 0)),
            gas_price=int(tx_data.get('gasPrice', 0)),
            gas_limit=int(tx_data.get('gas', 0)),
            input_data=tx_data.get('input', '0x'),
            nonce=int(tx_data.get('nonce', 0)),
            block_number=tx_data.get('blockNumber'),
            timestamp=tx_data.get('timestamp', time.time()),
            provider=self.name,
            raw_tx=raw_tx
        )
    
    async def _analyze_transaction(self, tx: MempoolTransaction, raw_tx: Dict[str, Any]):
        """Analyze Blocknative enriched transaction data"""
        # Blocknative provides additional insights
        insights = raw_tx.get('insights', {})
        
        # Check for large value transfers
        value_usd = insights.get('valueUsd', 0)
        
        if value_usd > 100000:
            # Large transfer, emit as potential signal
            swap = LargeSwap(
                dex='unknown',
                token_in='',
                token_out='',
                amount_in=tx.value,
                amount_out=0,
                sender=tx.from_address,
                receiver=tx.to_address or '',
                tx_hash=tx.hash,
                block_number=tx.block_number or 0,
                price_impact_estimate=0.0,
                value_usd=value_usd
            )
            
            await self._emit_swap(swap)
        
        # Check for contract interactions with known protocols
        contract_type = insights.get('contractType')
        if contract_type in ['DEX', 'LENDING', 'ORACLE']:
            # This is interacting with a DeFi protocol
            pass
        
        # Check for flash loans
        if insights.get('isFlashLoan', False):
            # Flash loan detected - high priority signal
            pass
