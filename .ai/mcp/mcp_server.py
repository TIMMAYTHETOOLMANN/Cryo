#!/usr/bin/env python3
"""
JARVIS NEXUS — MCP Tool Server
Model Context Protocol server that exposes filesystem, terminal, testing,
and git tools over stdio transport. Compatible with any MCP client.

This server can be used by:
  - MCP-compatible IDE extensions
  - MCP client libraries
  - The JARVIS NEXUS agent loop (as an alternative to direct tool execution)

Protocol: MCP over stdio (JSON-RPC 2.0)
Transport: stdin/stdout
"""

import json
import os
import sys
import subprocess
import re
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── LiveStream integration for real-time visibility ──────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
try:
    from live_stream import get_stream, run_command_streamed, LiveStream
    _LIVE_STREAM_AVAILABLE = True
except ImportError:
    _LIVE_STREAM_AVAILABLE = False

# ── Sequential Thinking + Autonomous Controller ──────────────────
try:
    from sequential_thinking import SequentialThinkingEngine
    from autonomous_controller import ForwardThinkingTroubleshooter, classify_action, ApprovalLevel
    _THINKING_AVAILABLE = True
except ImportError:
    _THINKING_AVAILABLE = False

# ════════════════════════════════════════════════════════════════════
#  PROJECT ROOT
# ════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
os.environ['PYTHONIOENCODING'] = 'utf-8'

# ── Encoding safety (Windows) ────────────────────────────────────
for _s in ('stdin', 'stdout', 'stderr'):
    _stream = getattr(sys, _s)
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════
#  MCP PROTOCOL HANDLER
# ════════════════════════════════════════════════════════════════════

class MCPServer:
    """
    Minimal MCP server implementation over stdio.
    Handles JSON-RPC 2.0 messages for tool listing, tool execution, and resource access.
    """

    def __init__(self):
        self.server_name = "jarvis-nexus-mcp"
        self.server_version = "2.0.0"
        self.tools = self._define_tools()
        # Sequential Thinking Engine
        log_dir = PROJECT_ROOT / ".ai" / "agent" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._thinker = SequentialThinkingEngine(log_dir=log_dir) if _THINKING_AVAILABLE else None
        self._troubleshooter = ForwardThinkingTroubleshooter() if _THINKING_AVAILABLE else None

    def _log(self, *parts):
        """Log to stderr (stdout is reserved for MCP protocol)."""
        msg = " | ".join(str(p) for p in parts)
        print(f"[JARVIS-MCP] {msg}", file=sys.stderr, flush=True)

    def run(self):
        """Main stdio loop: read newline-delimited JSON-RPC messages from stdin, write responses to stdout."""
        self._log("JARVIS NEXUS MCP server starting")
        self._log(f"Project root: {PROJECT_ROOT}")
        self._log("Waiting for messages on stdin...")

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break  # EOF

                line = line.strip()
                if not line:
                    continue  # skip blank lines

                # Handle Content-Length header (LSP-style framing) as fallback
                if line.startswith("Content-Length:"):
                    content_length = int(line.split(":")[1].strip())
                    # Read until we get blank line separator
                    while True:
                        header_line = sys.stdin.readline().strip()
                        if not header_line:
                            break
                    body = sys.stdin.read(content_length)
                    request = json.loads(body)
                else:
                    request = json.loads(line)

                self._log(f"<- {request.get('method', '?')} id={request.get('id')}")
                response = self._handle_request(request)
                if response:
                    self._send_response(response)

            except json.JSONDecodeError as e:
                self._log(f"JSON parse error: {e}")
                continue
            except KeyboardInterrupt:
                break
            except Exception as e:
                self._log(f"Error: {e}")
                self._send_error(-1, -32603, f"Internal error: {e}")

    def _send_response(self, response: Dict):
        """Send a JSON-RPC response via stdout (newline-delimited JSON for MCP stdio transport)."""
        body = json.dumps(response, ensure_ascii=False)
        sys.stdout.write(body + "\n")
        sys.stdout.flush()

    def _send_notification(self, method: str, params: Dict):
        """Send a JSON-RPC notification (no id, no response expected). Used for progress updates."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        body = json.dumps(msg, ensure_ascii=False)
        sys.stdout.write(body + "\n")
        sys.stdout.flush()

    def _send_error(self, req_id: Any, code: int, message: str):
        self._send_response({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message}
        })

    # ════════════════════════════════════════════════════════════════
    #  REQUEST ROUTING
    # ════════════════════════════════════════════════════════════════

    def _handle_request(self, request: Dict) -> Optional[Dict]:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        handlers = {
            "initialize": self._handle_initialize,
            "initialized": lambda p: {},
            "shutdown": lambda p: {},
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "resources/list": self._handle_resources_list,
            "resources/read": self._handle_resources_read,
            "ping": lambda p: {},
        }

        handler = handlers.get(method)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }

        try:
            result = handler(params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)}
            }

    # ════════════════════════════════════════════════════════════════
    #  MCP HANDLERS
    # ════════════════════════════════════════════════════════════════

    def _handle_initialize(self, params: Dict) -> Dict:
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {
                "name": self.server_name,
                "version": self.server_version,
            }
        }

    def _handle_tools_list(self, params: Dict) -> Dict:
        return {"tools": self.tools}

    def _handle_tools_call(self, params: Dict) -> Dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # ── Live-stream the tool call so monitors can see it ──────
        if _LIVE_STREAM_AVAILABLE:
            try:
                get_stream().tool_call(tool_name, arguments, source="mcp")
            except Exception:
                pass

        executor_map = {
            "read_file": self._tool_read_file,
            "write_file": self._tool_write_file,
            "edit_file": self._tool_edit_file,
            "delete_file": self._tool_delete_file,
            "list_directory": self._tool_list_directory,
            "run_terminal": self._tool_run_terminal,
            "run_tests": self._tool_run_tests,
            "search_text": self._tool_search_text,
            "git_status": self._tool_git_status,
            "check_errors": self._tool_check_errors,
            "get_live_activity": self._tool_get_live_activity,
            "sequential_think": self._tool_sequential_think,
            "complete_thought": self._tool_complete_thought,
            "branch_thought": self._tool_branch_thought,
            "revise_thought": self._tool_revise_thought,
            "get_thinking_state": self._tool_get_thinking_state,
            "precheck_action": self._tool_precheck_action,
            "request_approval": self._tool_request_approval,
        }

        executor = executor_map.get(tool_name)
        if not executor:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

        try:
            result = executor(**arguments)
            # ── Live-stream the result ────────────────────────────
            if _LIVE_STREAM_AVAILABLE:
                try:
                    get_stream().tool_result(tool_name, True, result, source="mcp")
                except Exception:
                    pass
            return {
                "content": [{"type": "text", "text": result}],
                "isError": False,
            }
        except Exception as e:
            error_text = f"Error: {e}\n{traceback.format_exc()}"
            if _LIVE_STREAM_AVAILABLE:
                try:
                    get_stream().tool_result(tool_name, False, error_text, source="mcp")
                except Exception:
                    pass
            return {
                "content": [{"type": "text", "text": error_text}],
                "isError": True,
            }

    def _handle_resources_list(self, params: Dict) -> Dict:
        return {
            "resources": [
                {
                    "uri": "file://project-structure",
                    "name": "Project Structure",
                    "description": "Directory tree of the CryoSUPER project",
                    "mimeType": "text/plain",
                },
                {
                    "uri": "file://readme",
                    "name": "README.md",
                    "description": "Project README",
                    "mimeType": "text/markdown",
                },
            ]
        }

    def _handle_resources_read(self, params: Dict) -> Dict:
        uri = params.get("uri", "")
        if uri == "file://project-structure":
            tree = self._get_project_tree()
            return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": tree}]}
        elif uri == "file://readme":
            readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8", errors="replace")
            return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": readme}]}
        else:
            raise ValueError(f"Unknown resource: {uri}")

    # ════════════════════════════════════════════════════════════════
    #  TOOL DEFINITIONS
    # ════════════════════════════════════════════════════════════════

    def _define_tools(self) -> List[Dict]:
        return [
            {
                "name": "read_file",
                "description": "Read file contents with optional line range.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path (absolute or relative to project root)"},
                        "start_line": {"type": "integer", "description": "Start line (1-based)", "default": 1},
                        "end_line": {"type": "integer", "description": "End line (-1 for all)", "default": -1},
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "write_file",
                "description": "Create or overwrite a file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"]
                }
            },
            {
                "name": "edit_file",
                "description": "Replace specific text in a file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"]
                }
            },
            {
                "name": "delete_file",
                "description": "Delete a file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]
                }
            },
            {
                "name": "list_directory",
                "description": "List directory contents.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": "."},
                        "recursive": {"type": "boolean", "default": False},
                    }
                }
            },
            {
                "name": "run_terminal",
                "description": "Execute a shell command. Returns stdout, stderr, and exit code.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "default": 120},
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "run_tests",
                "description": "Run pytest or forge tests.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "framework": {"type": "string", "enum": ["pytest", "forge"], "default": "pytest"},
                        "pattern": {"type": "string", "default": ""},
                    }
                }
            },
            {
                "name": "search_text",
                "description": "Search for text in project files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "include_pattern": {"type": "string", "default": ""},
                        "is_regex": {"type": "boolean", "default": False},
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "git_status",
                "description": "Get git status, diff, or log.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["status", "diff", "log"], "default": "status"},
                    }
                }
            },
            {
                "name": "check_errors",
                "description": "Check a file for syntax errors.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]
                }
            },
            {
                "name": "get_live_activity",
                "description": "Get the most recent live activity log entries. Shows what the agent has been doing.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "lines": {"type": "integer", "description": "Number of recent lines to return", "default": 50},
                    },
                }
            },
            {
                "name": "sequential_think",
                "description": "Add a thought step to the sequential reasoning chain. Use this to plan, analyze, hypothesize, and track multi-step reasoning. Types: analysis, planning, hypothesis, execution, validation, precheck, risk_assessment.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thought": {"type": "string", "description": "The thought or reasoning step"},
                        "thought_type": {"type": "string", "enum": ["analysis", "planning", "hypothesis", "execution", "validation", "revision", "precheck", "risk_assessment"], "default": "analysis"},
                        "confidence": {"type": "number", "description": "Confidence 0.0-1.0", "default": 0.8},
                        "depends_on": {"type": "array", "items": {"type": "string"}, "description": "Step IDs this depends on"},
                    },
                    "required": ["thought"]
                }
            },
            {
                "name": "complete_thought",
                "description": "Mark a thought step as completed or failed with its result.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string", "description": "The step ID to complete"},
                        "result": {"type": "string", "description": "Result or outcome of this step"},
                        "success": {"type": "boolean", "default": True},
                        "confidence": {"type": "number", "description": "Updated confidence after seeing result"},
                    },
                    "required": ["step_id", "result"]
                }
            },
            {
                "name": "branch_thought",
                "description": "Create a branch to explore an alternative solution path. Use when the current approach may not be optimal.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "branch_name": {"type": "string", "description": "Name for this alternative path"},
                        "description": {"type": "string", "description": "Why this branch is being explored"},
                        "from_step": {"type": "string", "description": "Step ID to branch from (default: current)"},
                    },
                    "required": ["branch_name", "description"]
                }
            },
            {
                "name": "revise_thought",
                "description": "Revise a previous thought step with updated understanding.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "original_step_id": {"type": "string"},
                        "revised_thought": {"type": "string"},
                        "reason": {"type": "string", "description": "Why the revision is needed"},
                    },
                    "required": ["original_step_id", "revised_thought"]
                }
            },
            {
                "name": "get_thinking_state",
                "description": "Get the current sequential thinking chain state - all active, completed, and failed steps.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "include_completed": {"type": "boolean", "default": True},
                    },
                }
            },
            {
                "name": "precheck_action",
                "description": "Forward-thinking pre-validation. Analyze a planned action BEFORE executing it. Returns risk level, suggested pre-checks, and whether approval is needed. Use this before risky operations.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "intended_action": {"type": "string", "description": "What you plan to do"},
                        "tool_name": {"type": "string", "description": "The tool you plan to call"},
                        "arguments": {"type": "object", "description": "The arguments you plan to pass"},
                    },
                    "required": ["intended_action", "tool_name"]
                }
            },
            {
                "name": "request_approval",
                "description": "Request explicit user approval for a critical action (deployment, production push, etc). The system will PAUSE until approved. Only use for deployment-critical actions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "What action needs approval"},
                        "reason": {"type": "string", "description": "Why this needs human approval"},
                        "risk_level": {"type": "string", "enum": ["medium", "high", "critical"]},
                    },
                    "required": ["action", "reason"]
                }
            },
        ]

    # ════════════════════════════════════════════════════════════════
    #  TOOL IMPLEMENTATIONS
    # ════════════════════════════════════════════════════════════════

    def _resolve(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p.resolve()

    def _tool_read_file(self, path: str, start_line: int = 1, end_line: int = -1) -> str:
        fp = self._resolve(path)
        if not fp.exists():
            raise FileNotFoundError(f"File not found: {fp}")
        content = fp.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)
        if end_line == -1:
            end_line = len(lines)
        selected = lines[max(0, start_line - 1):end_line]
        return "".join(f"{start_line + i} | {line}" for i, line in enumerate(selected))

    def _tool_write_file(self, path: str, content: str) -> str:
        fp = self._resolve(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Written {content.count(chr(10)) + 1} lines to {fp}"

    def _tool_edit_file(self, path: str, old_string: str, new_string: str) -> str:
        fp = self._resolve(path)
        content = fp.read_text(encoding="utf-8", errors="replace")
        if old_string not in content:
            raise ValueError(f"String not found in {fp}")
        if content.count(old_string) > 1:
            raise ValueError(f"Ambiguous: {content.count(old_string)} matches")
        content = content.replace(old_string, new_string, 1)
        fp.write_text(content, encoding="utf-8")
        return f"Edited {fp}"

    def _tool_delete_file(self, path: str) -> str:
        fp = self._resolve(path)
        if fp.is_file():
            fp.unlink()
            return f"Deleted {fp}"
        raise FileNotFoundError(f"Not found: {fp}")

    def _tool_list_directory(self, path: str = ".", recursive: bool = False) -> str:
        dp = self._resolve(path)
        entries = []
        skip = {".git", "__pycache__", "node_modules", ".idea"}
        items = sorted(dp.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        for item in items:
            if item.name in skip:
                continue
            suffix = "/" if item.is_dir() else ""
            entries.append(f"{item.name}{suffix}")
        return "\n".join(entries)

    def _tool_run_terminal(self, command: str, timeout: int = 120) -> str:
        # Send MCP progress notification so IDE knows work is happening
        try:
            self._send_notification("notifications/progress", {
                "progressToken": "terminal-exec",
                "progress": 0,
                "total": 100,
                "message": f"Executing: {command[:80]}",
            })
        except Exception:
            pass

        if _LIVE_STREAM_AVAILABLE:
            success, output = run_command_streamed(
                command,
                cwd=str(PROJECT_ROOT),
                timeout=timeout,
                source="mcp",
            )
            # Signal completion
            try:
                self._send_notification("notifications/progress", {
                    "progressToken": "terminal-exec",
                    "progress": 100,
                    "total": 100,
                    "message": f"Done: exit={'0' if success else 'error'}",
                })
            except Exception:
                pass
            return output
        else:
            # Fallback: original silent capture
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                cwd=str(PROJECT_ROOT), timeout=timeout, errors="replace",
            )
            parts = []
            if result.stdout:
                parts.append(result.stdout[:8000])
            if result.stderr:
                parts.append(f"STDERR: {result.stderr[:4000]}")
            parts.append(f"EXIT: {result.returncode}")
            return "\n".join(parts)

    def _tool_run_tests(self, framework: str = "pytest", pattern: str = "") -> str:
        if framework == "pytest":
            cmd = f"{sys.executable} -m pytest -v --tb=short"
            if pattern:
                cmd += f" -k {pattern}"
        else:
            cmd = "forge test -vvv"
            if pattern:
                cmd += f" --match-contract {pattern}"
        return self._tool_run_terminal(cmd, timeout=300)

    def _tool_search_text(self, query: str, include_pattern: str = "", is_regex: bool = False) -> str:
        results = []
        pat = re.compile(query, re.IGNORECASE) if is_regex else None
        for fp in PROJECT_ROOT.rglob("*"):
            if len(results) >= 50:
                break
            if not fp.is_file() or fp.stat().st_size > 1_000_000:
                continue
            rel_parts = fp.relative_to(PROJECT_ROOT).parts
            if any(p.startswith(".") or p in ("__pycache__", "node_modules", "lib") for p in rel_parts):
                continue
            if include_pattern and not fp.match(include_pattern):
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
                for num, line in enumerate(text.splitlines(), 1):
                    if len(results) >= 50:
                        break
                    if (pat and pat.search(line)) or (not pat and query.lower() in line.lower()):
                        rel = fp.relative_to(PROJECT_ROOT)
                        results.append(f"{rel}:{num}: {line.strip()}")
            except Exception:
                continue
        return "\n".join(results) if results else f"No matches for '{query}'"

    def _tool_git_status(self, operation: str = "status") -> str:
        cmds = {"status": "git status -s", "diff": "git diff", "log": "git --no-pager log --oneline -15"}
        return self._tool_run_terminal(cmds.get(operation, "git status -s"))

    def _tool_check_errors(self, path: str) -> str:
        fp = self._resolve(path)
        if fp.suffix == ".py":
            source = fp.read_text(encoding="utf-8", errors="replace")
            compile(source, str(fp), "exec")
            return f"No syntax errors in {fp.name}"
        elif fp.suffix == ".sol":
            return self._tool_run_terminal("forge build")
        return f"No checker for {fp.suffix}"

    def _tool_get_live_activity(self, lines: int = 50) -> str:
        """Return recent live activity log entries."""
        log_file = PROJECT_ROOT / ".ai" / "agent" / "logs" / "live_stream.log"
        if not log_file.exists():
            return "No live activity log found. The live stream has not been initialized yet."
        try:
            all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return f"=== Recent Activity ({len(recent)} lines) ===\n" + "\n".join(recent)
        except Exception as e:
            return f"Error reading live log: {e}"

    # ══════════════════════════════════════════════════════════════
    #  SEQUENTIAL THINKING TOOLS
    # ══════════════════════════════════════════════════════════════

    def _tool_sequential_think(self, thought: str, thought_type: str = "analysis",
                                confidence: float = 0.8, depends_on: list = None) -> str:
        if not self._thinker:
            return json.dumps({"error": "Sequential thinking engine not available"})
        result = self._thinker.add_thought(
            thought=thought, thought_type=thought_type,
            confidence=confidence, depends_on=depends_on,
        )
        return json.dumps(result, default=str)

    def _tool_complete_thought(self, step_id: str, result: str,
                                success: bool = True, confidence: float = None) -> str:
        if not self._thinker:
            return json.dumps({"error": "Sequential thinking engine not available"})
        res = self._thinker.complete_thought(step_id, result, success, confidence)
        return json.dumps(res, default=str)

    def _tool_branch_thought(self, branch_name: str, description: str,
                              from_step: str = None) -> str:
        if not self._thinker:
            return json.dumps({"error": "Sequential thinking engine not available"})
        result = self._thinker.branch_thought(branch_name, description, from_step)
        return json.dumps(result, default=str)

    def _tool_revise_thought(self, original_step_id: str, revised_thought: str,
                              reason: str = "") -> str:
        if not self._thinker:
            return json.dumps({"error": "Sequential thinking engine not available"})
        result = self._thinker.revise_thought(original_step_id, revised_thought, reason)
        return json.dumps(result, default=str)

    def _tool_get_thinking_state(self, include_completed: bool = True) -> str:
        if not self._thinker:
            return json.dumps({"error": "Sequential thinking engine not available"})
        state = self._thinker.get_state(include_completed)
        # Also include the human-readable chain
        state["chain_of_thought"] = self._thinker.get_chain_of_thought()
        return json.dumps(state, default=str)

    def _tool_precheck_action(self, intended_action: str, tool_name: str = "",
                               arguments: dict = None) -> str:
        if not self._troubleshooter:
            return json.dumps({"proceed": True, "risk_level": "unknown",
                               "message": "Troubleshooter not available"})
        result = self._troubleshooter.analyze_before_execute(tool_name, arguments or {})
        # Also log as a thinking step if available
        if self._thinker:
            self._thinker.precheck_action(intended_action, tool_name, arguments)
        return json.dumps({
            "proceed": result.proceed,
            "approval_required": result.approval_required,
            "approval_reason": result.approval_reason,
            "risk_level": result.risk_level,
            "warnings": result.warnings,
            "preflight_checks": [
                {"name": c.check_name, "description": c.description,
                 "tool": c.tool_to_run, "args": c.tool_args, "critical": c.critical}
                for c in result.preflight_checks
            ],
            "auto_fixes": result.auto_fixes,
        }, default=str)

    def _tool_request_approval(self, action: str, reason: str,
                                risk_level: str = "critical") -> str:
        """Pause and request user approval. Writes to approval file and waits."""
        approval_file = PROJECT_ROOT / ".ai" / "agent" / "logs" / "pending_approval.json"
        approval_data = {
            "action": action,
            "reason": reason,
            "risk_level": risk_level,
            "requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "PENDING",
        }
        approval_file.write_text(json.dumps(approval_data, indent=2), encoding="utf-8")

        if _LIVE_STREAM_AVAILABLE:
            try:
                get_stream().emit("approval_requested", {
                    "action": action, "reason": reason, "risk_level": risk_level,
                }, source="gate")
            except Exception:
                pass

        self._log(f"APPROVAL REQUESTED: {action} | {reason}")

        return json.dumps({
            "status": "APPROVAL_REQUIRED",
            "action": action,
            "reason": reason,
            "risk_level": risk_level,
            "instruction": (
                "STOP EXECUTION. This action requires explicit user approval. "
                "The user has been notified via the live activity stream. "
                f"Approval file: {approval_file}\n"
                "Do NOT proceed with this action until approved. "
                "Continue working on other non-critical tasks if available."
            ),
        })

    def _get_project_tree(self) -> str:
        lines = []
        skip = {".git", "__pycache__", "node_modules", ".idea", "lib"}
        for item in sorted(PROJECT_ROOT.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
            if item.name in skip:
                continue
            suffix = "/" if item.is_dir() else ""
            lines.append(f"{item.name}{suffix}")
            if item.is_dir():
                try:
                    for sub in sorted(item.iterdir())[:20]:
                        sub_suffix = "/" if sub.is_dir() else ""
                        lines.append(f"  {sub.name}{sub_suffix}")
                except PermissionError:
                    pass
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    server = MCPServer()
    server.run()
