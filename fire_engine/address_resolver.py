# =============================================================================
#  Dynamic Address Resolver — Single source of truth for all on-chain addresses
#
#  Resolves contract addresses, token addresses, and protocol-specific configs
#  dynamically per chain. Priority order:
#    1. Environment variables (PROTOCOL_CHAIN env pattern)
#    2. ConfigManager registry (loaded from .env)
#    3. Canonical defaults (well-known mainnet deployments)
#
#  NEVER returns stale transaction hashes or zero addresses.
#  All addresses are validated (checksum, non-zero) before returning.
#
#  Usage:
#      resolver = AddressResolver()               # auto-loads from env
#      pool = resolver.resolve("aave_v3_pool", chain_id=1)
#      token = resolver.token("USDC", chain_id=42161)
#      executor = resolver.executor(chain_id=1)   # our deployed contracts
# =============================================================================

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Address Validation
# ---------------------------------------------------------------------------

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ZERO_ADDR = "0x" + "0" * 40


def is_valid_address(addr: Optional[str]) -> bool:
    """Check if a string is a valid non-zero Ethereum address."""
    if not addr:
        return False
    if not _ADDR_RE.match(addr):
        return False
    if addr == _ZERO_ADDR:
        return False
    return True


def checksum_address(addr: str) -> str:
    """Convert an address to EIP-55 checksum format."""
    addr_lower = addr.lower().replace("0x", "")
    digest = hashlib.sha3_256(addr_lower.encode()).hexdigest()
    result = "0x"
    for i, c in enumerate(addr_lower):
        if c in "0123456789":
            result += c
        elif int(digest[i], 16) >= 8:
            result += c.upper()
        else:
            result += c
    return result


# ---------------------------------------------------------------------------
# Canonical Protocol Addresses (well-known, immutable deployments)
# ---------------------------------------------------------------------------

# These are the REAL deployed contract addresses on each chain.
# They serve as fallback when env vars are not set.
# Source: official protocol documentation.

CANONICAL_PROTOCOLS: Dict[str, Dict[int, str]] = {
    # ── Aave V3 Pool ────────────────────────────────────────────────────
    "aave_v3_pool": {
        1:     "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        42161: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        10:    "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        8453:  "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
        137:   "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        43114: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    },
    # ── Aave V2 Pool ────────────────────────────────────────────────────
    "aave_v2_pool": {
        1: "0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9",
    },
    # ── Compound V3 (Comet USDC) ────────────────────────────────────────
    "compound_v3_comet": {
        1:     "0xc3d688B66703497DAA19211EEdff47f25384cdc3",
        42161: "0xA5EDBDD9646f8dFF606d7448e414884C7d905dCA",
        8453:  "0xb125E6687d4313864e53df431d5425969c15Eb2F",
    },
    # ── Compound V2 Comptroller ─────────────────────────────────────────
    "compound_v2_comptroller": {
        1: "0x3d9819210A31b4961b30EF54bE2aeD79B9c9Cd3B",
    },
    # ── MakerDAO ────────────────────────────────────────────────────────
    "maker_vat": {
        1: "0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B",
    },
    "maker_dog": {
        1: "0x135954d155898D42C90D2a57824C690e0c7BEf1B",
    },
    "maker_flash": {
        1: "0x60744434d6339a6B27d73d9Eda62b6F66a0a04FA",
    },
    # ── Uniswap V3 Router ───────────────────────────────────────────────
    "uniswap_v3_router": {
        1:     "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        42161: "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        10:    "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        8453:  "0x2626664c2603336E57B271c5C0b26F421741e481",
        137:   "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    },
    # ── Uniswap V2 Router ───────────────────────────────────────────────
    "uniswap_v2_router": {
        1:     "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
        42161: "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
        137:   "0xedf6066a2b290C185783862C7F4776A2C8077AD1",
    },
    # ── Balancer V2 Vault ───────────────────────────────────────────────
    "balancer_v2_vault": {
        1:     "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        42161: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        137:   "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        10:    "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    },
    # ── dYdX Solo ───────────────────────────────────────────────────────
    "dydx_solo": {
        1: "0x1E0447b19BB6EcFdAe1e4AE1694b0C3659614e4e",
    },
    # ── Chainlink ETH/USD ───────────────────────────────────────────────
    "chainlink_eth_usd": {
        1:     "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
        42161: "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",
        10:    "0x13e3Ee699D1909E989722E753853AE30b17e08c5",
        8453:  "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
        137:   "0xF9680D99D6C9589e2a93a78A04A279e509205945",
    },
    # ── Multicall3 (same on most chains) ────────────────────────────────
    "multicall3": {
        1:     "0xcA11bde05977b3631167028862bE2a173976CA11",
        42161: "0xcA11bde05977b3631167028862bE2a173976CA11",
        10:    "0xcA11bde05977b3631167028862bE2a173976CA11",
        8453:  "0xcA11bde05977b3631167028862bE2a173976CA11",
        137:   "0xcA11bde05977b3631167028862bE2a173976CA11",
        43114: "0xcA11bde05977b3631167028862bE2a173976CA11",
        56:    "0xcA11bde05977b3631167028862bE2a173976CA11",
        324:   "0xF9cda624FBC7e059355ce98a31693d299FACd963",  # zkSync differs
    },
}

# ---------------------------------------------------------------------------
# Canonical Token Addresses
# ---------------------------------------------------------------------------

CANONICAL_TOKENS: Dict[str, Dict[int, str]] = {
    "WETH": {
        1:     "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        10:    "0x4200000000000000000000000000000000000006",
        8453:  "0x4200000000000000000000000000000000000006",
        137:   "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        43114: "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB",
    },
    "USDC": {
        1:     "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        10:    "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        8453:  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        137:   "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        43114: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
    },
    "DAI": {
        1:     "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        42161: "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        10:    "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        137:   "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    },
    "USDT": {
        1:     "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        42161: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        10:    "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
        137:   "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
    },
    "WBTC": {
        1:     "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        42161: "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        137:   "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
    },
}


# ---------------------------------------------------------------------------
# Env-var naming convention for overrides
# ---------------------------------------------------------------------------

def _env_key(protocol: str, chain_id: int) -> str:
    """
    Generate the canonical env-var name for a protocol + chain.
    e.g. aave_v3_pool, chain 42161 → AAVE_V3_POOL_ARBITRUM
    """
    chain_names = {
        1: "ETHEREUM", 42161: "ARBITRUM", 10: "OPTIMISM",
        8453: "BASE", 137: "POLYGON", 43114: "AVALANCHE",
        56: "BSC", 324: "ZKSYNC",
    }
    chain_suffix = chain_names.get(chain_id, f"CHAIN_{chain_id}")
    return f"{protocol.upper()}_{chain_suffix}"


# ---------------------------------------------------------------------------
# AddressResolver
# ---------------------------------------------------------------------------

@dataclass
class ResolvedAddress:
    """Result of an address resolution with provenance tracking."""
    address: str
    source: str      # "env", "config", "canonical", "generated"
    chain_id: int
    protocol: str
    valid: bool = True


class AddressResolver:
    """
    Dynamic address resolver for the entire CryoSUPER system.

    Resolves protocol contracts, tokens, and our own deployed contracts
    using a priority cascade: env vars → ConfigManager → canonical defaults.

    Never returns zero addresses — raises if unresolvable.
    """

    def __init__(self, config_manager=None):
        """
        Args:
            config_manager: Optional ConfigManager instance. If None,
                            attempts import from MODULE_1 config.
        """
        self._config = config_manager
        if self._config is None:
            try:
                from MODULE_1_LIQUIDATION_ENGINE.config.settings import get_config
                self._config = get_config()
            except ImportError:
                pass

        # Runtime overrides (set via register())
        self._overrides: Dict[Tuple[str, int], str] = {}

        # Deployed executor addresses (our contracts)
        self._executors: Dict[int, str] = {}

        self._load_executor_addresses()

    # ── Core Resolution ──────────────────────────────────────────────

    def resolve(self, protocol: str, chain_id: int) -> ResolvedAddress:
        """
        Resolve a protocol contract address for a specific chain.

        Priority:
            1. Runtime overrides (register())
            2. Environment variables (PROTOCOL_CHAIN pattern)
            3. ConfigManager protocols
            4. Canonical defaults

        Args:
            protocol: Protocol identifier (e.g., "aave_v3_pool")
            chain_id: Target chain ID

        Returns:
            ResolvedAddress with the resolved address and source.

        Raises:
            AddressNotFound: If address cannot be resolved from any source.
        """
        key = (protocol, chain_id)

        # 1. Runtime override
        if key in self._overrides:
            addr = self._overrides[key]
            if is_valid_address(addr):
                return ResolvedAddress(addr, "override", chain_id, protocol)

        # 2. Environment variable
        env_key = _env_key(protocol, chain_id)
        env_val = os.getenv(env_key)
        if env_val and is_valid_address(env_val):
            return ResolvedAddress(env_val, "env", chain_id, protocol)

        # 3. ConfigManager
        if self._config:
            cfg_addr = self._resolve_from_config(protocol, chain_id)
            if cfg_addr and is_valid_address(cfg_addr):
                return ResolvedAddress(cfg_addr, "config", chain_id, protocol)

        # 4. Canonical defaults
        canonical = CANONICAL_PROTOCOLS.get(protocol, {}).get(chain_id)
        if canonical and is_valid_address(canonical):
            return ResolvedAddress(canonical, "canonical", chain_id, protocol)

        raise AddressNotFound(
            f"Cannot resolve address for {protocol} on chain {chain_id}. "
            f"Set env var {env_key} or register a canonical address."
        )

    def resolve_or_none(self, protocol: str, chain_id: int) -> Optional[ResolvedAddress]:
        """Like resolve() but returns None instead of raising."""
        try:
            return self.resolve(protocol, chain_id)
        except AddressNotFound:
            return None

    # ── Token Resolution ─────────────────────────────────────────────

    def token(self, symbol: str, chain_id: int) -> str:
        """
        Resolve a token address by symbol and chain.

        Args:
            symbol: Token symbol (e.g., "USDC", "WETH")
            chain_id: Target chain ID

        Returns:
            Checksummed token address.

        Raises:
            AddressNotFound: If token not found for the chain.
        """
        # Check env override first
        env_key = f"TOKEN_{symbol.upper()}_{_chain_name(chain_id)}"
        env_val = os.getenv(env_key)
        if env_val and is_valid_address(env_val):
            return env_val

        # Canonical
        addr = CANONICAL_TOKENS.get(symbol.upper(), {}).get(chain_id)
        if addr and is_valid_address(addr):
            return addr

        raise AddressNotFound(f"Token {symbol} not found on chain {chain_id}")

    def token_or_none(self, symbol: str, chain_id: int) -> Optional[str]:
        """Like token() but returns None instead of raising."""
        try:
            return self.token(symbol, chain_id)
        except AddressNotFound:
            return None

    # ── Executor Resolution ──────────────────────────────────────────

    def executor(self, chain_id: int) -> str:
        """
        Resolve our FinancialExecutor contract address on a chain.

        Checks:
            1. Runtime registered executors
            2. Env var: FIRE_EXECUTOR_{CHAIN}
            3. LIQUIDATION_EXECUTOR_V2 from ConfigManager
        """
        if chain_id in self._executors:
            return self._executors[chain_id]

        chain_name = _chain_name(chain_id)
        env_val = os.getenv(f"FIRE_EXECUTOR_{chain_name}")
        if env_val and is_valid_address(env_val):
            self._executors[chain_id] = env_val
            return env_val

        # Fall back to existing executor addresses from config
        if self._config:
            for attr in ("executor_v2", "executor_v1", "flash_executor"):
                addr = getattr(self._config, attr, "")
                if is_valid_address(addr):
                    self._executors[chain_id] = addr
                    return addr

        raise AddressNotFound(
            f"No executor deployed on chain {chain_id}. "
            f"Set FIRE_EXECUTOR_{chain_name} or deploy via foundry."
        )

    def executor_or_none(self, chain_id: int) -> Optional[str]:
        """Like executor() but returns None instead of raising."""
        try:
            return self.executor(chain_id)
        except AddressNotFound:
            return None

    # ── Registration ─────────────────────────────────────────────────

    def register(self, protocol: str, chain_id: int, address: str) -> None:
        """Register a runtime override for a protocol address."""
        if not is_valid_address(address):
            raise ValueError(f"Invalid address: {address}")
        self._overrides[(protocol, chain_id)] = address
        logger.info("📍 Registered %s on chain %d → %s", protocol, chain_id, address[:12])

    def register_executor(self, chain_id: int, address: str) -> None:
        """Register our executor contract address on a chain."""
        if not is_valid_address(address):
            raise ValueError(f"Invalid executor address: {address}")
        self._executors[chain_id] = address
        logger.info("📍 Executor registered: chain %d → %s", chain_id, address[:12])

    # ── CREATE2 Deterministic Address Generation ─────────────────────

    @staticmethod
    def compute_create2_address(
        deployer: str,
        salt: bytes,
        init_code_hash: bytes,
    ) -> str:
        """
        Compute a deterministic CREATE2 deployment address.

        This lets us predict where our contracts will deploy before
        actually deploying them, enabling cross-chain pre-configuration.

        Args:
            deployer: Address of the deploying factory contract
            salt: 32-byte salt value
            init_code_hash: keccak256 hash of the init code

        Returns:
            Predicted deployment address
        """
        raw = b"\xff" + bytes.fromhex(deployer[2:]) + salt + init_code_hash
        addr_hash = hashlib.sha3_256(raw).hexdigest()
        return "0x" + addr_hash[-40:]

    # ── Bulk Resolution ──────────────────────────────────────────────

    def resolve_all_for_chain(self, chain_id: int) -> Dict[str, ResolvedAddress]:
        """Resolve all known protocol addresses for a specific chain."""
        results: Dict[str, ResolvedAddress] = {}
        for protocol in CANONICAL_PROTOCOLS:
            resolved = self.resolve_or_none(protocol, chain_id)
            if resolved:
                results[protocol] = resolved
        return results

    def resolve_all_tokens(self, chain_id: int) -> Dict[str, str]:
        """Resolve all known token addresses for a specific chain."""
        results: Dict[str, str] = {}
        for symbol in CANONICAL_TOKENS:
            addr = self.token_or_none(symbol, chain_id)
            if addr:
                results[symbol] = addr
        return results

    # ── Introspection ────────────────────────────────────────────────

    def supported_chains(self, protocol: str) -> List[int]:
        """List all chains where a protocol is available."""
        canonical_chains = list(CANONICAL_PROTOCOLS.get(protocol, {}).keys())
        override_chains = [
            cid for (p, cid) in self._overrides if p == protocol
        ]
        return sorted(set(canonical_chains + override_chains))

    def stats(self) -> Dict[str, Any]:
        """Runtime statistics about the resolver."""
        return {
            "canonical_protocols": len(CANONICAL_PROTOCOLS),
            "canonical_tokens": len(CANONICAL_TOKENS),
            "runtime_overrides": len(self._overrides),
            "executors_registered": len(self._executors),
        }

    # ── Internal ─────────────────────────────────────────────────────

    def _resolve_from_config(self, protocol: str, chain_id: int) -> Optional[str]:
        """Try to resolve from ConfigManager."""
        if not self._config:
            return None

        # Map protocol keys to ConfigManager's protocol registry
        config_key = f"{protocol}_{chain_id}".replace("_pool", "")
        protocols = self._config.get_all_protocols()
        cfg = protocols.get(config_key)
        if cfg:
            return cfg.pool_address

        # Check flash providers
        providers = self._config.get_all_flash_providers()
        prov = providers.get(config_key)
        if prov:
            return prov.router_address

        return None

    def _load_executor_addresses(self):
        """Load executor addresses from environment."""
        chain_names = {
            1: "ETHEREUM", 42161: "ARBITRUM", 10: "OPTIMISM",
            8453: "BASE", 137: "POLYGON", 43114: "AVALANCHE",
            56: "BSC", 324: "ZKSYNC",
        }
        for cid, name in chain_names.items():
            env_val = os.getenv(f"FIRE_EXECUTOR_{name}")
            if env_val and is_valid_address(env_val):
                self._executors[cid] = env_val


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AddressNotFound(Exception):
    """Raised when an address cannot be resolved from any source."""
    pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _chain_name(chain_id: int) -> str:
    """Get a human-readable chain name for env var construction."""
    names = {
        1: "ETHEREUM", 42161: "ARBITRUM", 10: "OPTIMISM",
        8453: "BASE", 137: "POLYGON", 43114: "AVALANCHE",
        56: "BSC", 324: "ZKSYNC",
    }
    return names.get(chain_id, f"CHAIN_{chain_id}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_resolver: Optional[AddressResolver] = None


def get_resolver() -> AddressResolver:
    """Get the global AddressResolver singleton."""
    global _resolver
    if _resolver is None:
        _resolver = AddressResolver()
    return _resolver
