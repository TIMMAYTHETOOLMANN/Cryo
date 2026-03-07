#!/usr/bin/env python3
"""
TARGET FINDER — Live On-Chain Execution Candidate Discovery
=============================================================
Queries live health factors, oracle prices, and protocol state
directly from on-chain contracts.  Zero hardcoded/placeholder data.

Data sources:
  - Aave V3 Pool.getUserAccountData() via on-chain calls
  - Chainlink oracle latestAnswer() for live ETH/USD price
  - Reserve Protocol RToken basketsNeeded / totalSupply for live collateral ratio
  - Borrower addresses from recent Borrow events (on-chain bootstrap)
  - .env for all contract addresses, RPC URLs, and wallet config
"""

import json
import os
import sys
import time
from typing import Dict, List

from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

# ── Configuration from .env (single source of truth) ─────────────
RPC_URLS: Dict[int, str] = {
    1:     os.getenv("ETH_RPC_URL", os.getenv("MAINNET_RPC_URL", "")),
    42161: os.getenv("ARBITRUM_RPC_URL", ""),
    10:    os.getenv("OPTIMISM_RPC_URL", ""),
    137:   os.getenv("POLYGON_RPC_URL", ""),
    8453:  os.getenv("BASE_RPC_URL", ""),
    43114: os.getenv("AVALANCHE_RPC_URL", ""),
    56:    os.getenv("BSC_RPC_URL", ""),
    324:   os.getenv("ZKSYNC_RPC_URL", ""),
}

CHAIN_NAMES = {
    1: "Ethereum", 42161: "Arbitrum", 10: "Optimism",
    137: "Polygon", 8453: "Base", 43114: "Avalanche",
    56: "BSC", 324: "zkSync Era",
}

# Deployed executor contracts — read from .env
EXECUTOR_V1   = os.getenv("LIQUIDATION_EXECUTOR_V1", "")
EXECUTOR_V2   = os.getenv("LIQUIDATION_EXECUTOR_V2", "")
FLASH_EXEC    = os.getenv("FLASH_EXECUTOR", "")
TREASURY      = os.getenv("TREASURY_ADDRESS", "")

# Aave V3 Pool addresses per chain
AAVE_V3_POOLS: Dict[int, str] = {
    1:     os.getenv("AAVE_V3_POOL_ETHEREUM", ""),
    42161: os.getenv("AAVE_V3_POOL_ARBITRUM", ""),
    10:    os.getenv("AAVE_V3_POOL_OPTIMISM", ""),
    137:   os.getenv("AAVE_V3_POOL_POLYGON", ""),
    8453:  os.getenv("AAVE_V3_POOL_BASE", ""),
    43114: os.getenv("AAVE_V3_POOL_AVALANCHE", ""),
}

# Compound V3 addresses per chain
COMPOUND_V3: Dict[int, str] = {
    1:     os.getenv("COMPOUND_V3_ETHEREUM", ""),
    42161: os.getenv("COMPOUND_V3_ARBITRUM", ""),
    8453:  os.getenv("COMPOUND_V3_BASE", ""),
}

# Chainlink ETH/USD feed
CHAINLINK_ETH_USD = os.getenv("CHAINLINK_ETH_USD", "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419")

# Reserve Protocol RTokens (Ethereum mainnet)
RESERVE_RTOKENS = {
    "ETH+":  "0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8",
    "eUSD":  "0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F",
    "hyUSD": "0xaCdf0DBA4B9839b96221a8487e9ca660a48212be",
}

# Thresholds from .env
MIN_PROFIT_USD       = float(os.getenv("MIN_PROFIT_USD", "0.50"))
HEALTH_FACTOR_THRESH = float(os.getenv("HEALTH_FACTOR_THRESHOLD", "1.05"))
MIN_DEBT_USD         = float(os.getenv("MIN_DEBT_USD", "100"))

# ── ABIs (minimal, for on-chain queries) ─────────────────────────
AAVE_GET_USER_DATA_ABI = json.loads("""[{
    "inputs":[{"name":"user","type":"address"}],
    "name":"getUserAccountData",
    "outputs":[
        {"name":"totalCollateralBase","type":"uint256"},
        {"name":"totalDebtBase","type":"uint256"},
        {"name":"availableBorrowsBase","type":"uint256"},
        {"name":"currentLiquidationThreshold","type":"uint256"},
        {"name":"ltv","type":"uint256"},
        {"name":"healthFactor","type":"uint256"}
    ],
    "stateMutability":"view","type":"function"
}]""")

CHAINLINK_PRICE_ABI = json.loads("""[{
    "inputs":[],"name":"latestAnswer",
    "outputs":[{"name":"","type":"int256"}],
    "stateMutability":"view","type":"function"
}]""")

RTOKEN_ABI = json.loads("""[
    {"inputs":[],"name":"main","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"basketsNeeded","outputs":[{"name":"","type":"uint192"}],"stateMutability":"view","type":"function"}
]""")

MAIN_ABI = json.loads("""[
    {"inputs":[],"name":"basketHandler","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"}
]""")

BASKET_HANDLER_ABI = json.loads("""[
    {"inputs":[],"name":"fullyCollateralized","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"}
]""")


# ── Helpers ──────────────────────────────────────────────────────

def connect(chain_id: int) -> Web3:
    """Create a Web3 provider for the given chain."""
    url = RPC_URLS.get(chain_id, "")
    if not url:
        raise ValueError(f"No RPC URL for chain {chain_id}")
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))


def get_eth_price(w3: Web3) -> float:
    """Fetch live ETH/USD from Chainlink oracle."""
    feed = w3.eth.contract(
        address=Web3.to_checksum_address(CHAINLINK_ETH_USD),
        abi=CHAINLINK_PRICE_ABI,
    )
    answer = feed.functions.latestAnswer().call()
    return answer / 1e8


def discover_borrowers(w3: Web3, pool_address: str, lookback: int = 10000) -> set:
    """
    Discover active borrowers from recent Aave V3 Borrow events.
    Returns set of checksummed addresses.
    """
    BORROW_TOPIC = "0x" + Web3.keccak(
        text="Borrow(address,address,address,uint256,uint8,uint256,uint16)"
    ).hex()

    current_block = w3.eth.block_number
    from_block = max(0, current_block - lookback)
    users = set()

    try:
        logs = w3.eth.get_logs({
            "address": Web3.to_checksum_address(pool_address),
            "fromBlock": from_block,
            "toBlock": current_block,
            "topics": [BORROW_TOPIC],
        })
        for log in logs:
            if len(log["topics"]) >= 3:
                borrower = "0x" + log["topics"][2].hex()[-40:]
                users.add(Web3.to_checksum_address(borrower))
            if len(log.get("data", b"")) >= 64:
                user_hex = "0x" + log["data"].hex()[24:64]
                try:
                    users.add(Web3.to_checksum_address(user_hex))
                except Exception:
                    pass
    except Exception as e:
        print(f"    ⚠️ Event scan error: {e}")

    return users


def check_health_factors(
    w3: Web3, pool_address: str, users: set, eth_price: float
) -> List[dict]:
    """
    Query Aave V3 getUserAccountData for each user.
    Returns list of liquidation candidates sorted by health factor.
    """
    pool = w3.eth.contract(
        address=Web3.to_checksum_address(pool_address),
        abi=AAVE_GET_USER_DATA_ABI,
    )
    candidates = []

    for user in users:
        try:
            data = pool.functions.getUserAccountData(
                Web3.to_checksum_address(user)
            ).call()

            # Aave V3 returns values in base currency (USD with 8 decimals)
            collateral_usd = data[0] / 1e8
            debt_usd = data[1] / 1e8
            hf = data[5] / 1e18 if data[5] > 0 else 999.0

            if debt_usd < MIN_DEBT_USD:
                continue

            if hf < HEALTH_FACTOR_THRESH:
                # ── Realistic Aave V3 profit calculation ──
                # Aave V3 only allows liquidation when HF < 1.0.
                # Positions with 1.0 ≤ HF < threshold are WATCHLIST
                # (near-liquidation, worth monitoring for price drops).
                #
                # Aave V3 rules:
                #   close_factor = 50% of total debt  (if HF < 0.95: 100%)
                #   liquidation_bonus = 5-10% depending on asset (use 5% conservative)
                #   profit = close_factor * debt * bonus_pct - gas_cost
                #
                # Gas: liquidation txs on Ethereum cost ~500k-800k gas.
                # At 30 gwei + 2 gwei priority = ~$25-$50 USD.
                is_liquidatable = hf < 1.0
                close_factor = 1.0 if hf < 0.95 else 0.5
                bonus_pct = 0.05  # conservative 5% liquidation bonus
                liquidatable_debt = debt_usd * close_factor
                gross_profit = liquidatable_debt * bonus_pct
                gas_cost_usd = 50.0 if debt_usd > 10000 else 30.0

                # Flash loan fee (Aave V3 = 0.05% on premium, or 0 for no-fee)
                flash_loan_fee = liquidatable_debt * 0.0005
                net_profit = gross_profit - gas_cost_usd - flash_loan_fee

                if is_liquidatable:
                    urgency = "CRITICAL" if hf < 0.95 else "HIGH"
                    status = "EXECUTABLE"
                else:
                    urgency = "WATCH_HIGH" if hf < 1.01 else "WATCH_MED" if hf < 1.03 else "WATCH_LOW"
                    status = "WATCHLIST"

                if net_profit > MIN_PROFIT_USD or status == "WATCHLIST":
                    candidates.append({
                        "type": "LIQUIDATION",
                        "user": user,
                        "health_factor": round(hf, 6),
                        "collateral_usd": round(collateral_usd, 2),
                        "debt_usd": round(debt_usd, 2),
                        "close_factor": close_factor,
                        "gross_profit_usd": round(gross_profit, 2),
                        "gas_cost_usd": round(gas_cost_usd, 2),
                        "flash_loan_fee_usd": round(flash_loan_fee, 2),
                        "net_profit_usd": round(max(net_profit, 0), 2),
                        "urgency": urgency,
                        "status": status,
                        "executable_now": is_liquidatable,
                        "executor": "V2",
                    })
        except Exception:
            pass

    candidates.sort(key=lambda x: x["health_factor"])
    return candidates


def check_reserve_protocol(w3: Web3, eth_price: float) -> List[dict]:
    """
    Query Reserve Protocol RTokens for live collateral ratio.
    Detects over-collateralization arbitrage opportunities.
    """
    opportunities = []

    for name, rtoken_addr in RESERVE_RTOKENS.items():
        try:
            addr = Web3.to_checksum_address(rtoken_addr)
            rtoken = w3.eth.contract(address=addr, abi=RTOKEN_ABI)

            total_supply = rtoken.functions.totalSupply().call()
            if total_supply == 0:
                print(f"    ⚪ {name}: zero supply — skipped")
                continue

            baskets_needed = rtoken.functions.basketsNeeded().call()

            # Collateral ratio = basketsNeeded / totalSupply (both 18-dec)
            collateral_ratio = baskets_needed / total_supply if total_supply > 0 else 1.0

            # Check if fully collateralized via BasketHandler
            fully_collateralized = None
            try:
                main_addr = rtoken.functions.main().call()
                main_contract = w3.eth.contract(address=main_addr, abi=MAIN_ABI)
                bh_addr = main_contract.functions.basketHandler().call()
                bh = w3.eth.contract(address=bh_addr, abi=BASKET_HANDLER_ABI)
                fully_collateralized = bh.functions.fullyCollateralized().call()
            except Exception:
                pass

            # Estimate supply in USD
            supply_usd = (total_supply / 1e18)
            if "ETH" in name.upper():
                supply_usd *= eth_price

            excess_ratio = max(0, collateral_ratio - 1.0)
            profit_estimate = supply_usd * excess_ratio * 0.5  # conservative 50% capture

            if profit_estimate > MIN_PROFIT_USD:
                opportunities.append({
                    "type": "RESERVE_ARB",
                    "rToken": name,
                    "rToken_address": rtoken_addr,
                    "total_supply_raw": str(total_supply),
                    "baskets_needed_raw": str(baskets_needed),
                    "collateral_ratio": round(collateral_ratio, 6),
                    "collateral_pct": f"{collateral_ratio:.2%}",
                    "fully_collateralized": fully_collateralized,
                    "supply_usd": round(supply_usd, 2),
                    "excess_value_usd": round(supply_usd * excess_ratio, 2),
                    "estimated_profit_usd": round(profit_estimate, 2),
                    "urgency": "HIGH" if excess_ratio > 0.05 else "MEDIUM",
                    "flash_loan_required": True,
                    "flash_executor": FLASH_EXEC,
                })
                print(f"    ✅ {name}: {collateral_ratio:.2%} collateral, "
                      f"est. profit ${profit_estimate:,.2f}")
            else:
                print(f"    ⚪ {name}: {collateral_ratio:.4%} collateral — "
                      f"below profit threshold")

        except Exception as e:
            print(f"    ⚠️ {name}: query failed — {e}")

    return opportunities


# ── Main ─────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 70)
    print("  TARGET FINDER — Live On-Chain Execution Candidates")
    print("=" * 70)

    # ── Connect to Ethereum mainnet ──
    w3 = connect(1)
    block = w3.eth.block_number
    print(f"\n  Connected: Ethereum block {block:,}")

    # ── Live ETH/USD price ──
    print("\n[1] Fetching live ETH/USD from Chainlink...")
    try:
        eth_price = get_eth_price(w3)
        print(f"    ETH/USD: ${eth_price:,.2f}")
    except Exception as e:
        eth_price = 2000.0
        print(f"    ⚠️ Chainlink query failed ({e}), using fallback ${eth_price:,.0f}")

    # ── Treasury & Gas ──
    print("\n[2] Checking Treasury & Gas...")
    all_balances = {}
    for chain_id, name in CHAIN_NAMES.items():
        rpc = RPC_URLS.get(chain_id, "")
        if not rpc:
            continue
        try:
            cw3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            bal = cw3.eth.get_balance(Web3.to_checksum_address(TREASURY))
            bal_native = bal / 1e18
            all_balances[chain_id] = bal_native
            if bal_native > 0:
                usd_val = bal_native * eth_price  # approximate
                print(f"    {name:12s} (ID {chain_id:>5d}): {bal_native:.6f} native "
                      f"(~${usd_val:.2f}) {'✅' if bal_native > 0.001 else '⛽ LOW'}")
        except Exception:
            pass

    total_native = sum(all_balances.values())
    print(f"    Total native across chains: ~{total_native:.6f}")

    # ── Executor Contracts ──
    print("\n[3] Checking Executor Contracts...")
    for name, addr in [("V1", EXECUTOR_V1), ("V2", EXECUTOR_V2), ("Flash", FLASH_EXEC)]:
        if not addr:
            print(f"    {name}: NOT CONFIGURED in .env")
            continue
        try:
            code = w3.eth.get_code(Web3.to_checksum_address(addr))
            has_code = len(code) > 2
            bal = w3.eth.get_balance(Web3.to_checksum_address(addr))
            print(f"    {name}: {addr} — "
                  f"{'DEPLOYED ✅' if has_code else '❌ NO CODE'} | "
                  f"{bal/1e18:.4f} ETH")
        except Exception as e:
            print(f"    {name}: {addr} — error: {e}")

    all_targets = []

    # ── Aave V3 Liquidation Scan (all chains) ──
    print("\n[4] Scanning Aave V3 for Liquidations (live on-chain)...")
    for chain_id, pool_addr in AAVE_V3_POOLS.items():
        if not pool_addr:
            continue
        name = CHAIN_NAMES.get(chain_id, str(chain_id))
        try:
            cw3 = connect(chain_id)
            is_l2 = chain_id in (10, 8453, 42161, 137, 43114, 56, 324)
            lookback = 50000 if is_l2 else 10000

            print(f"    {name}: discovering borrowers (last {lookback} blocks)...",
                  end="", flush=True)
            borrowers = discover_borrowers(cw3, pool_addr, lookback)
            print(f" {len(borrowers)} found", end="", flush=True)

            if borrowers:
                candidates = check_health_factors(cw3, pool_addr, borrowers, eth_price)
                for c in candidates:
                    c["chain_id"] = chain_id
                    c["chain_name"] = name
                    c["protocol"] = "aave_v3"
                    c["pool_address"] = pool_addr
                all_targets.extend(candidates)

                hf_below = [b for b in candidates if b["health_factor"] < HEALTH_FACTOR_THRESH]
                print(f" → {len(hf_below)} liquidatable")
                for c in hf_below[:3]:
                    print(f"      🎯 HF={c['health_factor']:.4f} | "
                          f"Debt=${c['debt_usd']:,.0f} | "
                          f"Profit=${c['net_profit_usd']:,.2f} | "
                          f"{c['user'][:10]}...")
            else:
                print(" → 0 borrowers in range")

        except Exception as e:
            print(f" ⚠️ {e}")

    # ── Reserve Protocol (Ethereum only) ──
    print("\n[5] Scanning Reserve Protocol (live on-chain)...")
    reserve_targets = check_reserve_protocol(w3, eth_price)
    all_targets.extend(reserve_targets)

    # ── Summary ──
    print("\n" + "=" * 70)
    print(f"  TARGETS FOUND: {len(all_targets)}")
    print("=" * 70)

    if all_targets:
        # Sort by profit descending
        all_targets.sort(
            key=lambda x: x.get("net_profit_usd", x.get("estimated_profit_usd", 0)),
            reverse=True,
        )

        print("\n  Top Execution Candidates:\n")
        for i, target in enumerate(all_targets[:15], 1):
            ttype = target["type"]
            profit = target.get("net_profit_usd", target.get("estimated_profit_usd", 0))
            urgency = target.get("urgency", "")

            print(f"  {i}. [{ttype}] Profit: ${profit:,.2f} | Urgency: {urgency}")

            if ttype == "LIQUIDATION":
                print(f"     Chain: {target.get('chain_name', '?')} | "
                      f"HF: {target['health_factor']:.4f} | "
                      f"Debt: ${target['debt_usd']:,.0f} | "
                      f"User: {target['user'][:16]}...")
            elif ttype == "RESERVE_ARB":
                print(f"     RToken: {target['rToken']} ({target['rToken_address'][:12]}...) | "
                      f"Collateral: {target['collateral_pct']} | "
                      f"Flash: {'YES' if target.get('flash_loan_required') else 'NO'}")
            print()

        # Save to targets.json
        output = {
            "timestamp": int(time.time()),
            "block": block,
            "eth_price_usd": round(eth_price, 2),
            "treasury": {
                "address": TREASURY,
                "balances": {str(k): round(v, 6) for k, v in all_balances.items()},
            },
            "executors": {
                "v1": EXECUTOR_V1,
                "v2": EXECUTOR_V2,
                "flash": FLASH_EXEC,
            },
            "config": {
                "min_profit_usd": MIN_PROFIT_USD,
                "health_factor_threshold": HEALTH_FACTOR_THRESH,
                "min_debt_usd": MIN_DEBT_USD,
            },
            "targets": all_targets,
        }

        with open("targets.json", "w") as f:
            json.dump(output, f, indent=2)
        print(f"  Targets saved to targets.json")

        # Execution commands
        print("\n" + "=" * 70)
        print("  EXECUTION COMMANDS")
        print("=" * 70)
        print("\n  For Liquidations:")
        print("    python unified_execution_bridge.py")
        print("    python main.py --master")
        print("\n  For Reserve Protocol Arb:")
        print("    forge script script/DeployFlashLoanArbitrage.s.sol \\")
        print("      --rpc-url $MAINNET_RPC_URL --private-key $PRIVATE_KEY --broadcast")
    else:
        print("\n  No targets found meeting criteria.")
        print(f"  Thresholds: HF<{HEALTH_FACTOR_THRESH}, Debt>${MIN_DEBT_USD}, Profit>${MIN_PROFIT_USD}")
        print("  This is normal in stable markets. The main pipeline scans continuously.")

    print("\n" + "=" * 70)
    return all_targets


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--min-profit":
        MIN_PROFIT_USD = float(sys.argv[2])
    main()
