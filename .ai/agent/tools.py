"""
JARVIS NEXUS — Tool Executor
Full-stack tool implementations: filesystem, terminal, testing, git, search, error checking.
Every tool returns a dict with 'success' (bool) and 'output' (str).
"""

import os
import re
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Dict, Any, Optional

from .config import AgentConfig

# ── LiveStream integration for real-time visibility ──────────────
try:
    from .live_stream import get_stream, run_command_streamed
    _LIVE_STREAM_AVAILABLE = True
except ImportError:
    _LIVE_STREAM_AVAILABLE = False


class ToolExecutor:
    """
    Executes tool calls from the agent loop.
    All file paths are resolved relative to project root if not absolute.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.root = config.project_root
        self._terminal_env = os.environ.copy()
        self._terminal_env["PYTHONIOENCODING"] = "utf-8"

    # ════════════════════════════════════════════════════════════════
    #  PATH RESOLUTION & SAFETY
    # ════════════════════════════════════════════════════════════════

    def _resolve(self, path_str: str) -> Path:
        """Resolve a path relative to project root, or use absolute."""
        p = Path(path_str)
        if not p.is_absolute():
            p = self.root / p
        return p.resolve()

    def _is_safe_path(self, path: Path) -> bool:
        """Check that path is within project and not in protected dirs."""
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError:
            return False
        for protected in self.config.protected_paths:
            if protected in path.parts:
                # Allow reading .env.template but not .env
                if protected == ".env" and path.name == ".env.template":
                    return True
                if protected == ".env" and path.name == ".env":
                    return False
                if protected in (".git", "__pycache__", "node_modules"):
                    return False
        return True

    def _check_dangerous(self, command: str) -> Optional[str]:
        """Check if a command is in the dangerous list."""
        cmd_lower = command.lower().strip()
        for danger in self.config.dangerous_commands:
            if danger.lower() in cmd_lower:
                return f"BLOCKED: Dangerous command detected: '{danger}'"
        return None

    # ════════════════════════════════════════════════════════════════
    #  DISPATCH
    # ════════════════════════════════════════════════════════════════

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Route a tool call to the appropriate handler."""
        handler_map = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "delete_file": self._delete_file,
            "list_directory": self._list_directory,
            "run_terminal": self._run_terminal,
            "run_tests": self._run_tests,
            "search_text": self._search_text,
            "git_operation": self._git_operation,
            "check_errors": self._check_errors,
            "task_complete": self._task_complete,
            "sequential_think": self._sequential_think,
            "complete_thought": self._complete_thought,
            "branch_thought": self._branch_thought,
            "revise_thought": self._revise_thought,
            "get_thinking_state": self._get_thinking_state,
            "get_live_activity": self._get_live_activity,
            "request_approval": self._request_approval,
            "precheck_action": self._precheck_action,
        }

        handler = handler_map.get(tool_name)
        if not handler:
            return {"success": False, "output": f"Unknown tool: {tool_name}"}

        try:
            return handler(**arguments)
        except Exception as e:
            return {
                "success": False,
                "output": f"Tool '{tool_name}' error: {type(e).__name__}: {e}\n{traceback.format_exc()}"
            }

    # ════════════════════════════════════════════════════════════════
    #  FILE OPERATIONS
    # ════════════════════════════════════════════════════════════════

    def _read_file(self, path: str, start_line: int = 1, end_line: int = -1) -> Dict:
        fp = self._resolve(path)
        if not fp.exists():
            return {"success": False, "output": f"File not found: {fp}"}
        if not fp.is_file():
            return {"success": False, "output": f"Not a file: {fp}"}

        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines(keepends=True)
            total = len(lines)

            if end_line == -1:
                end_line = total
            start_line = max(1, start_line)
            end_line = min(total, end_line)

            selected = lines[start_line - 1 : end_line]
            numbered = "".join(
                f"{start_line + i} | {line}" for i, line in enumerate(selected)
            )

            return {
                "success": True,
                "output": f"File: {fp} ({total} lines, showing {start_line}-{end_line})\n{numbered}"
            }
        except Exception as e:
            return {"success": False, "output": f"Read error: {e}"}

    def _write_file(self, path: str, content: str) -> Dict:
        fp = self._resolve(path)
        if not self._is_safe_path(fp):
            return {"success": False, "output": f"Protected path, cannot write: {fp}"}

        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            lines = content.count("\n") + 1
            return {"success": True, "output": f"Written {lines} lines to {fp}"}
        except Exception as e:
            return {"success": False, "output": f"Write error: {e}"}

    def _edit_file(self, path: str, old_string: str, new_string: str) -> Dict:
        fp = self._resolve(path)
        if not fp.exists():
            return {"success": False, "output": f"File not found: {fp}"}
        if not self._is_safe_path(fp):
            return {"success": False, "output": f"Protected path, cannot edit: {fp}"}

        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
            count = content.count(old_string)

            if count == 0:
                # Try fuzzy match: strip trailing whitespace per line
                old_stripped = "\n".join(l.rstrip() for l in old_string.split("\n"))
                content_stripped = "\n".join(l.rstrip() for l in content.split("\n"))
                if old_stripped in content_stripped:
                    # Find the actual text to replace using the original content
                    idx = content_stripped.index(old_stripped)
                    # Map back to original: count chars up to idx
                    actual_old = content[idx : idx + len(old_stripped)]
                    content = content.replace(actual_old, new_string, 1)
                    fp.write_text(content, encoding="utf-8")
                    return {"success": True, "output": f"Edited {fp} (fuzzy match, 1 replacement)"}
                return {"success": False, "output": f"String not found in {fp}. File has {len(content)} chars."}

            if count > 1:
                return {
                    "success": False,
                    "output": f"Ambiguous: found {count} matches in {fp}. Include more context lines."
                }

            content = content.replace(old_string, new_string, 1)
            fp.write_text(content, encoding="utf-8")
            return {"success": True, "output": f"Edited {fp} (1 replacement)"}
        except Exception as e:
            return {"success": False, "output": f"Edit error: {e}"}

    def _delete_file(self, path: str) -> Dict:
        fp = self._resolve(path)
        if not self._is_safe_path(fp):
            return {"success": False, "output": f"Protected path, cannot delete: {fp}"}

        try:
            if fp.is_file():
                fp.unlink()
                return {"success": True, "output": f"Deleted file: {fp}"}
            elif fp.is_dir():
                if any(fp.iterdir()):
                    return {"success": False, "output": f"Directory not empty: {fp}. Use run_terminal with 'rm -rf' for non-empty dirs."}
                fp.rmdir()
                return {"success": True, "output": f"Deleted empty directory: {fp}"}
            else:
                return {"success": False, "output": f"Path not found: {fp}"}
        except Exception as e:
            return {"success": False, "output": f"Delete error: {e}"}

    # ════════════════════════════════════════════════════════════════
    #  DIRECTORY LISTING
    # ════════════════════════════════════════════════════════════════

    def _list_directory(self, path: str = ".", recursive: bool = False) -> Dict:
        dp = self._resolve(path)
        if not dp.exists():
            return {"success": False, "output": f"Directory not found: {dp}"}
        if not dp.is_dir():
            return {"success": False, "output": f"Not a directory: {dp}"}

        try:
            entries = []
            if recursive:
                for item in sorted(dp.rglob("*")):
                    # Max depth 3
                    try:
                        rel = item.relative_to(dp)
                        if len(rel.parts) > 3:
                            continue
                    except ValueError:
                        continue
                    # Skip hidden dirs and __pycache__
                    if any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
                        continue
                    suffix = "/" if item.is_dir() else ""
                    entries.append(f"  {rel}{suffix}")
            else:
                for item in sorted(dp.iterdir()):
                    if item.name.startswith(".") or item.name == "__pycache__":
                        continue
                    suffix = "/" if item.is_dir() else ""
                    entries.append(f"  {item.name}{suffix}")

            output = f"Directory: {dp}\n" + "\n".join(entries[:500])
            if len(entries) > 500:
                output += f"\n  ... and {len(entries) - 500} more"
            return {"success": True, "output": output}
        except Exception as e:
            return {"success": False, "output": f"List error: {e}"}

    # ════════════════════════════════════════════════════════════════
    #  TERMINAL EXECUTION
    # ════════════════════════════════════════════════════════════════

    def _run_terminal(self, command: str, working_dir: str = ".", timeout: int = 120) -> Dict:
        danger = self._check_dangerous(command)
        if danger:
            return {"success": False, "output": danger}

        wd = self._resolve(working_dir)
        if not wd.is_dir():
            wd = self.root

        if _LIVE_STREAM_AVAILABLE:
            # ── Streaming mode: real-time output to live monitor ──
            success, output = run_command_streamed(
                command,
                cwd=str(wd),
                timeout=timeout,
                env=self._terminal_env,
                source="agent",
            )
            return {"success": success, "output": output}
        else:
            # ── Fallback: silent capture ──────────────────────────
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=str(wd),
                    timeout=timeout,
                    env=self._terminal_env,
                    errors="replace",
                )

                stdout = result.stdout or ""
                stderr = result.stderr or ""
                max_len = 8000
                if len(stdout) > max_len:
                    stdout = stdout[:max_len] + f"\n... [truncated, {len(result.stdout)} total chars]"
                if len(stderr) > max_len:
                    stderr = stderr[:max_len] + f"\n... [truncated, {len(result.stderr)} total chars]"

                output_parts = []
                if stdout.strip():
                    output_parts.append(f"STDOUT:\n{stdout}")
                if stderr.strip():
                    output_parts.append(f"STDERR:\n{stderr}")
                output_parts.append(f"EXIT CODE: {result.returncode}")

                return {
                    "success": result.returncode == 0,
                    "output": "\n".join(output_parts)
                }
            except subprocess.TimeoutExpired:
                return {"success": False, "output": f"Command timed out after {timeout}s: {command}"}
            except Exception as e:
                return {"success": False, "output": f"Terminal error: {e}"}

    # ════════════════════════════════════════════════════════════════
    #  TEST RUNNER
    # ════════════════════════════════════════════════════════════════

    def _run_tests(
        self,
        test_path: str = "",
        framework: str = "pytest",
        pattern: str = "",
        verbose: bool = True,
    ) -> Dict:
        if framework == "pytest":
            cmd_parts = [f'"{sys.executable}"', "-m", "pytest"]
            if test_path:
                cmd_parts.append(test_path)
            if pattern:
                cmd_parts.extend(["-k", pattern])
            if verbose:
                cmd_parts.extend(["-v", "--tb=short"])
            cmd_parts.append("--no-header")
            command = " ".join(cmd_parts)

        elif framework == "forge":
            cmd_parts = ["forge", "test", "-vvv"]
            if pattern:
                cmd_parts.extend(["--match-contract", pattern])
            command = " ".join(cmd_parts)

        elif framework == "custom":
            if not test_path:
                return {"success": False, "output": "Custom framework requires test_path as the command"}
            command = test_path
        else:
            return {"success": False, "output": f"Unknown test framework: {framework}"}

        return self._run_terminal(command, timeout=300)

    # ════════════════════════════════════════════════════════════════
    #  SEARCH
    # ════════════════════════════════════════════════════════════════

    def _search_text(
        self,
        query: str,
        include_pattern: str = "",
        is_regex: bool = False,
        max_results: int = 50,
    ) -> Dict:
        results = []
        search_root = self.root

        try:
            if is_regex:
                pattern = re.compile(query, re.IGNORECASE)
            else:
                pattern = None

            for fp in search_root.rglob("*"):
                if len(results) >= max_results:
                    break
                if not fp.is_file():
                    continue
                # Skip binary / hidden / large files
                if any(p.startswith(".") or p in ("node_modules", "__pycache__", "lib") for p in fp.relative_to(search_root).parts):
                    continue
                if fp.stat().st_size > 1_000_000:  # skip >1MB
                    continue
                if include_pattern:
                    if not fp.match(include_pattern):
                        continue

                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    for line_num, line in enumerate(text.splitlines(), 1):
                        if len(results) >= max_results:
                            break
                        if is_regex and pattern:
                            if pattern.search(line):
                                rel = fp.relative_to(search_root)
                                results.append(f"{rel}:{line_num}: {line.strip()}")
                        else:
                            if query.lower() in line.lower():
                                rel = fp.relative_to(search_root)
                                results.append(f"{rel}:{line_num}: {line.strip()}")
                except (UnicodeDecodeError, PermissionError):
                    continue

            if results:
                output = f"Found {len(results)} matches for '{query}':\n" + "\n".join(results)
            else:
                output = f"No matches found for '{query}'"
            return {"success": True, "output": output}
        except Exception as e:
            return {"success": False, "output": f"Search error: {e}"}

    # ════════════════════════════════════════════════════════════════
    #  GIT OPERATIONS
    # ════════════════════════════════════════════════════════════════

    def _git_operation(self, operation: str, args: str = "") -> Dict:
        git_commands = {
            "status": "git status --short",
            "diff": f"git diff {args}".strip(),
            "log": f"git --no-pager log --oneline -20 {args}".strip(),
            "add": f"git add {args or '.'}",
            "commit": f'git commit -m "{args}"' if args else 'git commit -m "auto-commit by agent"',
            "branch": f"git branch {args}".strip(),
            "checkout": f"git checkout {args}".strip(),
            "stash": f"git stash {args}".strip(),
        }

        cmd = git_commands.get(operation)
        if not cmd:
            return {"success": False, "output": f"Unknown git operation: {operation}"}

        return self._run_terminal(cmd)

    # ════════════════════════════════════════════════════════════════
    #  ERROR CHECKING
    # ════════════════════════════════════════════════════════════════

    def _check_errors(self, path: str) -> Dict:
        fp = self._resolve(path)
        if not fp.exists():
            return {"success": False, "output": f"File not found: {fp}"}

        ext = fp.suffix.lower()

        if ext == ".py":
            # Python: compile check + basic import check
            try:
                source = fp.read_text(encoding="utf-8", errors="replace")
                compile(source, str(fp), "exec")
                # Also run py_compile (quote Python path for Windows spaces)
                result = self._run_terminal(
                    f'"{sys.executable}" -m py_compile "{fp}"',
                    timeout=30
                )
                if result["success"]:
                    return {"success": True, "output": f"No syntax errors in {fp.name}"}
                else:
                    return result
            except SyntaxError as e:
                return {
                    "success": False,
                    "output": f"Syntax error in {fp.name}: line {e.lineno}: {e.msg}"
                }

        elif ext == ".sol":
            return self._run_terminal("forge build", timeout=120)

        elif ext in (".json",):
            try:
                json.loads(fp.read_text(encoding="utf-8"))
                return {"success": True, "output": f"Valid JSON: {fp.name}"}
            except json.JSONDecodeError as e:
                return {"success": False, "output": f"JSON error in {fp.name}: {e}"}

        elif ext in (".yaml", ".yml"):
            try:
                import yaml
                yaml.safe_load(fp.read_text(encoding="utf-8"))
                return {"success": True, "output": f"Valid YAML: {fp.name}"}
            except Exception as e:
                return {"success": False, "output": f"YAML error in {fp.name}: {e}"}

        else:
            return {"success": True, "output": f"No checker available for {ext} files — skipped."}

    # ════════════════════════════════════════════════════════════════
    #  SEQUENTIAL THINKING + PRECHECK
    # ════════════════════════════════════════════════════════════════

    def _sequential_think(self, thought: str, thought_type: str = "analysis",
                          confidence: float = 0.8, **kwargs) -> Dict:
        try:
            from .sequential_thinking import SequentialThinkingEngine
            if not hasattr(self, '_thinker'):
                from .config import LOGS_DIR
                self._thinker = SequentialThinkingEngine(log_dir=LOGS_DIR)
            result = self._thinker.add_thought(thought, thought_type, confidence)
            return {"success": True, "output": json.dumps(result, default=str)}
        except Exception as e:
            return {"success": True, "output": f"Thought recorded: {thought}"}

    def _complete_thought(self, step_id: str, result: str,
                          success: bool = True, **kwargs) -> Dict:
        try:
            if hasattr(self, '_thinker'):
                res = self._thinker.complete_thought(step_id, result, success)
                return {"success": True, "output": json.dumps(res, default=str)}
        except Exception:
            pass
        return {"success": True, "output": f"Thought {step_id} completed: {result}"}

    def _precheck_action(self, intended_action: str, tool_name: str = "",
                         arguments: dict = None, **kwargs) -> Dict:
        try:
            from .autonomous_controller import ForwardThinkingTroubleshooter
            if not hasattr(self, '_troubleshooter'):
                self._troubleshooter = ForwardThinkingTroubleshooter()
            result = self._troubleshooter.analyze_before_execute(tool_name, arguments or {})
            return {"success": True, "output": json.dumps({
                "proceed": result.proceed,
                "approval_required": result.approval_required,
                "approval_reason": result.approval_reason,
                "risk_level": result.risk_level,
                "warnings": result.warnings,
                "auto_fixes": result.auto_fixes,
            }, default=str)}
        except Exception as e:
            return {"success": True, "output": f"Precheck: proceed (analyzer unavailable: {e})"}

    def _branch_thought(self, branch_name: str, description: str,
                        from_step: str = "", **kwargs) -> Dict:
        try:
            if not hasattr(self, '_thinker'):
                from .config import LOGS_DIR
                from .sequential_thinking import SequentialThinkingEngine
                self._thinker = SequentialThinkingEngine(log_dir=LOGS_DIR)
            result = self._thinker.branch_thought(
                branch_name, description, from_step or None
            )
            return {"success": True, "output": json.dumps(result, default=str)}
        except Exception as e:
            return {"success": False, "output": f"branch_thought error: {e}"}

    def _revise_thought(self, original_step_id: str, revised_thought: str,
                        reason: str = "", **kwargs) -> Dict:
        try:
            if not hasattr(self, '_thinker'):
                from .config import LOGS_DIR
                from .sequential_thinking import SequentialThinkingEngine
                self._thinker = SequentialThinkingEngine(log_dir=LOGS_DIR)
            result = self._thinker.revise_thought(original_step_id, revised_thought, reason)
            return {"success": True, "output": json.dumps(result, default=str)}
        except Exception as e:
            return {"success": False, "output": f"revise_thought error: {e}"}

    def _get_thinking_state(self, include_completed: bool = True, **kwargs) -> Dict:
        try:
            if not hasattr(self, '_thinker'):
                from .config import LOGS_DIR
                from .sequential_thinking import SequentialThinkingEngine
                self._thinker = SequentialThinkingEngine(log_dir=LOGS_DIR)
            state = self._thinker.get_state(include_completed)
            state["chain_of_thought"] = self._thinker.get_chain_of_thought()
            return {"success": True, "output": json.dumps(state, default=str)}
        except Exception as e:
            return {"success": False, "output": f"get_thinking_state error: {e}"}

    def _get_live_activity(self, lines: int = 50, **kwargs) -> Dict:
        try:
            from .config import LOGS_DIR
            log_file = LOGS_DIR / "live_stream.log"
            if not log_file.exists():
                return {"success": True, "output": "No live activity log found. The live stream has not started yet."}
            all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return {"success": True, "output": f"=== Recent Activity ({len(recent)} lines) ===\n" + "\n".join(recent)}
        except Exception as e:
            return {"success": False, "output": f"get_live_activity error: {e}"}

    def _request_approval(self, action: str, reason: str,
                          risk_level: str = "critical", **kwargs) -> Dict:
        try:
            import time as _time
            from .config import LOGS_DIR
            approval_file = LOGS_DIR / "pending_approval.json"
            approval_data = {
                "action": action,
                "reason": reason,
                "risk_level": risk_level,
                "requested_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "PENDING",
            }
            approval_file.write_text(
                json.dumps(approval_data, indent=2), encoding="utf-8"
            )
            if _LIVE_STREAM_AVAILABLE:
                try:
                    get_stream().emit("approval_requested", {
                        "action": action, "reason": reason, "risk_level": risk_level,
                    }, source="gate")
                except Exception:
                    pass
            return {
                "success": True,
                "output": json.dumps({
                    "status": "APPROVAL_REQUIRED",
                    "action": action,
                    "reason": reason,
                    "risk_level": risk_level,
                    "instruction": (
                        "STOP EXECUTION. This action requires explicit user approval. "
                        f"Approval file written to: {approval_file}\n"
                        "Do NOT proceed with this action until the user approves. "
                        "Continue working on other non-critical tasks if available."
                    ),
                }),
            }
        except Exception as e:
            return {"success": False, "output": f"request_approval error: {e}"}

    # ════════════════════════════════════════════════════════════════
    #  TASK COMPLETION SIGNAL
    # ════════════════════════════════════════════════════════════════

    def _task_complete(self, summary: str) -> Dict:
        return {
            "success": True,
            "output": f"TASK COMPLETE: {summary}",
            "task_complete": True,
        }
