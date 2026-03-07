# ⚠️ DEPRECATED FILES — DO NOT USE
**Moved: 2026-03-05**

These files were identified as **dead code, exact duplicates, or fragmented
earlier injection attempts** that are not imported anywhere in the live
codebase. They have been replaced by the consolidated `enhanced_modules/`
package and the canonical module implementations.

## Files Moved Here

| Original Location | Reason |
|---|---|
| `profit_engine/profit_protocol_orchestrator.py` | **Exact duplicate** of `profit_protocol_master_orchestrator.py`. Orphaned stub — not imported anywhere. |
| `profit_engine/profit_protocol_master_orchestrator.py` | **Exact duplicate** of above. Orphaned stub — not imported anywhere. |
| `profit_engine/profit_protocol_cleanup_optimizer.py` | One-off cleanup script, not a runtime module. Never imported. |
| `profit_engine/super_aggressive_profit_launcher.py` | Earlier launcher attempt with mock fallbacks. Superseded by `master_orchestrator.py`. |
| `MODULE_1_LIQUIDATION_ENGINE/profit_maximizer.py` | Mock-based launcher attempt. Superseded by `master_orchestrator.py`. |
| `MODULE_1_LIQUIDATION_ENGINE/profit_maximizer_launcher.py` | **Near-identical** to `profit_maximizer.py` above. Same mock classes. |
| `MODULE_1_LIQUIDATION_ENGINE/production_monitor.py` | Standalone monitor. Not imported. Functionality lives in `MODULE_1_LIQUIDATION_ENGINE/monitoring/profit_monitor.py` and `enhanced_modules/module_8_analytics_dashboard/`. |
| `.ai/mcp/opportunity_execution_bridge.py` | **Byte-identical** to `ultra_execution_bridge.py`. Incomplete stub. |
| `.ai/mcp/ultra_execution_bridge.py` | **Byte-identical** to above. Incomplete stub with undefined references. |

## Safe to Delete

All these files can be permanently deleted. They are preserved here only as
a safety net during the consolidation period. After verifying the system runs
correctly, this entire `_deprecated/` directory can be removed.

## Canonical Locations

The **single source of truth** for each capability is now:

| Capability | Canonical Location |
|---|---|
| System Orchestration | `master_orchestrator.py` |
| Liquidation Pipeline | `MODULE_1_LIQUIDATION_ENGINE/pipeline.py` |
| Flash Loan Aggregation | `MODULE_1_LIQUIDATION_ENGINE/src/flash_loan_providers/aggregator.py` + `enhanced_modules/module_3_flash_loan_aggregator/` |
| MEV Protection | `MODULE_1_LIQUIDATION_ENGINE/src/mev_protection/flashbots.py` + `enhanced_modules/module_7_mev_strategy/` |
| Risk Management | `MODULE_1_LIQUIDATION_ENGINE/src/risk_management/safeguards.py` + `enhanced_modules/module_6_risk_protection/` |
| Profit Analytics | `MODULE_1_LIQUIDATION_ENGINE/stage_7_analytics/analytics_engine.py` + `enhanced_modules/module_8_analytics_dashboard/` |
| Execution Bridge | `unified_execution_bridge.py` |
| Profit Engine | `profit_engine/` (all non-deprecated files) |
