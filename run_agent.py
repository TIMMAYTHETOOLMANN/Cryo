#!/usr/bin/env python3
"""
JARVIS NEXUS — Autonomous Agent Runner
Single entry point for launching the autonomous coding agent.

Usage:
  python run_agent.py                          # Interactive mode (REPL)
  python run_agent.py --task "fix the bug"     # Single task mode
  python run_agent.py --test                   # Run self-diagnostics
  python run_agent.py --status                 # Print system status

Environment (set in .env or export):
  LLM_BASE_URL    = http://localhost:11434/v1   (Ollama default)
  LLM_MODEL       = llama3                       (model name)
  LLM_API_KEY     = not-needed                   (for local models)
  LLM_MAX_CONTEXT = 8192                         (context window)
"""
 
import argparse
import os
import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Also add .ai directory to path for agent package imports
AI_DIR = PROJECT_ROOT / ".ai"
sys.path.insert(0, str(AI_DIR))

# Encoding safety (Windows)
os.environ['PYTHONIOENCODING'] = 'utf-8'
for _s in ('stdout', 'stderr'):
    _stream = getattr(sys, _s)
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def check_dependencies():
    """Verify required packages are installed."""
    missing = []
    try:
        import openai
    except ImportError:
        missing.append("openai")
    try:
        from dotenv import load_dotenv
    except ImportError:
        missing.append("python-dotenv")

    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print(f"Install with: pip install {' '.join(missing)}")
        sys.exit(1)


def run_diagnostics():
    """Run self-diagnostic tests to verify the agent system works."""
    print("\n" + "="*70)
    print("  JARVIS NEXUS — System Diagnostics")
    print("="*70)

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    # 1. Config
    print("\n[1/6] Loading configuration...")
    from agent.config import AgentConfig
    config = AgentConfig.from_env()
    print(f"  Model: {config.llm_model}")
    print(f"  Endpoint: {config.llm_base_url}")
    print(f"  Context: {config.max_context_tokens} tokens")
    print(f"  Project: {config.project_root}")
    print("  -> OK")

    # 2. Tool executor
    print("\n[2/6] Testing tool executor...")
    from agent.tools import ToolExecutor
    tools = ToolExecutor(config)

    result = tools.execute("list_directory", {"path": "."})
    print(f"  list_directory: {'OK' if result['success'] else 'FAIL'}")

    result = tools.execute("run_terminal", {"command": f'"{sys.executable}" --version'})
    print(f"  run_terminal: {'OK' if result['success'] else 'FAIL'} ({result['output'][:50]})")

    # Write + read + delete cycle
    test_file = ".ai/agent/_diag_test.tmp"
    result = tools.execute("write_file", {"path": test_file, "content": "JARVIS NEXUS DIAG"})
    print(f"  write_file: {'OK' if result['success'] else 'FAIL'}")
    result = tools.execute("read_file", {"path": test_file})
    print(f"  read_file: {'OK' if result['success'] else 'FAIL'}")
    result = tools.execute("delete_file", {"path": test_file})
    print(f"  delete_file: {'OK' if result['success'] else 'FAIL'}")

    result = tools.execute("search_text", {"query": "JARVIS", "include_pattern": "*.py", "max_results": 5})
    print(f"  search_text: {'OK' if result['success'] else 'FAIL'} (found matches)")

    result = tools.execute("check_errors", {"path": "main.py"})
    print(f"  check_errors: {'OK' if result['success'] else 'FAIL'}")

    # 3. Self-heal engine
    print("\n[3/6] Testing self-heal engine...")
    from agent.self_heal import SelfHealEngine
    healer = SelfHealEngine(identical_threshold=3)
    for i in range(3):
        r = healer.record_result("test_tool", {"a": 1}, False, "same error")
    stats = healer.get_stats()
    assert stats["recovery_attempts"] == 1, f"Expected 1 recovery attempt, got {stats['recovery_attempts']}"
    print(f"  Adaptive recovery triggers at threshold: PASS")

    # Test that successes reset
    for i in range(5):
        healer.record_result("test_tool", {"a": 1}, True, "ok")
    assert healer.get_stats()["recovery_attempts"] == 0, "Should reset after sustained success"
    print(f"  Recovery resets after success streak: PASS")
    print("  -> OK")

    # 4. Session manager
    print("\n[4/6] Testing session manager...")
    from agent.session_manager import SessionManager
    session = SessionManager("diagnostic test")
    session.log_tool_call("test", {"arg": "val"})
    session.log_tool_result("test", True, "success")
    session.log_completion("diagnostic complete", {})
    print(f"  Session file: {session.session_file.name}")
    # Cleanup
    try:
        session.session_file.unlink(missing_ok=True)
    except Exception:
        pass
    print("  -> OK")

    # 5. System prompt
    print("\n[5/6] Testing system prompt builder...")
    from agent.system_prompt import build_system_prompt
    prompt = build_system_prompt(config)
    print(f"  Prompt length: {len(prompt)} chars (~{len(prompt)//4} tokens)")
    assert "JARVIS NEXUS" in prompt
    assert "read_file" in prompt
    assert "run_terminal" in prompt
    print("  -> OK")

    # 6. LLM connectivity
    print("\n[6/6] Testing LLM connectivity...")
    try:
        from openai import OpenAI
        client = OpenAI(base_url=config.llm_base_url, api_key=config.llm_api_key)
        response = client.chat.completions.create(
            model=config.llm_model,
            messages=[{"role": "user", "content": "Say 'NEXUS ONLINE' and nothing else."}],
            max_tokens=20,
        )
        reply = response.choices[0].message.content.strip()
        print(f"  LLM Response: {reply}")
        print("  -> OK")
    except Exception as e:
        print(f"  LLM Connection FAILED: {type(e).__name__}: {e}")
        print("  -> This is expected if your LLM server isn't running yet.")
        print(f"     Expected endpoint: {config.llm_base_url}")
        print(f"     Expected model: {config.llm_model}")
        print("")
        print("  Quick-start options:")
        print("    Ollama:    ollama serve  &&  ollama pull llama3")
	@FindBy(css="")
	private WebElement webElement;
        print("    LM Studio: Start app -> Download model -> Start server")
        print("    llama.cpp: ./server -m model.gguf --port 8080")

    print("\n" + "="*70)
    print("  Diagnostics Complete")
    print("="*70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="JARVIS NEXUS — Autonomous Coding Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_agent.py                             Interactive mode
  python run_agent.py --task "add error handling to main.py"
  python run_agent.py --task "run all tests and fix failures"
  python run_agent.py --test                      Self-diagnostics
  python run_agent.py --status                    System info

Environment Variables (set in .env):
  LLM_BASE_URL    Ollama: http://localhost:11434/v1
                  llama.cpp: http://localhost:8080/v1
                  LM Studio: http://localhost:1234/v1
  LLM_MODEL       Model name (e.g., llama3, codellama, qwen2.5-coder)
  LLM_MAX_CONTEXT Context window size in tokens
        """
    )
    parser.add_argument("--task", "-t", type=str, help="Task to execute (single-shot mode)")
    parser.add_argument("--context", "-c", type=str, default="", help="Additional context for the task")
    parser.add_argument("--test", action="store_true", help="Run self-diagnostics")
    parser.add_argument("--status", action="store_true", help="Print system status")
    parser.add_argument("--model", type=str, help="Override LLM model name")
    parser.add_argument("--endpoint", type=str, help="Override LLM endpoint URL")

    args = parser.parse_args()

    check_dependencies()

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    # Apply CLI overrides
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    if args.endpoint:
        os.environ["LLM_BASE_URL"] = args.endpoint

    if args.test:
        run_diagnostics()
        return

    from agent.config import AgentConfig
    config = AgentConfig.from_env()

    if args.status:
        print(f"\nJARVIS NEXUS Status:")
        print(f"  Endpoint:  {config.llm_base_url}")
        print(f"  Model:     {config.llm_model}")
        print(f"  Context:   {config.max_context_tokens} tokens")
        print(f"  Max Tools: {config.max_tool_calls}")
        print(f"  Project:   {config.project_root}")
        print(f"  Temp:      {config.temperature}")

        from agent.config import SESSIONS_DIR
        sessions = list(SESSIONS_DIR.glob("session_*.jsonl"))
        print(f"  Sessions:  {len(sessions)} logged")
        return

    if args.task:
        from agent.agent_loop import AgentLoop
        agent = AgentLoop(config)
        result = agent.run_task(args.task, context=args.context)
        print(f"\n[RESULT] {result}")
    else:
        from agent.agent_loop import interactive_mode
        interactive_mode(config)


if __name__ == "__main__":
    main()
