"""
JARVIS NEXUS - Autonomous Execution Controller
Governs unlimited autonomous operation with:
  - NO artificial tool-call ceilings (truly unlimited execution)
  - Forward-thinking pre-validation before every action
  - Approval gates ONLY at deployment-critical moments
  - Proactive troubleshooting pipeline (predict -> prevent -> fix)
  - Auto-continuation without nudging
"""

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ApprovalLevel(str, Enum):
    NONE     = "none"
    INFORM   = "inform"
    APPROVE  = "approve"


_CRITICAL_PATTERNS = [
    # ---- APPROVE: full stop, wait for human ----
    (r"deploy.*mainnet", ApprovalLevel.APPROVE, "Mainnet deployment"),
    (r"deploy.*prod", ApprovalLevel.APPROVE, "Production deployment"),
    (r"forge\s+create.*--rpc", ApprovalLevel.APPROVE, "Contract deployment to live network"),
    (r"hardhat\s+deploy.*--network\s+(?!localhost|hardhat|local)", ApprovalLevel.APPROVE, "Non-local deploy"),
    (r"cast\s+send.*--rpc-url", ApprovalLevel.APPROVE, "Live blockchain transaction"),
    (r"transfer.*mainnet", ApprovalLevel.APPROVE, "Mainnet fund transfer"),
    (r"--value\s+\d+.*--rpc", ApprovalLevel.APPROVE, "Transaction with value on live network"),
    (r"git\s+push.*origin\s+(main|master|prod)", ApprovalLevel.APPROVE, "Push to main/production branch"),
    (r"npm\s+publish", ApprovalLevel.APPROVE, "NPM package publish"),
    (r"docker\s+push", ApprovalLevel.APPROVE, "Docker image push to registry"),
    (r"DROP\s+(DATABASE|TABLE)", ApprovalLevel.APPROVE, "Database drop"),
    (r"rm\s+-rf\s+/", ApprovalLevel.APPROVE, "Root filesystem deletion"),
    # ---- INFORM: log prominently but keep going ----
    (r"git\s+push", ApprovalLevel.INFORM, "Git push (non-production)"),
    (r"docker\s+build", ApprovalLevel.INFORM, "Docker build"),
    (r"pip\s+install", ApprovalLevel.INFORM, "Package installation"),
    (r"forge\s+build", ApprovalLevel.INFORM, "Solidity compilation"),
]


def classify_action(command: str) -> Tuple[ApprovalLevel, str]:
    """Classify a command for approval requirements."""
    for pattern, level, reason in _CRITICAL_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return level, reason
    return ApprovalLevel.NONE, ""


@dataclass
class PreflightCheck:
    """A proactive check to run before executing an action."""
    check_name: str
    description: str
    tool_to_run: str
    tool_args: Dict[str, Any]
    critical: bool = False


@dataclass
class TroubleshootResult:
    """Result of forward-thinking analysis."""
    proceed: bool
    approval_required: bool
    approval_reason: str = ""
    risk_level: str = "low"
    preflight_checks: List[PreflightCheck] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    auto_fixes: List[str] = field(default_factory=list)


class ForwardThinkingTroubleshooter:
    """
    Proactive error prevention. Before any tool call, analyzes:
    1. Is this action safe to run autonomously?
    2. What could go wrong?
    3. What checks should run FIRST to prevent failure?
    4. If it fails, what is the automatic recovery plan?
    """

    def __init__(self):
        self._recent_failures: List[Dict] = []
        self._file_state_cache: Dict[str, float] = {}

    def analyze_before_execute(
        self, tool_name: str, arguments: Dict[str, Any],
    ) -> TroubleshootResult:
        """Analyze a planned tool call BEFORE executing it."""
        result = TroubleshootResult(proceed=True, approval_required=False)

        # ---- Approval gates (terminal commands) ----
        if tool_name == "run_terminal":
            cmd = arguments.get("command", "")
            level, reason = classify_action(cmd)
            if level == ApprovalLevel.APPROVE:
                result.proceed = False
                result.approval_required = True
                result.approval_reason = reason
                result.risk_level = "critical"
                return result
            elif level == ApprovalLevel.INFORM:
                result.warnings.append(f"Notable action: {reason}")
                result.risk_level = "medium"

        # ---- File operation pre-checks ----
        if tool_name == "edit_file":
            path = arguments.get("path", "")
            old_str = arguments.get("old_string", "")
            result.preflight_checks.append(PreflightCheck(
                check_name="verify_file_current",
                description=f"Read {path} to verify old_string still matches",
                tool_to_run="read_file",
                tool_args={"path": path},
                critical=True,
            ))
            if len(old_str) < 20:
                result.warnings.append(
                    "old_string is very short - include 3-5 context lines for unique match"
                )
                result.risk_level = "medium"

        elif tool_name == "write_file":
            path = arguments.get("path", "")
            if path.endswith(".py"):
                result.auto_fixes.append(f"After writing: check_errors(path='{path}')")
            elif path.endswith(".sol"):
                result.auto_fixes.append("After writing: run_terminal(command='forge build')")

        elif tool_name == "delete_file":
            path = arguments.get("path", "")
            result.risk_level = "medium"
            fname = path.split("/")[-1] if "/" in path else path
            result.preflight_checks.append(PreflightCheck(
                check_name="check_references",
                description=f"Search for references to {fname} before deleting",
                tool_to_run="search_text",
                tool_args={"query": fname},
                critical=True,
            ))

        # ---- Pattern-based failure prediction ----
        similar = [f for f in self._recent_failures if f.get("tool") == tool_name]
        if len(similar) >= 2:
            result.warnings.append(
                f"{tool_name} has failed {len(similar)} times recently. "
                "Consider a different approach."
            )
            result.risk_level = "high" if len(similar) >= 3 else "medium"

        return result

    def record_failure(self, tool_name: str, arguments: Dict, error: str):
        """Record a failure for pattern detection."""
        self._recent_failures.append({
            "tool": tool_name,
            "args": str(arguments)[:200],
            "error": error[:300],
            "time": time.time(),
        })
        if len(self._recent_failures) > 20:
            self._recent_failures = self._recent_failures[-20:]

    def record_success(self, tool_name: str):
        """Record success - clears failure patterns for this tool."""
        self._recent_failures = [
            f for f in self._recent_failures if f.get("tool") != tool_name
        ]

    def get_auto_continuation_prompt(
        self, last_result: Dict, task_progress: str,
    ) -> str:
        """
        Generate continuation prompt that keeps the agent working
        without needing user nudging. This is the key to sustained
        autonomous operation.
        """
        success = last_result.get("success", False)
        output = last_result.get("output", "")

        if success:
            return (
                f"Step succeeded. {task_progress}\n"
                "CONTINUE to the next step immediately. "
                "Do NOT stop or ask for permission.\n"
                "If the overall task is fully complete, call task_complete.\n"
                "Otherwise, execute your next tool call NOW."
            )

        error_lower = output.lower()
        suggestions = []
        if "modulenotfounderror" in error_lower or "importerror" in error_lower:
            suggestions.append("Install missing package with pip install")
        if "filenotfounderror" in error_lower:
            suggestions.append("Check path with list_directory, create if needed")
        if "syntaxerror" in error_lower:
            suggestions.append("Read the file, find the syntax error, fix with edit_file")
        if "permission" in error_lower:
            suggestions.append("Check file permissions or try a different path")
        if "timeout" in error_lower:
            suggestions.append("Increase timeout or break into smaller parts")
        if "connection" in error_lower or "refused" in error_lower:
            suggestions.append("Check if the service is running, verify URL/port")

        sug = "\n".join(f"  - {s}" for s in suggestions) if suggestions else \
            "  - Analyze the error and try a different approach"

        return (
            f"Step FAILED. {task_progress}\n"
            f"Error: {output[:400]}\n\n"
            "TROUBLESHOOT AND FIX THIS NOW. Do NOT stop or ask.\n"
            f"Suggested fixes:\n{sug}\n\n"
            "Make your next tool call to resolve this. Keep working."
        )
