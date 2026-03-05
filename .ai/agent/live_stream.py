"""
JARVIS NEXUS — Live Activity Stream
Real-time broadcast of all agent/MCP activity to a shared log file.
Any terminal can watch it with: python .ai/agent/live_monitor.py

This module provides a centralized, thread-safe stream that:
  - Writes timestamped events to a persistent log file
  - Emits events to stderr for MCP server visibility
  - Provides line-by-line subprocess streaming (replaces capture_output=True)
"""

import io
import os
import sys
import json
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Tuple

# ════════════════════════════════════════════════════════════════════
#  PATHS
# ════════════════════════════════════════════════════════════════════

_AGENT_DIR = Path(__file__).resolve().parent
_LOGS_DIR = _AGENT_DIR / "logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

LIVE_LOG_FILE = _LOGS_DIR / "live_stream.log"
LIVE_EVENTS_FILE = _LOGS_DIR / "live_events.jsonl"  # Machine-readable


# ════════════════════════════════════════════════════════════════════
#  LIVE STREAM SINGLETON
# ════════════════════════════════════════════════════════════════════

class LiveStream:
    """
    Thread-safe live activity broadcaster.
    Writes to both a human-readable log and a machine-readable JSONL file.
    """

    _instance: Optional["LiveStream"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._write_lock = threading.Lock()
        self._subscribers: list[Callable[[Dict], None]] = []

        # Clear previous session log on start
        try:
            LIVE_LOG_FILE.write_text(
                f"{'='*70}\n"
                f"  JARVIS NEXUS — Live Activity Stream\n"
                f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"  Watch with: python .ai/agent/live_monitor.py\n"
                f"{'='*70}\n\n",
                encoding="utf-8"
            )
        except Exception:
            pass

    # ── Public API ────────────────────────────────────────────────

    def emit(self, event_type: str, data: Dict[str, Any], source: str = "agent"):
        """Emit a live event. Thread-safe."""
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        event = {
            "timestamp": ts,
            "source": source,
            "type": event_type,
            "data": data,
        }

        with self._write_lock:
            # Human-readable log
            self._write_human_line(ts, source, event_type, data)
            # Machine-readable JSONL
            self._write_jsonl(event)
            # Stderr echo (visible in MCP server terminal)
            self._echo_stderr(ts, source, event_type, data)
            # Notify subscribers
            for sub in self._subscribers:
                try:
                    sub(event)
                except Exception:
                    pass

    def tool_call(self, tool_name: str, arguments: Dict, source: str = "agent"):
        """Log a tool invocation."""
        self.emit("tool_call", {
            "tool": tool_name,
            "arguments": self._summarize_args(arguments),
        }, source=source)

    def tool_result(self, tool_name: str, success: bool, output: str, source: str = "agent"):
        """Log a tool result."""
        self.emit("tool_result", {
            "tool": tool_name,
            "success": success,
            "output": output[:500] if len(output) > 500 else output,
        }, source=source)

    def terminal_start(self, command: str, source: str = "agent"):
        """Log terminal command start."""
        self.emit("terminal_start", {"command": command}, source=source)

    def terminal_line(self, stream: str, line: str, source: str = "agent"):
        """Log a single line of terminal output (real-time streaming)."""
        self.emit("terminal_output", {
            "stream": stream,
            "line": line.rstrip(),
        }, source=source)

    def terminal_done(self, command: str, exit_code: int, source: str = "agent"):
        """Log terminal command completion."""
        self.emit("terminal_done", {
            "command": command[:80],
            "exit_code": exit_code,
        }, source=source)

    def agent_thinking(self, message: str):
        """Log agent thought/plan."""
        self.emit("thinking", {"message": message[:300]}, source="agent")

    def agent_error(self, error: str, source: str = "agent"):
        """Log an error."""
        self.emit("error", {"error": error[:500]}, source=source)

    def recovery(self, strategy: str, instruction: str):
        """Log a self-heal recovery action."""
        self.emit("recovery", {
            "strategy": strategy,
            "instruction": instruction[:200],
        }, source="self-heal")

    def subscribe(self, callback: Callable[[Dict], None]):
        """Add a subscriber for live events."""
        self._subscribers.append(callback)

    # ── Internal Writers ──────────────────────────────────────────

    def _write_human_line(self, ts: str, source: str, event_type: str, data: Dict):
        """Append a human-readable line to the live log."""
        try:
            tag = f"[{ts}] [{source.upper():^6}]"

            if event_type == "tool_call":
                line = f"{tag} ▶ {data['tool']}({data['arguments']})\n"
            elif event_type == "tool_result":
                status = "✓" if data["success"] else "✗"
                line = f"{tag}   {status} {data['output'][:200]}\n"
            elif event_type == "terminal_start":
                line = f"{tag} $ {data['command']}\n"
            elif event_type == "terminal_output":
                prefix = "│ " if data["stream"] == "stdout" else "│err "
                line = f"{tag} {prefix}{data['line']}\n"
            elif event_type == "terminal_done":
                code = data["exit_code"]
                indicator = "✓" if code == 0 else f"✗ exit={code}"
                line = f"{tag} └─ {indicator}\n"
            elif event_type == "thinking":
                line = f"{tag} 💭 {data['message'][:200]}\n"
            elif event_type == "error":
                line = f"{tag} ❌ {data['error'][:200]}\n"
            elif event_type == "recovery":
                line = f"{tag} 🔧 [{data['strategy']}] {data['instruction'][:150]}\n"
            else:
                line = f"{tag} {event_type}: {json.dumps(data, default=str)[:200]}\n"

            with open(LIVE_LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
                f.write(line)
        except Exception:
            pass

    def _write_jsonl(self, event: Dict):
        """Append machine-readable event to JSONL file."""
        try:
            with open(LIVE_EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _echo_stderr(self, ts: str, source: str, event_type: str, data: Dict):
        """Echo to stderr so it's visible in MCP server terminal."""
        try:
            if event_type in ("terminal_output",):
                # Don't echo every output line to stderr (too noisy)
                return
            msg = f"[LIVE {ts}] [{source}] {event_type}"
            if event_type == "tool_call":
                msg += f": {data['tool']}({data['arguments']})"
            elif event_type == "terminal_start":
                msg += f": $ {data['command'][:80]}"
            elif event_type == "terminal_done":
                msg += f": exit={data['exit_code']}"
            print(msg, file=sys.stderr, flush=True)
        except Exception:
            pass

    @staticmethod
    def _summarize_args(args: Dict) -> str:
        """Compact summary of tool arguments."""
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 60:
                sv = sv[:57] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts)


# ════════════════════════════════════════════════════════════════════
#  STREAMING SUBPROCESS RUNNER
# ════════════════════════════════════════════════════════════════════

def run_command_streamed(
    command: str,
    cwd: str = ".",
    timeout: int = 120,
    env: Optional[Dict] = None,
    source: str = "agent",
    max_output: int = 8000,
) -> Tuple[bool, str]:
    """
    Execute a shell command with real-time line-by-line streaming to the LiveStream.
    Returns (success: bool, full_output: str).
    """
    stream = LiveStream()
    stream.terminal_start(command, source=source)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    stdout_lines = 0
    stderr_lines = 0

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env or os.environ.copy(),
            errors="replace",
            bufsize=1,  # Line-buffered
        )

        # Read stdout and stderr in parallel threads
        def _read_pipe(pipe, buf, stream_name):
            nonlocal stdout_lines, stderr_lines
            try:
                for line in iter(pipe.readline, ""):
                    if not line:
                        break
                    buf.write(line)
                    stream.terminal_line(stream_name, line, source=source)
                    if stream_name == "stdout":
                        stdout_lines += 1
                    else:
                        stderr_lines += 1
            except Exception:
                pass
            finally:
                pipe.close()

        t_out = threading.Thread(target=_read_pipe, args=(proc.stdout, stdout_buf, "stdout"), daemon=True)
        t_err = threading.Thread(target=_read_pipe, args=(proc.stderr, stderr_buf, "stderr"), daemon=True)
        t_out.start()
        t_err.start()

        # Wait for completion with timeout
        proc.wait(timeout=timeout)
        t_out.join(timeout=5)
        t_err.join(timeout=5)

        exit_code = proc.returncode
        stream.terminal_done(command, exit_code, source=source)

        # Build combined output for the tool result
        stdout_text = stdout_buf.getvalue()
        stderr_text = stderr_buf.getvalue()

        if len(stdout_text) > max_output:
            stdout_text = stdout_text[:max_output] + f"\n... [truncated, {len(stdout_buf.getvalue())} total chars]"
        if len(stderr_text) > max_output:
            stderr_text = stderr_text[:max_output] + f"\n... [truncated, {len(stderr_buf.getvalue())} total chars]"

        parts = []
        if stdout_text.strip():
            parts.append(f"STDOUT:\n{stdout_text}")
        if stderr_text.strip():
            parts.append(f"STDERR:\n{stderr_text}")
        parts.append(f"EXIT CODE: {exit_code}")

        return (exit_code == 0, "\n".join(parts))

    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        stream.terminal_done(command, -1, source=source)
        stream.agent_error(f"Command timed out after {timeout}s: {command}", source=source)
        return (False, f"Command timed out after {timeout}s: {command}")
    except Exception as e:
        stream.agent_error(f"Terminal error: {e}", source=source)
        return (False, f"Terminal error: {e}")


# ════════════════════════════════════════════════════════════════════
#  MODULE-LEVEL CONVENIENCE
# ════════════════════════════════════════════════════════════════════

def get_stream() -> LiveStream:
    """Get the singleton LiveStream instance."""
    return LiveStream()
