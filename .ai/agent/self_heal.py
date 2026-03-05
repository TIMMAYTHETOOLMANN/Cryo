"""
JARVIS NEXUS — Self-Healing Error Handler
Tracks consecutive errors, adapts strategy after threshold (5x identical),
provides recovery suggestions to the agent loop.
"""

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ErrorRecord:
    error_hash: str
    error_message: str
    tool_name: str
    arguments_summary: str
    timestamp: float
    attempt_number: int


@dataclass
class RecoveryStrategy:
    strategy_name: str
    instruction: str
    priority: int  # 1=highest


class SelfHealEngine:
    """
    Tracks errors and provides adaptive recovery.
    After N consecutive identical errors, injects strategy shift into agent context.
    """

    def __init__(self, identical_threshold: int = 5, max_total_retries: int = 25):
        self.identical_threshold = identical_threshold
        self.max_total_retries = max_total_retries
        self._error_history: List[ErrorRecord] = []
        self._consecutive_identical: int = 0
        self._last_error_hash: Optional[str] = None
        self._total_errors: int = 0
        self._error_hash_counts: Dict[str, int] = defaultdict(int)
        self._recovery_attempts: int = 0
        self._is_escalated: bool = False
        self._total_successes: int = 0
        self._consecutive_successes: int = 0

    @staticmethod
    def _hash_error(tool_name: str, error_msg: str) -> str:
        normalized = error_msg.strip().lower()
        normalized = re.sub(r'0x[0-9a-f]+', '<addr>', normalized)
        normalized = re.sub(r'\d{10,}', '<ts>', normalized)
        normalized = re.sub(r'line \d+', 'line <N>', normalized)
        key = f"{tool_name}::{normalized}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def record_result(self, tool_name: str, arguments: Dict, success: bool, output: str) -> Optional[RecoveryStrategy]:
        if success:
            self._consecutive_successes += 1
            self._total_successes += 1
            self._consecutive_identical = 0
            self._last_error_hash = None
            if self._consecutive_successes >= 3:
                self._recovery_attempts = 0
            return None

        self._consecutive_successes = 0
        self._total_errors += 1
        error_hash = self._hash_error(tool_name, output)
        self._error_hash_counts[error_hash] += 1

        self._error_history.append(ErrorRecord(
            error_hash=error_hash,
            error_message=output[:500],
            tool_name=tool_name,
            arguments_summary=json.dumps(arguments, default=str)[:200],
            timestamp=time.time(),
            attempt_number=self._total_errors,
        ))

        if error_hash == self._last_error_hash:
            self._consecutive_identical += 1
        else:
            self._consecutive_identical = 1
            self._last_error_hash = error_hash

        if self._total_errors >= self.max_total_retries:
            self._is_escalated = True
            return RecoveryStrategy("ESCALATE_TO_USER",
                f"MAXIMUM RETRY LIMIT ({self._total_errors} errors). STOP retrying. "
                "Present a diagnostic summary and call task_complete.", 1)

        if self._consecutive_identical >= self.identical_threshold:
            self._recovery_attempts += 1
            strategy = self._get_strategy(tool_name, output, self._recovery_attempts)
            self._consecutive_identical = 0
            return strategy

        return None

    def _get_strategy(self, tool_name: str, last_error: str, round_num: int) -> RecoveryStrategy:
        if round_num == 1:
            return RecoveryStrategy("CHANGE_PARAMETERS",
                f"ADAPTIVE RECOVERY Round {round_num}: '{tool_name}' failed "
                f"{self.identical_threshold}x identically. Error: {last_error[:200]}\n"
                "CHANGE your parameters. Re-read the file, try different paths/flags. "
                "Do NOT repeat the same call.", 2)
        elif round_num == 2:
            return RecoveryStrategy("CHANGE_APPROACH",
                f"ADAPTIVE RECOVERY Round {round_num}: Parameter changes didn't work. "
                "Use a COMPLETELY DIFFERENT tool or approach. If read_file fails, use "
                "search_text. If edit_file fails, use write_file.", 2)
        elif round_num == 3:
            return RecoveryStrategy("DECOMPOSE",
                f"ADAPTIVE RECOVERY Round {round_num}: Break into smallest steps. "
                "1) Verify environment. 2) Check file exists. 3) Read error logs. "
                "4) Try simplest possible operation.", 1)
        elif round_num == 4:
            return RecoveryStrategy("SKIP_AND_DOCUMENT",
                f"ADAPTIVE RECOVERY Round {round_num}: Skip this sub-task. "
                "Document what failed and continue with other objectives.", 1)
        else:
            return RecoveryStrategy("FINAL_ESCALATION",
                f"FINAL ESCALATION Round {round_num}: All recovery exhausted. "
                "Call task_complete with full diagnostic.", 1)

    @property
    def is_escalated(self) -> bool:
        return self._is_escalated

    def get_stats(self) -> Dict:
        return {
            "total_errors": self._total_errors,
            "total_successes": self._total_successes,
            "consecutive_identical": self._consecutive_identical,
            "recovery_attempts": self._recovery_attempts,
            "unique_errors": len(self._error_hash_counts),
            "is_escalated": self._is_escalated,
        }

    def get_error_summary(self) -> str:
        if not self._error_history:
            return "No errors."
        lines = [f"Errors: {self._total_errors} total, {len(self._error_hash_counts)} unique"]
        for eh, count in sorted(self._error_hash_counts.items(), key=lambda x: -x[1]):
            rec = next(r for r in self._error_history if r.error_hash == eh)
            lines.append(f"  [{count}x] {rec.tool_name}: {rec.error_message[:80]}")
        return "\n".join(lines)
