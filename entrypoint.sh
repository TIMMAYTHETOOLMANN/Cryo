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

echo "=== OCDS Production Scanner ==="
echo "Scan interval: ${SCAN_INTERVAL}s"
echo "Networks: Ethereum, Arbitrum, Optimism, Polygon, Base, Avalanche, BSC, zkSync Era"
echo "================================="

while true; do
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Running production scan..."

    # Run mainnet scanner if RPC URL is available
    if [ -n "${MAINNET_RPC_URL:-}" ]; then
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Executing mainnet scanner..."
        BROADCAST_FLAG=""
        if [ -n "${PRIVATE_KEY:-}" ]; then
            BROADCAST_FLAG="--private-key ${PRIVATE_KEY} --broadcast"
        fi
        if forge script script/MainnetScanner.s.sol:MainnetScanner \
            --rpc-url "${MAINNET_RPC_URL}" \
            ${BROADCAST_FLAG} \
            ${VERBOSITY} 2>&1; then
            echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Mainnet scan completed."
        else
            echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Mainnet scan encountered errors."
        fi
    else
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] No MAINNET_RPC_URL set. Running unit tests only."
        if forge test ${VERBOSITY} --match-contract "CollateralizationDetectorTest|CrossChainDetectorTest" 2>&1; then
            echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Unit tests passed."
        else
            echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Unit tests failed."
        fi
    fi

    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Sleeping ${SCAN_INTERVAL}s..."
    sleep "${SCAN_INTERVAL}"
done
