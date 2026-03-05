 #!/usr/bin/env python3
"""
CRYOSUPER PRODUCTION MONITOR - Live Mainnet Performance Tracking
Real-time verification of profit extraction across all active strategies
"""

import asyncio
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import requests
from web3 import Web3
import os
import sys

@dataclass
class ProductionMetrics:
    """Real-time production metrics for live monitoring"""
    timestamp: int
    active_strategies: int
    total_profit_usd: float
    successful_executions: int
    failed_executions: int
    execution_success_rate: float
    avg_profit_per_trade: float
    gas_costs_eth: float
    net_profit_usd: float
    uptime_minutes: float
    chain_health: Dict[int, bool]  # chain_id -> is_healthy
    
    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "active_strategies": self.active_strategies,
            "total_profit_usd": round(self.total_profit_usd, 2),
            "successful_executions": self.successful_executions,
            "failed_executions": self.failed_executions,
            "execution_success_rate": round(self.execution_success_rate, 4),
            "avg_profit_per_trade": round(self.avg_profit_per_trade, 2),
            "gas_costs_eth": round(self.gas_costs_eth, 6),
            "net_profit_usd": round(self.net_profit_usd, 2),
            "uptime_minutes": round(self.uptime_minutes, 2),
            "chain_health": self.chain_health
        }

class ProductionVerificationMonitor:
    """Mainnet production verification and monitoring system"""
    
    def __init__(self, config_file="production_config.json"):
        self.is_running = False
        self.start_time = time.time()
        self.metrics_history: List[ProductionMetrics] = []
        self.strategy_performance: Dict[str, Dict] = {}
        self.chain_connections: Dict[int, Web3] = {}
        self.execution_log: List[Dict] = []
        
        # Load production configuration
        self.config = self._load_config(config_file)
        self._initialize_chain_connections()
    
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        """Load production configuration"""
        default_config = {
            "monitoring_interval": 30,  # seconds
            "profit_threshold_alert": 1000,  # USD
            "success_rate_alert": 0.8,  # 80%
            "chains_to_monitor": [1, 42161, 10, 137, 8453],  # Mainnet chains
            "rpc_timeout": 10,
            "max_history_hours": 24,
            "alert_webhook": None
        }
        
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    user_config = json.load(f)
                    return {**default_config, **user_config}
        except Exception as e:
            print(f"⚠️  Config load error: {e}, using defaults")
        
        return default_config
    
    def _initialize_chain_connections(self):
        """Initialize Web3 connections for all monitored chains"""
        rpc_endpoints = {
            1: os.getenv('ETH_MAINNET_RPC', 'https://eth.llamarpc.com'),
            42161: os.getenv('ARB_MAINNET_RPC', 'https://arb1.arbitrum.io/rpc'),
            10: os.getenv('OP_MAINNET_RPC', 'https://mainnet.optimism.io'),
            137: os.getenv('POLYGON_RPC', 'https://polygon-rpc.com'),
            8453: os.getenv('BASE_RPC', 'https://mainnet.base.org'),
        }
        
        for chain_id in self.config["chains_to_monitor"]:
            if chain_id in rpc_endpoints:
                try:
                    self.chain_connections[chain_id] = Web3(
                        Web3.HTTPProvider(
                            rpc_endpoints[chain_id],
                            request_kwargs={'timeout': self.config["rpc_timeout"]}
                        )
                    )
                    # Test connection
                    block = self.chain_connections[chain_id].eth.block_number
                    print(f"✅ Chain {chain_id} connected (block: {block})")
                except Exception as e:
                    print(f"❌ Chain {chain_id} connection failed: {e}")
    
    def record_execution(self, strategy: str, profit_usd: float, 
                        gas_cost_eth: float, success: bool, chain_id: int):
        """Record execution result for monitoring"""
        execution_record = {
            "timestamp": int(time.time()),
            "strategy": strategy,
            "profit_usd": profit_usd,
            "gas_cost_eth": gas_cost_eth,
            "success": success,
            "chain_id": chain_id
        }
        
        self.execution_log.append(execution_record)
        
        # Update strategy performance
        if strategy not in self.strategy_performance:
            self.strategy_performance[strategy] = {
                "total_executions": 0,
                "successful_executions": 0,
                "total_profit_usd": 0,
                "total_gas_cost_eth": 0
            }
        
        perf = self.strategy_performance[strategy]
        perf["total_executions"] += 1
        perf["total_profit_usd"] += profit_usd if success else 0
        perf["total_gas_cost_eth"] += gas_cost_eth
        if success:
            perf["successful_executions"] += 1
    
    def get_current_metrics(self) -> ProductionMetrics:
        """Calculate current production metrics"""
        current_time = time.time()
        uptime_minutes = (current_time - self.start_time) / 60
        
        # Calculate totals from execution log
        successful_executions = sum(1 for e in self.execution_log if e["success"])
        failed_executions = len(self.execution_log) - successful_executions
        total_profit = sum(e["profit_usd"] for e in self.execution_log if e["success"])
        total_gas_cost = sum(e["gas_cost_eth"] for e in self.execution_log)
        
        # Calculate success rate (avoid division by zero)
        success_rate = (successful_executions / len(self.execution_log)) if self.execution_log else 0
        
        # Average profit per successful trade
        avg_profit = (total_profit / successful_executions) if successful_executions else 0
        
        # Estimate net profit (profit - gas costs in USD, assuming $2000/ETH)
        eth_price = 2000  # You might want to fetch this from an oracle
        net_profit = total_profit - (total_gas_cost * eth_price)
        
        # Check chain health
        chain_health = {}
        for chain_id, w3 in self.chain_connections.items():
            try:
                block = w3.eth.block_number
                chain_health[chain_id] = block > 0
            except:
                chain_health[chain_id] = False
        
        return ProductionMetrics(
            timestamp=int(current_time),
            active_strategies=len(self.strategy_performance),
            total_profit_usd=total_profit,
            successful_executions=successful_executions,
            failed_executions=failed_executions,
            execution_success_rate=success_rate,
            avg_profit_per_trade=avg_profit,
            gas_costs_eth=total_gas_cost,
            net_profit_usd=net_profit,
            uptime_minutes=uptime_minutes,
            chain_health=chain_health
        )
    
    def generate_production_report(self) -> Dict[str, Any]:
        """Generate comprehensive production report"""
        metrics = self.get_current_metrics()
        
        report = {
            "system_status": "ACTIVE" if self.is_running else "STOPPED",
            "current_metrics": metrics.to_dict(),
            "strategy_performance": {},
            "alerts": [],
            "recommendations": []
        }
        
        # Strategy performance breakdown
        for strategy, perf in self.strategy_performance.items():
            rate = (perf["successful_executions"] / perf["total_executions"]) if perf["total_executions"] else 0
            report["strategy_performance"][strategy] = {
                "success_rate": round(rate, 4),
                "total_profit_usd": round(perf["total_profit_usd"], 2),
                "avg_profit_per_trade": round(
                    perf["total_profit_usd"] / perf["successful_executions"] if perf["successful_executions"] else 0, 2
                ),
                "execution_count": perf["total_executions"]
            }
        
        # Generate alerts
        if metrics.execution_success_rate < self.config["success_rate_alert"]:
            report["alerts"].append({
                "level": "WARNING",
                "message": f"Low success rate: {metrics.execution_success_rate:.1%}",
                "recommendation": "Review strategy parameters or reduce aggression"
            })
        
        if metrics.net_profit_usd < -100:  # Negative net profit
            report["alerts"].append({
                "level": "CRITICAL", 
                "message": f"Negative net profit: ${metrics.net_profit_usd:.2f}",
                "recommendation": "Immediate strategy adjustment needed"
            })
        
        # Check chain health
        unhealthy_chains = [cid for cid, healthy in metrics.chain_health.items() if not healthy]
        if unhealthy_chains:
            report["alerts"].append({
                "level": "WARNING",
                "message": f"Unhealthy chains: {unhealthy_chains}",
                "recommendation": "Check RPC endpoints or switch providers"
            })
        
        return report
    
    async def start_monitoring(self):
        """Start the production monitoring loop"""
        self.is_running = True
        print("🚀 Starting CRYOSUPER Production Monitoring...")
        
        while self.is_running:
            try:
                # Generate and log current metrics
                metrics = self.get_current_metrics()
                self.metrics_history.append(metrics)
                
                # Keep history within limit
                max_records = self.config["max_history_hours"] * 120  # 2 records per hour
                if len(self.metrics_history) > max_records:
                    self.metrics_history = self.metrics_history[-max_records:]
                
                # Print status update
                if len(self.execution_log) > 0:
                    print(f"📊 Production Status | "
                          f"Profit: ${metrics.net_profit_usd:,.2f} | "
                          f"Success: {metrics.execution_success_rate:.1%} | "
                          f"Uptime: {metrics.uptime_minutes:.1f}m")
                
                # Generate alerts if thresholds crossed
                report = self.generate_production_report()
                if report["alerts"]:
                    for alert in report["alerts"]:
                        print(f"🚨 {alert['level']}: {alert['message']}")
                
                # Send webhook notification if configured
                if self.config.get("alert_webhook") and report["alerts"]:
                    await self._send_webhook_alert(report["alerts"])
                
                await asyncio.sleep(self.config["monitoring_interval"])
                
            except Exception as e:
                print(f"❌ Monitoring error: {e}")
                await asyncio.sleep(10)  # Retry after shorter delay
    
    async def _send_webhook_alert(self, alerts: List[Dict]):
        """Send alert to webhook (Slack/Discord/etc)"""
        try:
            if self.config["alert_webhook"]:
                payload = {
                    "text": f"CRYOSUPER Production Alert",
                    "alerts": alerts,
                    "timestamp": datetime.now().isoformat()
                }
                requests.post(self.config["alert_webhook"], json=payload, timeout=5)
        except Exception as e:
            print(f"⚠️  Webhook alert failed: {e}")
    
    def stop_monitoring(self):
        """Stop the monitoring loop"""
        self.is_running = False
        print("🛑 Production monitoring stopped")

# Enhanced Profit Maximizer with Production Monitoring
class ProductionProfitMaximizer:
    """Production-ready profit maximizer with integrated monitoring"""
    
    def __init__(self):
        self.monitor = ProductionVerificationMonitor()
        self.strategies = {}
        self.monitor_thread = None
    
    async def launch_with_monitoring(self):
        """Launch strategies with production monitoring"""
        # Start monitoring in background
        self.monitor_thread = asyncio.create_task(self.monitor.start_monitoring())
        
        # Launch your strategies here
        # This would integrate with your existing ProfitMaximizer class
        
        print("🎯 Production deployment active with real-time monitoring")
        
        return True

# Example usage
async def main():
    """Example of production monitoring in action"""
    maximizer = ProductionProfitMaximizer()
    
    # Simulate some production activity
    monitor = maximizer.monitor
    
    # Record simulated executions (replace with real strategy calls)
    monitor.record_execution("liquidation_storm", 150.50, 0.0015, True, 1)
    monitor.record_execution("arbitrage_network", 45.25, 0.0008, True, 42161)
    monitor.record_execution("flash_loan_assault", 320.75, 0.0021, True, 1)
    monitor.record_execution("mev_hunter", 28.30, 0.0009, False, 10)  # Failed execution
    
    # Start monitoring
    await maximizer.launch_with_monitoring()
    
    # Let it run for a bit to see monitoring in action
    await asyncio.sleep(60)
    
    # Generate final report
    report = monitor.generate_production_report()
    print("\n📋 FINAL PRODUCTION REPORT:")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
