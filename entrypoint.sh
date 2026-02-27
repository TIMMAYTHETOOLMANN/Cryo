#!/usr/bin/env bash
set -euo pipefail

# Load environment variables
if [ -f /app/.env ]; then
    set -a
    # shellcheck source=/dev/null
    . /app/.env
    set +a
fi

POLL_INTERVAL="${POLL_INTERVAL:-300}"

echo "=== Mainnet Arbitrage Monitor ==="
echo "Poll interval: ${POLL_INTERVAL}s"
echo "Gas price cap: ${GAS_PRICE_CAP_GWEI:-50} gwei"
echo "Min profit: ${MIN_PROFIT_WEI:-10000000000000000} wei"
echo "================================="

while true; do
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Checking for arbitrage opportunity..."

    if forge script script/MainnetExploit.s.sol:MainnetExploit \
        --rpc-url "${MAINNET_RPC_URL}" \
        --private-key "${PRIVATE_KEY}" \
        --broadcast \
        --gas-price "${GAS_PRICE_CAP_GWEI:-50}gwei" \
        -vvv 2>&1; then
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Execution completed."
    else
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] No opportunity or execution failed."
    fi

    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Sleeping ${POLL_INTERVAL}s..."
    sleep "${POLL_INTERVAL}"
done
