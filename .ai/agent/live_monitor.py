 #!/usr/bin/env python3
"""
JARVIS NEXUS — Live Monitor
═══════════════════════════════════════════════════════════════
Run this in a separate terminal to watch agent activity in real-time.

Usage:
    python .ai/agent/live_monitor.py              # watch live (default)
    python .ai/agent/live_monitor.py --json        # watch JSON events
    python .ai/agent/live_monitor.py --history 50  # show last 50 lines then follow
    python .ai/agent/live_monitor.py --clear        # clear log and watch

This is equivalent to the terminal window that pops up in Copilot/JetBrains AI
when an agent runs a command — except it shows ALL agent activity.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

# ════════════════════════════════════════════════════════════════════
#  PATHS
# ════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"
LIVE_LOG = LOGS_DIR / "live_stream.log"
LIVE_EVENTS = LOGS_DIR / "live_events.jsonl"

# ════════════════════════════════════════════════════════════════════
#  ANSI COLORS (Windows 10+ supports ANSI in terminals)
# ════════════════════════════════════════════════════════════════════

class C:
    """ANSI color codes."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GRAY    = "\033[90m"
    BG_RED  = "\033[41m"
    BG_GREEN= "\033[42m"


def enable_ansi_windows():
    """Enable ANSI escape codes on Windows 10+."""
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════
#  COLORIZED LINE FORMATTER
# ════════════════════════════════════════════════════════════════════

def colorize_line(line: str) -> str:
    """Apply colors to a log line based on content."""
    if not line.strip():
        return line

    # Tool calls
    if "▶" in line:
        return f"{C.CYAN}{C.BOLD}{line}{C.RESET}"
    # Success results
    if "✓" in line:
        return f"{C.GREEN}{line}{C.RESET}"
    # Failed results
    if "✗" in line or "❌" in line:
        return f"{C.RED}{line}{C.RESET}"
    # Terminal command
    if "$ " in line and "[" in line:
        return f"{C.YELLOW}{C.BOLD}{line}{C.RESET}"
    # Terminal output lines
    if "│ " in line:
        return f"{C.DIM}{line}{C.RESET}"
    if "│err" in line:
        return f"{C.RED}{C.DIM}{line}{C.RESET}"
    # Terminal done
    if "└─" in line:
        if "✓" in line:
            return f"{C.GREEN}{line}{C.RESET}"
        return f"{C.RED}{line}{C.RESET}"
    # Thinking
    if "💭" in line:
        return f"{C.MAGENTA}{line}{C.RESET}"
    # Recovery
    if "🔧" in line:
        return f"{C.YELLOW}{line}{C.RESET}"
    # Header/separator
    if line.startswith("="):
        return f"{C.BLUE}{C.BOLD}{line}{C.RESET}"

    return line


def format_json_event(event: dict) -> str:
    """Format a JSON event as a colored one-liner."""
    ts = event.get("timestamp", "")
    src = event.get("source", "?")
    etype = event.get("type", "?")
    data = event.get("data", {})

    tag = f"{C.GRAY}[{ts}]{C.RESET} {C.BLUE}[{src}]{C.RESET}"

    if etype == "tool_call":
        return f"{tag} {C.CYAN}▶ {data.get('tool', '?')}({data.get('arguments', '')}){C.RESET}"
    elif etype == "tool_result":
        ok = data.get("success", False)
        icon = f"{C.GREEN}✓{C.RESET}" if ok else f"{C.RED}✗{C.RESET}"
        return f"{tag}   {icon} {data.get('output', '')[:120]}"
    elif etype == "terminal_start":
        return f"{tag} {C.YELLOW}$ {data.get('command', '')}{C.RESET}"
    elif etype == "terminal_output":
        stream = data.get("stream", "stdout")
        prefix = f"{C.DIM}│{C.RESET}" if stream == "stdout" else f"{C.RED}│err{C.RESET}"
        return f"{tag} {prefix} {data.get('line', '')}"
    elif etype == "terminal_done":
        code = data.get("exit_code", -1)
        icon = f"{C.GREEN}✓{C.RESET}" if code == 0 else f"{C.RED}✗ exit={code}{C.RESET}"
        return f"{tag} {C.DIM}└─{C.RESET} {icon}"
    elif etype == "thinking":
        return f"{tag} {C.MAGENTA}💭 {data.get('message', '')[:200]}{C.RESET}"
    elif etype == "error":
        return f"{tag} {C.RED}❌ {data.get('error', '')}{C.RESET}"
    elif etype == "recovery":
        return f"{tag} {C.YELLOW}🔧 [{data.get('strategy','')}] {data.get('instruction','')[:120]}{C.RESET}"
    else:
        return f"{tag} {etype}: {json.dumps(data, default=str)[:150]}"


# ════════════════════════════════════════════════════════════════════
#  TAIL FOLLOW
# ════════════════════════════════════════════════════════════════════

def tail_follow(filepath: Path, history: int = 20, use_json: bool = False):
    """
    Follow a file like `tail -f`, printing new lines as they appear.
    Shows last `history` lines on start.
    """
    print(f"\n{C.BLUE}{C.BOLD}{'═'*70}{C.RESET}")
    print(f"{C.BLUE}{C.BOLD}  JARVIS NEXUS — Live Activity Monitor{C.RESET}")
    print(f"{C.GRAY}  Watching: {filepath}{C.RESET}")
    print(f"{C.GRAY}  Press Ctrl+C to stop{C.RESET}")
    print(f"{C.BLUE}{C.BOLD}{'═'*70}{C.RESET}\n")

    # Wait for file to exist
    while not filepath.exists():
        print(f"{C.YELLOW}Waiting for activity log to be created...{C.RESET}", end="\r")
        time.sleep(1)

    # Show recent history
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        if history > 0 and lines:
            start = max(0, len(lines) - history)
            if start > 0:
                print(f"{C.GRAY}  ... ({start} earlier lines omitted) ...{C.RESET}\n")
            for line in lines[start:]:
                if use_json:
                    try:
                        event = json.loads(line)
                        print(format_json_event(event))
                    except json.JSONDecodeError:
                        print(line.rstrip())
                else:
                    print(colorize_line(line.rstrip()))
    except Exception:
        pass

    print(f"\n{C.GREEN}{'─'*50} live {'─'*50}{C.RESET}\n")

    # Follow new lines
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        # Seek to end
        f.seek(0, 2)

        while True:
            line = f.readline()
            if line:
                if use_json:
                    try:
                        event = json.loads(line)
                        print(format_json_event(event))
                    except json.JSONDecodeError:
                        print(line.rstrip())
                else:
                    print(colorize_line(line.rstrip()))
            else:
                time.sleep(0.1)  # Poll interval


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    enable_ansi_windows()

    parser = argparse.ArgumentParser(
        description="JARVIS NEXUS Live Activity Monitor — watch agent operations in real-time"
    )
    parser.add_argument("--json", action="store_true",
                        help="Watch JSON events file instead of human-readable log")
    parser.add_argument("--history", type=int, default=30,
                        help="Number of recent lines to show on start (default: 30)")
    parser.add_argument("--clear", action="store_true",
                        help="Clear logs before watching")
    args = parser.parse_args()

    target_file = LIVE_EVENTS if args.json else LIVE_LOG

    if args.clear:
        for f in (LIVE_LOG, LIVE_EVENTS):
            try:
                f.write_text("", encoding="utf-8")
            except Exception:
                pass
        print(f"{C.YELLOW}Logs cleared.{C.RESET}")

    try:
        tail_follow(target_file, history=args.history, use_json=args.json)
    except KeyboardInterrupt:
        print(f"\n{C.GRAY}Monitor stopped.{C.RESET}")


if __name__ == "__main__":
    main()
