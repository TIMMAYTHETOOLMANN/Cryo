"""
JARVIS NEXUS — Autonomous Agent Loop
ReAct-style agent loop that connects to a local LLaMA (via OpenAI-compatible API),
sends tool definitions, processes tool calls, and self-heals on errors.

Supports: Ollama, llama.cpp, LM Studio, vLLM, or any OpenAI-compatible endpoint.
"""

import json
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: 'openai' package required. Install with: pip install openai")
    sys.exit(1)

from .config import AgentConfig, TOOL_SCHEMAS
from .tools import ToolExecutor
from .self_heal import SelfHealEngine
from .session_manager import SessionManager
try:
    from .system_prompt_v3 import build_system_prompt
except ImportError:
    from .system_prompt import build_system_prompt

# ── LiveStream for real-time visibility ──────────────────────────
try:
    from .live_stream import get_stream
    _LIVE_STREAM_AVAILABLE = True
except ImportError:
    _LIVE_STREAM_AVAILABLE = False

# ── Sequential Thinking + Autonomous Controller ──────────────────
try:
    from .sequential_thinking import SequentialThinkingEngine
    from .autonomous_controller import ForwardThinkingTroubleshooter, classify_action, ApprovalLevel
    _AUTONOMY_AVAILABLE = True
except ImportError:
    _AUTONOMY_AVAILABLE = False


class AgentLoop:
    """
    Autonomous agent loop with:
    - Unlimited tool calls (configurable max as safety net)
    - Self-healing error handler with adaptive strategy shifts
    - Full conversation history management with context window awareness
    - Session logging for auditability
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig.from_env()
        self.client = OpenAI(
            base_url=self.config.llm_base_url,
            api_key=self.config.llm_api_key,
        )
        self.tools = ToolExecutor(self.config)
        self.healer = SelfHealEngine(
            identical_threshold=self.config.identical_error_threshold,
            max_total_retries=self.config.max_total_retries,
        )
        self.session: Optional[SessionManager] = None

        # Sequential Thinking + Autonomous Execution
        if _AUTONOMY_AVAILABLE:
            from .config import LOGS_DIR
            self.thinker = SequentialThinkingEngine(log_dir=LOGS_DIR)
            self.troubleshooter = ForwardThinkingTroubleshooter()
        else:
            self.thinker = None
            self.troubleshooter = None

    # ════════════════════════════════════════════════════════════════
    #  MAIN ENTRY POINT
    # ════════════════════════════════════════════════════════════════

    def run_task(self, task: str, context: str = "") -> str:
        """
        Execute a task autonomously. Returns the completion summary.

        Args:
            task: Natural language description of what to accomplish
            context: Optional additional context to inject into the prompt
        """
        self.session = SessionManager(task_description=task)
        self.session.log_user_message(task)

        system_prompt = build_system_prompt(self.config, task_context=context)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

        print(f"\n{'='*70}")
        print(f"  JARVIS NEXUS — Task Started")
        print(f"  Session: {self.session.session_id}")
        print(f"  Task: {task[:100]}")
        print(f"{'='*70}\n")

        tool_calls_made = 0
        task_completed = False
        final_summary = ""
        consecutive_empty = 0  # Track consecutive no-action responses
        MAX_CONSECUTIVE_EMPTY = 8  # Safety: bail if model is truly stuck in a loop

        # Unlimited execution — no tool_calls ceiling.
        # Only stops: task_complete signal, approval gate, escalation, or keyboard interrupt.
        while not task_completed and not self.healer.is_escalated:
            try:
                # ── Call LLM ──────────────────────────────────────
                response = self._call_llm(messages)

                if response is None:
                    print("[AGENT] LLM returned no response. Retrying...")
                    time.sleep(2)
                    consecutive_empty += 1
                    if consecutive_empty > MAX_CONSECUTIVE_EMPTY:
                        final_summary = "Agent stopped: LLM unresponsive after multiple retries."
                        break
                    continue

                consecutive_empty = 0
                message = response.choices[0].message

                # ── Check for text response (thinking/planning) ───
                if message.content:
                    print(f"\n[AGENT] {message.content[:500]}")
                    self.session.log_assistant_message(message.content)
                    if _LIVE_STREAM_AVAILABLE:
                        try:
                            get_stream().agent_thinking(message.content)
                        except Exception:
                            pass

                # ── Check for tool calls ──────────────────────────
                if not message.tool_calls:
                    if message.content and any(
                        phrase in message.content.lower()
                        for phrase in ["task complete", "done", "finished", "completed successfully"]
                    ):
                        final_summary = message.content
                        task_completed = True
                        break

                    # AUTO-CONTINUATION: don't ask, TELL the model to keep going
                    messages.append({"role": "assistant", "content": message.content or ""})
                    progress = f"Tool calls so far: {tool_calls_made}."
                    if self.troubleshooter and tool_calls_made > 0:
                        # Use the troubleshooter's smart continuation prompt
                        last_res = {"success": True, "output": message.content or ""}
                        continuation = self.troubleshooter.get_auto_continuation_prompt(last_res, progress)
                    else:
                        continuation = (
                            f"{progress} CONTINUE working. Do NOT stop or ask for permission. "
                            "Make your next tool call NOW. If fully done, call task_complete."
                        )
                    messages.append({"role": "user", "content": continuation})
                    continue

                # ── Process tool calls ────────────────────────────
                assistant_msg = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        }
                        for tc in message.tool_calls
                    ]
                }
                messages.append(assistant_msg)

                for tool_call in message.tool_calls:
                    tool_calls_made += 1
                    tool_name = tool_call.function.name
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                    # ── Forward-thinking pre-check ────────────────
                    if self.troubleshooter and tool_name == "run_terminal":
                        pre = self.troubleshooter.analyze_before_execute(tool_name, arguments)
                        if pre.approval_required:
                            # APPROVAL GATE: stop execution for this action
                            gate_msg = (
                                f"APPROVAL REQUIRED: {pre.approval_reason}\n"
                                f"Command: {arguments.get('command', '?')}\n"
                                "This action has been BLOCKED pending user approval. "
                                "Skip this and continue with other work, or call task_complete."
                            )
                            print(f"\n  [APPROVAL GATE] {pre.approval_reason}")
                            if _LIVE_STREAM_AVAILABLE:
                                try:
                                    get_stream().emit("approval_gate", {
                                        "reason": pre.approval_reason,
                                        "command": arguments.get("command", "")[:100],
                                    }, source="gate")
                                except Exception:
                                    pass
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": gate_msg,
                            })
                            continue
                        if pre.warnings:
                            for w in pre.warnings:
                                print(f"       [WARN] {w}")

                    self.session.log_tool_call(tool_name, arguments)
                    print(f"\n  [{tool_calls_made}] {tool_name}({self._format_args(arguments)})")

                    # ── Live-stream the tool call ─────────────────
                    if _LIVE_STREAM_AVAILABLE:
                        try:
                            get_stream().tool_call(tool_name, arguments)
                        except Exception:
                            pass

                    # Execute the tool
                    result = self.tools.execute(tool_name, arguments)
                    success = result.get("success", False)
                    output = result.get("output", "")

                    self.session.log_tool_result(tool_name, success, output)

                    status = "OK" if success else "ERR"
                    print(f"       -> [{status}] {output[:200]}")

                    # ── Live-stream the tool result ───────────────
                    if _LIVE_STREAM_AVAILABLE:
                        try:
                            get_stream().tool_result(tool_name, success, output)
                        except Exception:
                            pass

                    # Track in troubleshooter for pattern detection
                    if self.troubleshooter:
                        if success:
                            self.troubleshooter.record_success(tool_name)
                        else:
                            self.troubleshooter.record_failure(tool_name, arguments, output)

                    # Check for task completion signal
                    if result.get("task_complete"):
                        final_summary = output
                        task_completed = True
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": output,
                        })
                        break

                    # Self-heal check
                    recovery = self.healer.record_result(tool_name, arguments, success, output)
                    tool_output = output
                    if recovery:
                        print(f"\n  [RECOVERY] {recovery.strategy_name}: {recovery.instruction[:100]}")
                        self.session.log_recovery(recovery.strategy_name, recovery.instruction)
                        tool_output = f"{output}\n\n--- SYSTEM RECOVERY DIRECTIVE ---\n{recovery.instruction}"
                        if _LIVE_STREAM_AVAILABLE:
                            try:
                                get_stream().recovery(recovery.strategy_name, recovery.instruction)
                            except Exception:
                                pass

                    # ── Auto-continuation injection ───────────────
                    # After each tool result, inject a continuation
                    # directive so the model never stalls
                    if not success and self.troubleshooter:
                        progress = f"[{tool_calls_made} calls, {self.healer.get_stats()['total_errors']} errors]"
                        fix_prompt = self.troubleshooter.get_auto_continuation_prompt(result, progress)
                        tool_output = f"{tool_output}\n\n--- AUTO-CONTINUE ---\n{fix_prompt}"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_output,
                    })

                if task_completed:
                    break

                # ── Context window management ─────────────────────
                messages = self._trim_context(messages)

            except KeyboardInterrupt:
                print("\n[AGENT] Interrupted by user.")
                final_summary = "Task interrupted by user."
                break
            except Exception as e:
                print(f"\n[AGENT ERROR] {type(e).__name__}: {e}")
                traceback.print_exc()
                # Try to recover from LLM errors
                time.sleep(3)
                continue

        # ── Session complete ──────────────────────────────────────
        if not final_summary:
            if self.healer.is_escalated:
                final_summary = (
                    f"Task escalated after {self.healer.get_stats()['total_errors']} errors. "
                    f"{self.healer.get_error_summary()}"
                )
            else:
                final_summary = "Task ended without explicit completion signal."

        stats = self.healer.get_stats()
        self.session.log_completion(final_summary, stats)

        print(f"\n{'='*70}")
        print(f"  JARVIS NEXUS — Task {'Completed' if task_completed else 'Ended'}")
        print(f"  Tool Calls: {tool_calls_made}")
        print(f"  Errors: {stats['total_errors']} | Successes: {stats['total_successes']}")
        print(f"  Session Log: {self.session.session_file}")
        print(f"{'='*70}\n")

        return final_summary

    # ════════════════════════════════════════════════════════════════
    #  LLM INTERFACE
    # ════════════════════════════════════════════════════════════════

    def _call_llm(self, messages: List[Dict]) -> Any:
        """Call the local LLM with tool definitions."""
        try:
            response = self.client.chat.completions.create(
                model=self.config.llm_model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=self.config.temperature,
                max_tokens=self.config.max_output_tokens,
            )
            return response
        except Exception as e:
            error_msg = str(e)
            # Handle common local LLM issues
            if "tool" in error_msg.lower() or "function" in error_msg.lower():
                # Model doesn't support function calling — fall back to prompt-based
                print(f"[AGENT] Tool calling not supported by endpoint. Trying prompt-based mode...")
                return self._call_llm_promptbased(messages)
            raise

    def _call_llm_promptbased(self, messages: List[Dict]) -> Any:
        """
        Fallback for models/endpoints that don't support OpenAI function calling.
        Injects tool descriptions into the prompt and parses JSON tool calls from output.
        """
        # Convert tools to prompt text
        tool_desc = self._tools_to_prompt_text()

        # Rebuild messages without tool-specific fields
        clean_messages = []
        for msg in messages:
            if msg["role"] == "system":
                clean_messages.append({
                    "role": "system",
                    "content": msg["content"] + "\n\n" + tool_desc,
                })
            elif msg["role"] == "tool":
                clean_messages.append({
                    "role": "user",
                    "content": f"[Tool Result]: {msg['content']}",
                })
            elif msg["role"] == "assistant" and "tool_calls" in msg:
                tc_text = ""
                for tc in msg["tool_calls"]:
                    tc_text += f"\n[Tool Call]: {tc['function']['name']}({tc['function']['arguments']})"
                clean_messages.append({
                    "role": "assistant",
                    "content": (msg.get("content", "") + tc_text).strip(),
                })
            else:
                clean_messages.append({"role": msg["role"], "content": msg.get("content", "")})

        response = self.client.chat.completions.create(
            model=self.config.llm_model,
            messages=clean_messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_output_tokens,
        )

        # Parse tool calls from the response text
        self._parse_tool_calls_from_text(response)
        return response

    def _parse_tool_calls_from_text(self, response):
        """
        Parse tool calls from plain text response.
        Looks for JSON blocks like: {"tool": "name", "arguments": {...}}
        """
        import re
        from types import SimpleNamespace

        content = response.choices[0].message.content or ""

        # Find JSON tool call patterns
        patterns = [
            r'```json\s*(\{[^`]+\})\s*```',  # ```json {...} ```
            r'\{"tool":\s*"[^"]+",\s*"arguments":\s*\{[^}]+\}\}',  # inline JSON
        ]

        tool_calls = []
        for pattern in patterns:
            matches = re.findall(pattern, content, re.DOTALL)
            for match in matches:
                try:
                    data = json.loads(match)
                    if "tool" in data and "arguments" in data:
                        tc = SimpleNamespace(
                            id=f"call_{len(tool_calls)}",
                            function=SimpleNamespace(
                                name=data["tool"],
                                arguments=json.dumps(data["arguments"]),
                            )
                        )
                        tool_calls.append(tc)
                except json.JSONDecodeError:
                    continue

        if tool_calls:
            response.choices[0].message.tool_calls = tool_calls

    def _tools_to_prompt_text(self) -> str:
        """Convert tool schemas to a text description for prompt-based calling."""
        lines = ["## Tool Calling Format",
                 "To use a tool, include a JSON block in your response:",
                 '```json',
                 '{"tool": "tool_name", "arguments": {"param1": "value1"}}',
                 '```',
                 "",
                 "Available tools:"]
        for schema in TOOL_SCHEMAS:
            func = schema["function"]
            params = func.get("parameters", {}).get("properties", {})
            param_list = ", ".join(f'{k}: {v.get("type", "string")}' for k, v in params.items())
            lines.append(f"- **{func['name']}**({param_list}): {func['description']}")
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════════════
    #  CONTEXT WINDOW MANAGEMENT
    # ════════════════════════════════════════════════════════════════

    def _trim_context(self, messages: List[Dict]) -> List[Dict]:
        """
        Keep conversation within context window limits.
        Strategy: preserve system prompt + last N messages, summarize middle.
        """
        # Rough token estimate: 1 token ~= 4 chars
        total_chars = sum(len(json.dumps(m, default=str)) for m in messages)
        max_chars = self.config.max_context_tokens * 3  # Conservative: 3 chars/token

        if total_chars <= max_chars:
            return messages

        # Keep system prompt (first) and last 20 messages
        system = messages[:1]
        keep_last = 20
        recent = messages[-keep_last:] if len(messages) > keep_last else messages[1:]
        middle = messages[1:-keep_last] if len(messages) > keep_last + 1 else []

        if middle:
            # Summarize the middle section
            tool_calls_in_middle = sum(
                1 for m in middle if m.get("role") == "tool" or "tool_calls" in m
            )
            summary_msg = {
                "role": "user",
                "content": (
                    f"[CONTEXT SUMMARY: {len(middle)} earlier messages trimmed. "
                    f"Contained {tool_calls_in_middle} tool interactions. "
                    "Continue from the most recent context below.]"
                )
            }
            return system + [summary_msg] + recent
        return system + recent

    # ════════════════════════════════════════════════════════════════
    #  UTILITIES
    # ════════════════════════════════════════════════════════════════

    @staticmethod
    def _format_args(args: Dict) -> str:
        """Format tool arguments for console display."""
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 60:
                sv = sv[:57] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts)


# ════════════════════════════════════════════════════════════════════
#  INTERACTIVE MODE
# ════════════════════════════════════════════════════════════════════

def interactive_mode(config: Optional[AgentConfig] = None):
    """Run the agent in interactive REPL mode."""
    agent = AgentLoop(config)

    print("\n" + "="*70)
    print("  JARVIS NEXUS — Interactive Agent Mode")
    print("  Type a task and press Enter. Type 'quit' to exit.")
    print("  Type 'status' for system info. Type 'sessions' to list logs.")
    print("="*70 + "\n")

    while True:
        try:
            task = input("\n[YOU] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not task:
            continue
        if task.lower() in ("quit", "exit", "q"):
            print("Shutting down JARVIS NEXUS.")
            break
        if task.lower() == "status":
            print(f"  Model: {agent.config.llm_model}")
            print(f"  Endpoint: {agent.config.llm_base_url}")
            print(f"  Context: {agent.config.max_context_tokens} tokens")
            print(f"  Project: {agent.config.project_root}")
            continue
        if task.lower() == "sessions":
            from .config import SESSIONS_DIR
            sessions = sorted(SESSIONS_DIR.glob("session_*.jsonl"))
            for s in sessions[-10:]:
                print(f"  {s.name}")
            if not sessions:
                print("  No sessions yet.")
            continue

        result = agent.run_task(task)
        print(f"\n[RESULT] {result[:500]}")
