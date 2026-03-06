"""
JARVIS NEXUS — Sequential Thinking Engine
═══════════════════════════════════════════════════════════════
Multi-threaded, branching thought-chain processor integrated natively into
the JARVIS NEXUS MCP. Emulates deep sequential thinking with:

  - Branching thought chains (explore multiple solution paths)
  - Dependency-aware step sequencing
  - Revision and backtracking support
  - Confidence scoring and path selection
  - Persistent thought log for auditing

This replaces the need for @modelcontextprotocol/server-sequential-thinking
as a separate MCP server — it's built directly into the NEXUS pipeline.
"""

import json
import time
import uuid
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime


# ════════════════════════════════════════════════════════════════════
#  ENUMS & DATA STRUCTURES
# ════════════════════════════════════════════════════════════════════

class ThoughtType(str, Enum):
    ANALYSIS     = "analysis"        # Understanding the problem
    PLANNING     = "planning"        # Breaking into steps
    HYPOTHESIS   = "hypothesis"      # Testing an assumption
    EXECUTION    = "execution"       # Direct action step
    VALIDATION   = "validation"      # Verifying a result
    REVISION     = "revision"        # Correcting a prior thought
    BRANCH       = "branch"          # Exploring alternate path
    SYNTHESIS    = "synthesis"        # Combining findings
    PRECHECK     = "precheck"        # Forward-thinking pre-validation
    RISK_ASSESS  = "risk_assessment" # Evaluating risk before action


class StepStatus(str, Enum):
    PENDING     = "pending"
    ACTIVE      = "active"
    COMPLETED   = "completed"
    FAILED      = "failed"
    REVISED     = "revised"
    BRANCHED    = "branched"
    SKIPPED     = "skipped"


@dataclass
class ThoughtStep:
    """A single step in a sequential thought chain."""
    id: str
    thought: str
    thought_type: ThoughtType
    status: StepStatus = StepStatus.PENDING
    confidence: float = 0.8          # 0.0 - 1.0
    branch_id: str = "main"          # Which branch this belongs to
    parent_id: Optional[str] = None  # Previous step in chain
    depends_on: List[str] = field(default_factory=list)  # Steps that must complete first
    children: List[str] = field(default_factory=list)     # Steps spawned from this
    revision_of: Optional[str] = None  # If this revises a prior step
    tool_calls: List[Dict] = field(default_factory=list)  # Tools needed for this step
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "thought": self.thought,
            "type": self.thought_type.value,
            "status": self.status.value,
            "confidence": self.confidence,
            "branch": self.branch_id,
            "parent": self.parent_id,
            "depends_on": self.depends_on,
            "children": self.children,
            "revision_of": self.revision_of,
            "tool_calls": self.tool_calls,
            "result": self.result[:300] if self.result else None,
            "error": self.error[:200] if self.error else None,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


@dataclass
class ThoughtBranch:
    """A branch in the thought tree — represents one solution path."""
    id: str
    name: str
    description: str
    steps: List[str] = field(default_factory=list)     # Step IDs in order
    status: StepStatus = StepStatus.ACTIVE
    confidence: float = 0.5
    parent_branch: Optional[str] = None
    branch_point_step: Optional[str] = None            # Step where we branched
    created_at: float = field(default_factory=time.time)


# ════════════════════════════════════════════════════════════════════
#  SEQUENTIAL THINKING ENGINE
# ════════════════════════════════════════════════════════════════════

class SequentialThinkingEngine:
    """
    Maintains a persistent, branching chain of thought for complex tasks.

    The engine tracks:
    - An ordered chain of thought steps
    - Multiple branches for exploring alternatives
    - Dependencies between steps
    - Confidence scoring for selecting the best path
    - Full revision history

    MCP tools exposed:
    - sequential_think: Add a new thought step
    - branch_thought: Create a branch to explore an alternative
    - revise_thought: Revise a previous step with new understanding
    - get_thinking_state: Get current chain state
    - resolve_branch: Pick the winning branch and merge
    - precheck_action: Pre-validate an action before executing it
    """

    def __init__(self, log_dir: Optional[Path] = None):
        self.steps: Dict[str, ThoughtStep] = {}
        self.branches: Dict[str, ThoughtBranch] = {}
        self.active_branch: str = "main"
        self.step_counter: int = 0
        self.task_description: str = ""
        self._log_dir = log_dir

        # Initialize main branch
        self.branches["main"] = ThoughtBranch(
            id="main",
            name="main",
            description="Primary solution path",
        )

    # ────────────────────────────────────────────────────────────────
    #  CORE: ADD THOUGHT
    # ────────────────────────────────────────────────────────────────

    def add_thought(
        self,
        thought: str,
        thought_type: str = "analysis",
        confidence: float = 0.8,
        depends_on: Optional[List[str]] = None,
        tool_calls: Optional[List[Dict]] = None,
        branch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a thought step to the sequential chain.

        Returns the step record and the current chain state summary.
        """
        self.step_counter += 1
        step_id = f"step_{self.step_counter}"
        target_branch = branch_id or self.active_branch
        branch = self.branches.get(target_branch)

        if not branch:
            return {"error": f"Branch '{target_branch}' not found"}

        # Find parent (last step in this branch)
        parent_id = branch.steps[-1] if branch.steps else None

        # Check dependencies are satisfied
        dep_list = depends_on or []
        unresolved = [
            d for d in dep_list
            if d in self.steps and self.steps[d].status not in (StepStatus.COMPLETED, StepStatus.SKIPPED)
        ]

        try:
            tt = ThoughtType(thought_type)
        except ValueError:
            tt = ThoughtType.ANALYSIS

        step = ThoughtStep(
            id=step_id,
            thought=thought,
            thought_type=tt,
            confidence=confidence,
            branch_id=target_branch,
            parent_id=parent_id,
            depends_on=dep_list,
            tool_calls=tool_calls or [],
            status=StepStatus.PENDING if unresolved else StepStatus.ACTIVE,
        )

        self.steps[step_id] = step
        branch.steps.append(step_id)

        # Link parent -> child
        if parent_id and parent_id in self.steps:
            self.steps[parent_id].children.append(step_id)

        self._persist_step(step)

        return {
            "step_id": step_id,
            "step_number": self.step_counter,
            "total_steps": len(self.steps),
            "branch": target_branch,
            "status": step.status.value,
            "unresolved_deps": unresolved,
            "chain_summary": self._get_chain_summary(target_branch),
            "next_actions": self._suggest_next_actions(step),
        }

    # ────────────────────────────────────────────────────────────────
    #  CORE: COMPLETE THOUGHT
    # ────────────────────────────────────────────────────────────────

    def complete_thought(
        self,
        step_id: str,
        result: str,
        success: bool = True,
        confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Mark a thought step as completed or failed, with result."""
        step = self.steps.get(step_id)
        if not step:
            return {"error": f"Step '{step_id}' not found"}

        step.status = StepStatus.COMPLETED if success else StepStatus.FAILED
        step.result = result
        step.completed_at = time.time()
        if confidence is not None:
            step.confidence = confidence

        if not success:
            step.error = result

        # Check if any pending steps can now be activated
        activated = []
        for sid, s in self.steps.items():
            if s.status == StepStatus.PENDING:
                unresolved = [
                    d for d in s.depends_on
                    if d in self.steps and self.steps[d].status not in (StepStatus.COMPLETED, StepStatus.SKIPPED)
                ]
                if not unresolved:
                    s.status = StepStatus.ACTIVE
                    activated.append(sid)

        # Update branch confidence
        branch = self.branches.get(step.branch_id)
        if branch:
            branch_steps = [self.steps[sid] for sid in branch.steps if sid in self.steps]
            completed = [s for s in branch_steps if s.status == StepStatus.COMPLETED]
            if completed:
                branch.confidence = sum(s.confidence for s in completed) / len(completed)

        self._persist_step(step)

        return {
            "step_id": step_id,
            "status": step.status.value,
            "newly_activated": activated,
            "branch_confidence": branch.confidence if branch else 0,
            "chain_summary": self._get_chain_summary(step.branch_id),
        }

    # ────────────────────────────────────────────────────────────────
    #  BRANCHING
    # ────────────────────────────────────────────────────────────────

    def branch_thought(
        self,
        branch_name: str,
        description: str,
        from_step: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new branch to explore an alternative solution path.
        Branches fork from the current position or a specific step.
        """
        branch_id = f"branch_{len(self.branches)}"
        parent_branch = self.active_branch
        branch_point = from_step or (
            self.branches[parent_branch].steps[-1]
            if self.branches[parent_branch].steps
            else None
        )

        branch = ThoughtBranch(
            id=branch_id,
            name=branch_name,
            description=description,
            parent_branch=parent_branch,
            branch_point_step=branch_point,
        )
        self.branches[branch_id] = branch

        # Mark the branching step
        if branch_point and branch_point in self.steps:
            self.steps[branch_point].status = StepStatus.BRANCHED
            self.steps[branch_point].children.append(f"[branch:{branch_id}]")

        return {
            "branch_id": branch_id,
            "name": branch_name,
            "branched_from": parent_branch,
            "branch_point": branch_point,
            "total_branches": len(self.branches),
            "instruction": (
                f"Branch '{branch_name}' created. Add thoughts to it with "
                f"branch_id='{branch_id}'. Switch active branch with resolve_branch."
            ),
        }

    def resolve_branch(
        self,
        winning_branch: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        Select the winning branch and merge it into the main line.
        Other branches are archived for reference.
        """
        if winning_branch not in self.branches:
            return {"error": f"Branch '{winning_branch}' not found"}

        old_active = self.active_branch
        self.active_branch = winning_branch

        # Archive non-winning branches at same level
        for bid, b in self.branches.items():
            if bid != winning_branch and b.parent_branch == self.branches[winning_branch].parent_branch:
                if b.status == StepStatus.ACTIVE:
                    b.status = StepStatus.SKIPPED

        return {
            "active_branch": winning_branch,
            "previous_branch": old_active,
            "reason": reason,
            "branch_confidence": self.branches[winning_branch].confidence,
            "chain_summary": self._get_chain_summary(winning_branch),
        }

    # ────────────────────────────────────────────────────────────────
    #  REVISION
    # ────────────────────────────────────────────────────────────────

    def revise_thought(
        self,
        original_step_id: str,
        revised_thought: str,
        reason: str = "",
        confidence: float = 0.8,
    ) -> Dict[str, Any]:
        """
        Revise a previous thought with updated understanding.
        The original is marked as revised; the new step links back.
        """
        original = self.steps.get(original_step_id)
        if not original:
            return {"error": f"Step '{original_step_id}' not found"}

        original.status = StepStatus.REVISED

        # Create revision step
        result = self.add_thought(
            thought=f"[REVISION of {original_step_id}] {revised_thought}",
            thought_type="revision",
            confidence=confidence,
            branch_id=original.branch_id,
        )

        new_step_id = result.get("step_id")
        if new_step_id and new_step_id in self.steps:
            self.steps[new_step_id].revision_of = original_step_id
            self.steps[new_step_id].metadata["revision_reason"] = reason

        return {
            **result,
            "revised_step": original_step_id,
            "reason": reason,
        }

    # ────────────────────────────────────────────────────────────────
    #  PRE-CHECK (Forward-Thinking Validation)
    # ────────────────────────────────────────────────────────────────

    def precheck_action(
        self,
        intended_action: str,
        tool_name: str = "",
        arguments: Optional[Dict] = None,
        risk_factors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Forward-thinking pre-validation before executing a tool call.
        Analyzes the intended action for potential issues and suggests
        validation steps to run first.

        This is the proactive troubleshooting layer — it thinks AHEAD
        to prevent errors rather than just reacting to them.
        """
        checks = []
        risk_level = "low"
        suggestions = []

        args = arguments or {}

        # ── File operation checks ─────────────────────────────────
        if tool_name in ("write_file", "edit_file", "delete_file"):
            path = args.get("path", "")
            checks.append(f"Verify path exists/is-writable: {path}")
            if tool_name == "edit_file":
                checks.append("Read file first to confirm old_string match is unique")
                suggestions.append({
                    "pre_tool": "read_file",
                    "pre_args": {"path": path},
                    "reason": "Verify current file state before editing"
                })
            if tool_name == "delete_file":
                risk_level = "medium"
                checks.append("Confirm file is not imported/referenced elsewhere")
                suggestions.append({
                    "pre_tool": "search_text",
                    "pre_args": {"query": path.split("/")[-1] if "/" in path else path},
                    "reason": "Check for references before deleting"
                })

        # ── Terminal command checks ───────────────────────────────
        if tool_name == "run_terminal":
            cmd = args.get("command", "")
            if any(d in cmd.lower() for d in ["deploy", "push", "publish", "mainnet", "production"]):
                risk_level = "critical"
                checks.append("DEPLOYMENT DETECTED — requires explicit user approval")
                suggestions.append({
                    "action": "REQUEST_APPROVAL",
                    "reason": "Deployment/production command detected"
                })
            if "install" in cmd.lower():
                risk_level = "medium"
                checks.append("Package installation — verify requirements.txt consistency")
            if "rm " in cmd or "del " in cmd.lower():
                risk_level = "medium"
                checks.append("Destructive command — verify targets")

        # ── Test-before-deploy pattern ────────────────────────────
        if tool_name == "run_tests":
            checks.append("Ensure relevant files are saved and syntax-checked first")

        # ── Git operation checks ──────────────────────────────────
        if tool_name == "git_operation":
            op = args.get("operation", "")
            if op in ("commit", "push"):
                risk_level = "medium"
                checks.append("Run tests before committing")
                checks.append("Check for uncommitted debug/temp code")
                suggestions.append({
                    "pre_tool": "run_tests",
                    "pre_args": {},
                    "reason": "Validate before commit"
                })

        # Log as a precheck thought step
        step_result = self.add_thought(
            thought=f"[PRECHECK] {intended_action}",
            thought_type="precheck",
            confidence=0.9 if risk_level == "low" else 0.6,
        )

        return {
            **step_result,
            "risk_level": risk_level,
            "checks": checks,
            "suggested_pre_validations": suggestions,
            "proceed": risk_level != "critical",
            "requires_approval": risk_level == "critical",
        }

    # ────────────────────────────────────────────────────────────────
    #  STATE QUERIES
    # ────────────────────────────────────────────────────────────────

    def get_state(self, include_completed: bool = True) -> Dict[str, Any]:
        """Get the full current thinking state."""
        active_steps = [
            s.to_dict() for s in self.steps.values()
            if s.status in (StepStatus.ACTIVE, StepStatus.PENDING)
        ]
        completed_steps = [
            s.to_dict() for s in self.steps.values()
            if s.status == StepStatus.COMPLETED
        ] if include_completed else []

        failed_steps = [
            s.to_dict() for s in self.steps.values()
            if s.status == StepStatus.FAILED
        ]

        branch_info = []
        for bid, b in self.branches.items():
            branch_info.append({
                "id": b.id,
                "name": b.name,
                "description": b.description,
                "step_count": len(b.steps),
                "confidence": round(b.confidence, 3),
                "status": b.status.value,
                "is_active": bid == self.active_branch,
            })

        return {
            "total_steps": len(self.steps),
            "active_steps": active_steps,
            "completed_steps": completed_steps[-10:],  # Last 10
            "failed_steps": failed_steps,
            "branches": branch_info,
            "active_branch": self.active_branch,
            "chain_summary": self._get_chain_summary(self.active_branch),
        }

    # ────────────────────────────────────────────────────────────────
    #  STATE INSPECTION
    # ────────────────────────────────────────────────────────────────

    def get_thinking_state(self, branch_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve the complete thinking state for inspection and review.
        Returns all steps, branches, dependencies, and confidence scores.
        """
        target_branch = branch_id or self.active_branch
        if target_branch not in self.branches:
            return {"error": f"Branch '{target_branch}' not found"}

        branch = self.branches[target_branch]
        branch_steps = [self.steps[sid] for sid in branch.steps if sid in self.steps]

        return {
            "task": self.task_description,
            "active_branch": self.active_branch,
            "target_branch": target_branch,
            "branch_info": {
                "id": branch.id,
                "name": branch.name,
                "description": branch.description,
                "status": branch.status.value,
                "confidence": branch.confidence,
                "total_steps": len(branch.steps),
            },
            "steps": [
                {
                    "id": s.id,
                    "thought": s.thought[:200],
                    "type": s.thought_type.value,
                    "status": s.status.value,
                    "confidence": s.confidence,
                    "parent": s.parent_id,
                    "depends_on": s.depends_on,
                    "children": s.children,
                    "result": s.result[:100] if s.result else None,
                    "error": s.error[:100] if s.error else None,
                }
                for s in branch_steps
            ],
            "all_branches": [
                {
                    "id": b.id,
                    "name": b.name,
                    "description": b.description,
                    "status": b.status.value,
                    "confidence": b.confidence,
                    "step_count": len(b.steps),
                }
                for b in self.branches.values()
            ],
            "stats": {
                "total_steps": len(self.steps),
                "total_branches": len(self.branches),
                "completed": sum(1 for s in self.steps.values() if s.status == StepStatus.COMPLETED),
                "active": sum(1 for s in self.steps.values() if s.status == StepStatus.ACTIVE),
                "failed": sum(1 for s in self.steps.values() if s.status == StepStatus.FAILED),
                "pending": sum(1 for s in self.steps.values() if s.status == StepStatus.PENDING),
            }
        }

    # ────────────────────────────────────────────────────────────────
    #  CHAIN SUMMARY & INSPECTION
    # ────────────────────────────────────────────────────────────────

    def get_chain_of_thought(self, branch_id: Optional[str] = None) -> str:
        """Get a human-readable chain-of-thought for the specified branch."""
        bid = branch_id or self.active_branch
        branch = self.branches.get(bid)
        if not branch:
            return f"Branch '{bid}' not found"

        lines = [
            f"═══ Chain of Thought: {branch.name} ═══",
            f"Confidence: {branch.confidence:.0%} | Steps: {len(branch.steps)}",
            "",
        ]

        for i, step_id in enumerate(branch.steps, 1):
            step = self.steps.get(step_id)
            if not step:
                continue

            status_icon = {
                StepStatus.COMPLETED: "✓",
                StepStatus.FAILED: "✗",
                StepStatus.ACTIVE: "►",
                StepStatus.PENDING: "○",
                StepStatus.REVISED: "↻",
                StepStatus.BRANCHED: "⑂",
                StepStatus.SKIPPED: "─",
            }.get(step.status, "?")

            type_tag = f"[{step.thought_type.value}]"
            conf = f"({step.confidence:.0%})"

            lines.append(f"  {status_icon} {i}. {type_tag} {conf} {step.thought[:120]}")
            if step.result and step.status == StepStatus.COMPLETED:
                lines.append(f"      → {step.result[:100]}")
            if step.error:
                lines.append(f"      ✗ {step.error[:100]}")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────────────
    #  INTERNAL HELPERS
    # ────────────────────────────────────────────────────────────────

    def _get_chain_summary(self, branch_id: str) -> str:
        """Compact one-liner summary of chain state."""
        branch = self.branches.get(branch_id)
        if not branch:
            return "no branch"
        total = len(branch.steps)
        done = sum(
            1 for sid in branch.steps
            if sid in self.steps and self.steps[sid].status == StepStatus.COMPLETED
        )
        active = sum(
            1 for sid in branch.steps
            if sid in self.steps and self.steps[sid].status == StepStatus.ACTIVE
        )
        failed = sum(
            1 for sid in branch.steps
            if sid in self.steps and self.steps[sid].status == StepStatus.FAILED
        )
        return f"[{branch.name}] {done}/{total} done, {active} active, {failed} failed, conf={branch.confidence:.0%}"

    def _suggest_next_actions(self, step: ThoughtStep) -> List[str]:
        """Suggest what the agent should do next based on the thought chain."""
        suggestions = []

        if step.thought_type == ThoughtType.ANALYSIS:
            suggestions.append("Plan concrete steps based on this analysis")
        elif step.thought_type == ThoughtType.PLANNING:
            suggestions.append("Begin executing the first planned step")
        elif step.thought_type == ThoughtType.EXECUTION:
            suggestions.append("Validate the execution result")
        elif step.thought_type == ThoughtType.VALIDATION:
            suggestions.append("If valid, proceed to next step. If not, revise.")
        elif step.thought_type == ThoughtType.HYPOTHESIS:
            suggestions.append("Test this hypothesis with a concrete action")
        elif step.thought_type == ThoughtType.PRECHECK:
            suggestions.append("Execute the pre-validation checks, then proceed")

        if step.tool_calls:
            suggestions.append(f"Execute {len(step.tool_calls)} planned tool calls")

        return suggestions

    def _persist_step(self, step: ThoughtStep):
        """Write step to persistent thought log if log_dir is set."""
        if not self._log_dir:
            return
        try:
            log_file = self._log_dir / "thought_chain.jsonl"
            with open(log_file, "a", encoding="utf-8") as f:
                entry = {
                    "timestamp": datetime.now().isoformat(),
                    **step.to_dict(),
                }
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass

    def reset(self, task_description: str = ""):
        """Reset for a new task."""
        self.steps.clear()
        self.branches.clear()
        self.step_counter = 0
        self.active_branch = "main"
        self.task_description = task_description
        self.branches["main"] = ThoughtBranch(
            id="main",
            name="main",
            description="Primary solution path",
        )
