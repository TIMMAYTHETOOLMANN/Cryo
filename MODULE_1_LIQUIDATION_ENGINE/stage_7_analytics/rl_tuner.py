#!/usr/bin/env python3
"""
STAGE 7 — Reinforcement Learning Parameter Tuner (Script 3 Enhancement #7)
============================================================================
Self-optimizing parameter tuning via Q-learning.

Dynamically adjusts per-chain thresholds:
  - MIN_DEBT_USD
  - GAS_PRICE_CAP_GWEI
  - SLIPPAGE_TOLERANCE
  - MIN_PROFIT_USD
  - HEALTH_FACTOR_THRESHOLD

The RL agent observes execution outcomes and learns which parameter
configurations maximize cumulative profit per chain/asset.

State space (discretized):
  - gas_price_bucket: low/medium/high
  - volatility_bucket: low/medium/high
  - competition_bucket: low/medium/high (based on success rate)
  - chain_bucket: by chain_id

Action space:
  - Increase/decrease each parameter by a step size
  - Hold current parameters

Reward:
  - profit_usd if execution succeeded
  - -gas_cost_usd if execution failed
  - 0 if skipped (no penalty for caution)
"""

import logging
import os
import json
from collections import defaultdict
from typing import Dict, Optional
from dataclasses import dataclass

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


@dataclass
class RLState:
    """Discretized state for Q-learning."""
    chain_id: int
    gas_bucket: str      # "low", "medium", "high"
    vol_bucket: str      # "low", "medium", "high"
    competition_bucket: str  # "low", "medium", "high"

    @property
    def key(self) -> str:
        return f"{self.chain_id}:{self.gas_bucket}:{self.vol_bucket}:{self.competition_bucket}"


@dataclass
class RLAction:
    """Parameter adjustment action."""
    param: str
    direction: str  # "increase", "decrease", "hold"

    @property
    def key(self) -> str:
        return f"{self.param}:{self.direction}"


@dataclass
class ParameterSet:
    """Tunable parameters per chain."""
    min_debt_usd: float = 50.0
    gas_price_cap_gwei: float = 5.0
    slippage_tolerance: float = 0.005
    min_profit_usd: float = 0.10
    hf_threshold: float = 1.05


# Parameter bounds (safety limits)
PARAM_BOUNDS = {
    "min_debt_usd":      (10.0, 50000.0),
    "gas_price_cap_gwei": (1.0, 500.0),
    "slippage_tolerance": (0.001, 0.05),
    "min_profit_usd":    (0.01, 500.0),
    "hf_threshold":      (1.01, 1.20),
}

PARAM_STEPS = {
    "min_debt_usd":      50.0,
    "gas_price_cap_gwei": 5.0,
    "slippage_tolerance": 0.001,
    "min_profit_usd":    1.0,
    "hf_threshold":      0.005,
}

TUNABLE_PARAMS = list(PARAM_BOUNDS.keys())

GAS_BUCKETS = [(0, 15, "low"), (15, 50, "medium"), (50, 999, "high")]
VOL_BUCKETS = [(0, 0.02, "low"), (0.02, 0.08, "medium"), (0.08, 1.0, "high")]
SUCCESS_BUCKETS = [(0.7, 1.0, "low"), (0.3, 0.7, "medium"), (0, 0.3, "high")]


def _bucket(value: float, buckets: list) -> str:
    for low, high, label in buckets:
        if low <= value < high:
            return label
    return buckets[-1][2]


class RLParameterTuner:
    """
    Q-learning agent for parameter optimization.

    Learns per-state optimal parameter configurations.
    Updates are applied gradually to avoid destabilizing the system.
    """

    def __init__(
        self,
        config: Optional[ConfigManager] = None,
        learning_rate: float = 0.1,
        discount_factor: float = 0.95,
        exploration_rate: float = 0.15,
    ):
        self.config = config or get_config()
        self.lr = learning_rate
        self.gamma = discount_factor
        self.epsilon = exploration_rate

        # Q-table: state_key → action_key → Q-value
        self._q_table: Dict[str, Dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )

        # Current parameter sets per chain
        self._params: Dict[int, ParameterSet] = {}

        # Episode tracking
        self._last_state: Optional[RLState] = None
        self._last_action: Optional[RLAction] = None
        self._episode_count = 0

        # Persistence path
        self._save_path = os.getenv(
            "RL_QTABLE_PATH",
            os.path.join(os.path.dirname(__file__), "..", ".rl_qtable.json"),
        )
        self._load_qtable()

    def get_params(self, chain_id: int) -> ParameterSet:
        """Get current tuned parameters for a chain."""
        if chain_id not in self._params:
            self._params[chain_id] = ParameterSet(
                min_debt_usd=self.config.execution.min_debt_usd,
                gas_price_cap_gwei=self.config.execution.gas_price_cap_gwei,
                slippage_tolerance=0.005,
                min_profit_usd=self.config.execution.min_profit_usd,
                hf_threshold=self.config.execution.health_factor_threshold,
            )
        return self._params[chain_id]

    def observe_and_act(
        self,
        chain_id: int,
        gas_gwei: float,
        volatility: float,
        recent_success_rate: float,
        reward: float,
    ) -> ParameterSet:
        """
        Observe environment, update Q-values, select action, return adjusted params.

        Args:
            chain_id: Which chain
            gas_gwei: Current gas price
            volatility: Current volatility index
            recent_success_rate: Success rate over last N executions
            reward: Profit from last execution (negative if failed)

        Returns:
            Updated ParameterSet for the chain
        """
        # Discretize state
        state = RLState(
            chain_id=chain_id,
            gas_bucket=_bucket(gas_gwei, GAS_BUCKETS),
            vol_bucket=_bucket(volatility, VOL_BUCKETS),
            competition_bucket=_bucket(recent_success_rate, SUCCESS_BUCKETS),
        )

        # Update Q-value for last state-action pair
        if self._last_state and self._last_action:
            old_q = self._q_table[self._last_state.key][self._last_action.key]
            # Max Q for current state
            max_future_q = max(
                self._q_table[state.key].values()
            ) if self._q_table[state.key] else 0

            new_q = old_q + self.lr * (reward + self.gamma * max_future_q - old_q)
            self._q_table[self._last_state.key][self._last_action.key] = new_q

        # Select action (epsilon-greedy)
        import random
        if random.random() < self.epsilon:
            # Explore: random param + direction
            param = random.choice(TUNABLE_PARAMS)
            direction = random.choice(["increase", "decrease", "hold"])
        else:
            # Exploit: best known action for this state
            actions = self._q_table[state.key]
            if actions:
                best_key = max(actions, key=actions.get)
                parts = best_key.split(":")
                param, direction = parts[0], parts[1]
            else:
                param = TUNABLE_PARAMS[0]
                direction = "hold"

        action = RLAction(param=param, direction=direction)

        # Apply action
        params = self.get_params(chain_id)
        self._apply_action(params, action)

        # Save state for next update
        self._last_state = state
        self._last_action = action
        self._episode_count += 1

        # Periodic save
        if self._episode_count % 50 == 0:
            self._save_qtable()
            logger.info(
                f"🧠 RL Tuner: episode {self._episode_count}, "
                f"Q-table size {sum(len(v) for v in self._q_table.values())}, "
                f"chain {chain_id} params adjusted"
            )

        return params

    @staticmethod
    def _apply_action(params: ParameterSet, action: RLAction):
        """Apply an action to the parameter set (within bounds)."""
        if action.direction == "hold":
            return

        current = getattr(params, action.param, None)
        if current is None:
            return

        step = PARAM_STEPS.get(action.param, 0)
        bounds = PARAM_BOUNDS.get(action.param, (0, float("inf")))

        if action.direction == "increase":
            new_val = min(current + step, bounds[1])
        else:
            new_val = max(current - step, bounds[0])

        setattr(params, action.param, new_val)

    def _save_qtable(self):
        """Persist Q-table to disk."""
        try:
            data = {
                sk: dict(av) for sk, av in self._q_table.items()
            }
            with open(self._save_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"Q-table save error: {e}")

    def _load_qtable(self):
        """Load Q-table from disk if exists."""
        try:
            if os.path.exists(self._save_path):
                with open(self._save_path) as f:
                    data = json.load(f)
                for sk, av in data.items():
                    for ak, qv in av.items():
                        self._q_table[sk][ak] = qv
                logger.info(f"🧠 RL Tuner: loaded Q-table ({len(data)} states)")
        except Exception as e:
            logger.debug(f"Q-table load error: {e}")

    def get_diagnostics(self) -> Dict:
        """Return RL agent diagnostics."""
        return {
            "episodes": self._episode_count,
            "q_table_states": len(self._q_table),
            "q_table_entries": sum(len(v) for v in self._q_table.values()),
            "exploration_rate": self.epsilon,
            "learning_rate": self.lr,
            "chain_params": {
                cid: {
                    "min_debt": p.min_debt_usd,
                    "gas_cap": p.gas_price_cap_gwei,
                    "slippage": p.slippage_tolerance,
                    "min_profit": p.min_profit_usd,
                    "hf_threshold": p.hf_threshold,
                }
                for cid, p in self._params.items()
            },
        }
