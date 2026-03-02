#!/usr/bin/env python3
"""
Dynamic Reconnaissance Engine
Leverages ContractCrawler and StaticAnalyzer to rapidly locate new targets.
"""

import asyncio
import os
import sys
from typing import List, Dict

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from omni_channel.contract_crawler.protocol_classifier import ProtocolClassifier
from omni_channel.static_analyzer.vulnerability_scanner import VulnerabilityScanner

class DynamicRecon:
    def __init__(self):
        self.classifier = ProtocolClassifier()
        self.scanner = VulnerabilityScanner()
        self.targets = []

    async def scan_new_deployments(self, chain_id: int):
        print(f"🔍 Scanning chain {chain_id} for new protocols...")
        # In a real scenario, this would use the ContractCrawler to fetch new deployments
        # For this consolidation, we'll simulate finding a new lending protocol
        new_contracts = ["0x" + "a"*40] # Mock new deployment
        
        for contract in new_contracts:
            protocol_type = await self.classifier.classify(contract)
            print(f"   Found {protocol_type} at {contract}")
            
            if protocol_type in ["lending", "reserve"]:
                vulns = await self.scanner.scan(contract)
                if vulns:
                    print(f"   ⚠️  Vulnerabilities found: {vulns}")
                    self.targets.append({
                        'address': contract,
                        'type': protocol_type,
                        'vulns': vulns
                    })

    def get_active_targets(self) -> List[Dict]:
        return self.targets

async def main():
    recon = DynamicRecon()
    await recon.scan_new_deployments(1) # Ethereum
    print(f"Total targets located: {len(recon.get_active_targets())}")

if __name__ == "__main__":
    asyncio.run(main())
