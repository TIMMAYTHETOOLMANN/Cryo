#!/usr/bin/env python3
"""
Infura Provider Implementation
Mempool data via Infura WebSocket streams
"""

import asyncio
import json
import time
import websockets
from typing import Dict, List, Optional, Any
from web3 import Web3

from .base_provider import BaseMempoolProvider, ProviderConfig, MempoolTransaction, OracleUpdate, LargeSwap


class InfuraProvider(BaseMempoolProvider):
    """
    Infura WebSocket API
    Provides mempool data and event subscriptions
    
    API: https://infura.io/docs/
    """
    
    @property
    def name(self) -> str:
        return "infura"
    
    async def connect(self):
        """Connect to Infura WebSocket"""
        ws_url = self.config.ws_endpoint or f"wss://mainnet.infura.io/ws/v3/{self.config.api_key}"
        
        self.websocket = await websockets.connect(
            ws_url,
            ping_interval=30,
            ping_timeout=10
        )
        
        # Initialize Web3 for decoding
        self.w3 = Web3(Web3.WebsocketProvider(ws_url, websocket_timeout=30))
        
        print(f"   📡 Infura WebSocket: {ws_url}")
    
    async def disconnect(self):
        """Close Infura connection"""
        if hasattr(self, 'websocket') and self.websocket:
            await self.websocket.close()
        if hasattr(self, 'w3') and self.w3:
            await self.w3.provider.websocket.close()
    
    async def subscribe_transactions(self):
        """Subscribe to pending transaction stream"""
        subscribe_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": ["newPendingTransactions"]
        }
        
        await self.websocket.send(json.dumps(subscribe_msg))
        print(f"   📡 Infura subscribed to pending transactions")
    
    async def subscribe_logs(self, contract_address: str, topics: List[str]):
        """Subscribe to specific contract events"""
        subscribe_msg = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "eth_subscribe",
            "params": [
                "logs",
                {
                    "address": contract_address,
                    "topics": topics
                }
            ]
        }
        
        await self.websocket.send(json.dumps(subscribe_msg))
    
    async def get_pending_transactions(self, limit: int = 100) -> List[MempoolTransaction]:
        """Poll for pending transactions via eth_getBlockByNumber"""
        # Infura doesn't have direct mempool API, get latest pending block
        block_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "eth_getBlockByNumber",
            "params": ["pending", True]
        }
        
        await self.websocket.send(json.dumps(block_request))
        
        response = await asyncio.wait_for(
            self.websocket.recv(),
            timeout=self.config.timeout_ms / 1000
        )
        
        data = json.loads(response)
        transactions = []
        
        if 'result' in data and data['result']:
            block = data['result']
            raw_txs = block.get('transactions', [])[:limit]
            
            for raw_tx in raw_txs:
                tx = self.parse_transaction(raw_tx)
                transactions.append(tx)
        
        return transactions
    
    async def _process_stream(self):
        """Process Infura transaction stream"""
        subscription_id = None
        
        async for message in self.websocket:
            if not self.is_running:
                break
            
            receive_time = time.time()
            self._record_message()
            
            try:
                data = json.loads(message)
                
                # Handle subscription response
                if 'result' in data and 'id' in data:
                    subscription_id = data['result']
                    print(f"   ✅ Infura subscription ID: {subscription_id}")
                
                # Handle subscription notifications
                if 'method' in data and data['method'] == 'eth_subscription':
                    params = data.get('params', {})
                    result = params.get('result', {})
                    
                    # Check if transaction hash (string) or full transaction (object)
                    if isinstance(result, str):
                        # Got transaction hash, need to fetch full tx
                        await self._fetch_transaction(result, receive_time)
                    elif isinstance(result, dict):
                        # Got full transaction
                        await self._handle_transaction(result, receive_time)
                    
                    # Handle log subscriptions
                    if 'address' in result and 'topics' in result:
                        await self._handle_log(result, receive_time)
                
            except json.JSONDecodeError:
                self.stats.errors += 1
            except Exception as e:
                self.stats.errors += 1
                print(f"   ⚠️  Infura processing error: {e}")
    
    async def _fetch_transaction(self, tx_hash: str, receive_time: float):
        """Fetch full transaction details"""
        try:
            request = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "eth_getTransactionByHash",
                "params": [tx_hash]
            }
            
            await self.websocket.send(json.dumps(request))
            
            response = await asyncio.wait_for(
                self.websocket.recv(),
                timeout=self.config.timeout_ms / 1000
            )
            
            data = json.loads(response)
            
            if 'result' in data and data['result']:
                await self._handle_transaction(data['result'], receive_time)
                
        except asyncio.TimeoutError:
            self.stats.errors += 1
        except Exception as e:
            self.stats.errors += 1
    
    async def _handle_transaction(self, raw_tx: Dict[str, Any], receive_time: float):
        """Process transaction from Infura"""
        tx = self.parse_transaction(raw_tx)
        
        # Calculate latency
        if 'timestamp' in raw_tx:
            latency_ms = (receive_time - raw_tx['timestamp']) * 1000
            self._record_latency(latency_ms)
        else:
            # Use receive time as proxy
            self._record_latency(100)  # Estimate 100ms latency
        
        await self._emit_transaction(tx)
        
        # Analyze for patterns
        await self._analyze_transaction(tx)
    
    async def _handle_log(self, log_data: Dict[str, Any], receive_time: float):
        """Process log event"""
        # Similar to bloXroute implementation
        topics = log_data.get('topics', [])
        
        if len(topics) > 0:
            # Oracle update
            if topics[0] == '0x0559e1d74e6f1096eab3d8f5a5c86e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e':
                await self._handle_oracle_log(log_data)
            
            # Swap event
            elif topics[0] == '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822':
                await self._handle_swap_log(log_data)
    
    async def _handle_oracle_log(self, log_data: Dict[str, Any]):
        """Process oracle update log"""
        from eth_abi import decode
        
        try:
            topics = log_data.get('topics', [])
            data_hex = log_data.get('data', '0x')
            
            if len(topics) >= 3:
                round_id = int(topics[1].hex(), 16)
                aggregator = '0x' + topics[3][26:] if len(topics) > 3 else ''
                
                decoded = decode(['uint256'], bytes.fromhex(data_hex[2:]))
                updated_at = decoded[0]
                
                update = OracleUpdate(
                    aggregator=aggregator,
                    asset='UNKNOWN',
                    price=0,
                    round_id=round_id,
                    updated_at=updated_at,
                    block_number=int(log_data.get('blockNumber', 0)),
                    tx_hash=log_data.get('transactionHash', ''),
                    price_usd=0.0
                )
                
                await self._emit_oracle_update(update)
                
        except Exception as e:
            pass  # Silently ignore decode errors
    
    async def _handle_swap_log(self, log_data: Dict[str, Any]):
        """Process DEX swap log"""
        from eth_abi import decode
        
        try:
            topics = log_data.get('topics', [])
            data_hex = log_data.get('data', '0x')
            
            if len(topics) >= 3 and len(data_hex) > 2:
                sender = '0x' + topics[1][26:]
                receiver = '0x' + topics[2][26:]
                
                decoded = decode(['uint256', 'uint256', 'uint256', 'uint256'], bytes.fromhex(data_hex[2:]))
                amount0_in, amount1_in, amount0_out, amount1_out = decoded
                
                value_usd = (amount0_in + amount1_in) / 1e18 * 2000
                
                swap = LargeSwap(
                    dex='unknown',
                    token_in='',
                    token_out='',
                    amount_in=max(amount0_in, amount1_in),
                    amount_out=max(amount0_out, amount1_out),
                    sender=sender,
                    receiver=receiver,
                    tx_hash=log_data.get('transactionHash', ''),
                    block_number=int(log_data.get('blockNumber', 0)),
                    price_impact_estimate=0.0,
                    value_usd=value_usd
                )
                
                await self._emit_swap(swap)
                
        except Exception as e:
            pass
    
    async def _analyze_transaction(self, tx: MempoolTransaction):
        """Analyze transaction for patterns"""
        # Check for large value transfers
        value_usd = (tx.value / 1e18) * 2000
        
        if value_usd > 100000:
            # Large transfer detected
            pass
