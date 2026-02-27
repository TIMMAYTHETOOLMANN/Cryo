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
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Running detection scan..."

    if forge test ${VERBOSITY} --match-contract "CollateralizationDetectorTest|CrossChainDetectorTest" 2>&1; then
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Scan completed successfully."
    else
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Scan encountered errors."
    fi

    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Sleeping ${SCAN_INTERVAL}s..."
    sleep "${SCAN_INTERVAL}"
done
