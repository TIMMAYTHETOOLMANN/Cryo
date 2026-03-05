# ... existing code ...

class EnhancedProfitEngine:
    """Enhanced with vulnerability exploitation capabilities"""
    
    def __init__(self):
        self.vulnerability_scanner = UnifiedVulnerabilityScanner(
            self.rpc_gateway, self, self.exploitation_orchestrator
        )
        self.exploitation_orchestrator = CryoExploitationOrchestrator(
            self.rpc_gateway, self.flash_loan_router, 
            self.mev_protector, self.profit_calculator
        )
    
    async def scan_and_exploit_contracts(self, contract_addresses: List[str]):
        """Scan contracts and exploit profitable vulnerabilities"""
        
        exploit_results = []
        
        for contract_address in contract_addresses:
            try:
                # Context for exploitation
                context = await self._build_exploitation_context(contract_address)
                
                # Execute scan-to-exploit pipeline
                results = await self.vulnerability_scanner.scan_and_exploit(
                    contract_address, context
                )
                
                exploit_results.extend(results)
                
            except Exception as e:
                logger.error(f"Failed to exploit {contract_address}: {e}")
        
        return self._aggregate_exploit_results(exploit_results)

# ... existing code ...
