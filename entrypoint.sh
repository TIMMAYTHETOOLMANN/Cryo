#!/usr/bin/env bash
set -euo pipefail

# Load environment variables
if [ -f /app/.env ]; then
    set -a
    # shellcheck source=/dev/null
    . /app/.env
    set +a
fi

SCAN_INTERVAL="${SCAN_INTERVAL:-300}"
VERBOSITY="${VERBOSITY:--vvv}"
BROADCAST_FLAG=""
if [ -n "${PRIVATE_KEY:-}" ]; then
    BROADCAST_FLAG="--private-key ${PRIVATE_KEY} --broadcast"
fi

echo "=== OCDS Production Scanner ==="
echo "Scan interval: ${SCAN_INTERVAL}s"
echo "Networks: Ethereum, Arbitrum, Optimism, Polygon, Base, Avalanche, BSC, zkSync Era"
echo "================================="

while true; do
    TS="[$(date -u '+%Y-%m-%d %H:%M:%S UTC')]"

    # ------------------------------------------------------------------
    # Phase 1: Ethereum mainnet — RToken reconnaissance and execution
    # ------------------------------------------------------------------
    if [ -n "${MAINNET_RPC_URL:-}" ]; then
        echo "${TS} Running Ethereum mainnet scanner..."
        if forge script script/MainnetScanner.s.sol:MainnetScanner \
            --rpc-url "${MAINNET_RPC_URL}" \
            ${BROADCAST_FLAG} \
            ${VERBOSITY} 2>&1; then
            echo "${TS} Mainnet scan completed."
        else
            echo "${TS} Mainnet scan encountered errors — continuing."
        fi
    fi

    # ------------------------------------------------------------------
    # Phase 2: Multi-chain scanning (all chains with configured RPC URLs)
    # ------------------------------------------------------------------
    # Use an explicit ordered array to guarantee consistent scan sequence
    CHAIN_IDS=(1 42161 10 137 8453 43114 56 324)
    CHAIN_RPC_URLS=(
        "${MAINNET_RPC_URL:-}"
        "${ARBITRUM_RPC_URL:-}"
        "${OPTIMISM_RPC_URL:-}"
        "${POLYGON_RPC_URL:-}"
        "${BASE_RPC_URL:-}"
        "${AVALANCHE_RPC_URL:-}"
        "${BSC_RPC_URL:-}"
        "${ZKSYNC_RPC_URL:-}"
    )

    for idx in "${!CHAIN_IDS[@]}"; do
        CHAIN_ID="${CHAIN_IDS[$idx]}"
        RPC="${CHAIN_RPC_URLS[$idx]}"
        if [ -n "${RPC}" ]; then
            echo "${TS} Scanning chain ${CHAIN_ID}..."
            if forge script script/ScanNetworks.s.sol:ScanNetworks \
                --rpc-url "${RPC}" \
                --sig "scanSingleNetwork(uint256)" "${CHAIN_ID}" \
                ${VERBOSITY} 2>&1; then
                echo "${TS} Chain ${CHAIN_ID} scan completed."
            else
                echo "${TS} Chain ${CHAIN_ID} scan encountered errors — continuing."
            fi
        fi
    done

    # ------------------------------------------------------------------
    # Phase 3: Unit tests only (no RPC URLs configured)
    # ------------------------------------------------------------------
    if [ -z "${MAINNET_RPC_URL:-}" ]; then
        echo "${TS} No RPC URLs set — running unit tests only."
        if forge test ${VERBOSITY} --match-contract "CollateralizationDetectorTest|CrossChainDetectorTest" 2>&1; then
            echo "${TS} Unit tests passed."
        else
            echo "${TS} Unit tests FAILED."
        fi
    fi

    echo "${TS} Sleeping ${SCAN_INTERVAL}s until next cycle..."
    sleep "${SCAN_INTERVAL}"
done

