# ⚠️  DEPRECATED — DO NOT USE
#
# All modules from this folder have been consolidated into:
#
#     MODULE_1_LIQUIDATION_ENGINE/
#     ├── stage_0_preflight/     ← system validation
#     ├── stage_1_detection/     ← opportunity detector
#     ├── stage_2_analysis/      ← profitability calculator + risk manager
#     ├── stage_3_execution/     ← flash loan aggregator + executor + gas manager
#     ├── stage_4_mev_protection/← flashbots + bloXroute
#     ├── stage_5_cross_chain/   ← multi-chain orchestrator
#     ├── stage_6_profit_collection/ ← treasury manager
#     ├── monitoring/            ← profit monitor (replaces all old monitors)
#     ├── pipeline.py            ← master pipeline orchestrator
#     └── main.py                ← single entry point
#
# This folder is kept for reference only.
# The canonical entry point is:
#     python -m MODULE_1_LIQUIDATION_ENGINE.main
#
# Mapping of old files → new locations:
#   detector.py              → stage_1_detection/opportunity_detector.py
#   enhanced_detector.py     → stage_1_detection/opportunity_detector.py (merged)
#   calculator.py            → stage_2_analysis/profitability_calculator.py
#   mempool_sniffer.py       → stage_1_detection/ (via src/mev_protection)
#   monitor.py               → monitoring/profit_monitor.py
#   simple_monitor.py        → monitoring/profit_monitor.py (merged)
#   enhanced_monitor.py      → monitoring/profit_monitor.py (merged)
#   comprehensive_monitor.py → monitoring/profit_monitor.py (merged)
#   check_status.py          → monitoring/profit_monitor.py (status_check method)
#   autonomous_hunter.py     → pipeline.py (absorbed into main pipeline)
#   dashboard.html           → dashboard/dashboard.html
