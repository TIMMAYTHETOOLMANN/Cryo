# JARVIS NEXUS v2.0 — Autonomous Agent Architecture

JARVIS NEXUS v2.0 represents a major evolution in autonomous agent capability, shifting from a reactive tool-executor to a proactive, deep-thinking autonomous entity.

## Key Subsystems

### 1. Sequential Thinking Engine (`.ai/agent/sequential_thinking.py`)
The native reasoning core of the agent. It replaces the external `@modelcontextprotocol/server-sequential-thinking` package with a native module integrated directly into the NEXUS pipeline.
- **Branching Thought Chains:** Explore multiple solution paths simultaneously.
- **Confidence Scoring:** Real-time evaluation of thought quality (0.0 - 1.0).
- **Dependency Tracking:** Ensures steps are executed in a logical order.
- **Backtracking & Revision:** Supports updating prior conclusions with new information.
- **Persistent Logging:** Full thought chains are recorded for auditing and debugging.

### 2. Autonomous Execution Controller (`.ai/agent/autonomous_controller.py`)
The "brain" that governs continuous operation and safety.
- **Approval Gate Classifier:** A regex-based system (16+ critical patterns) that blocks dangerous or deployment-critical actions until a human approves.
- **Forward-Thinking Troubleshooter:** Pre-validates every action BEFORE execution, predicting potential failures and suggesting preemptive checks.
- **Auto-Continuation Prompt Generator:** Injects context-specific fix suggestions and continuation directives so the model NEVER stops working or goes idle.

### 3. Integration & MCP Server (`.ai/mcp/mcp_server.py`)
Exposes 18 high-performance tools (up from 11) to any MCP client.
- **Tools:** `read_file`, `write_file`, `edit_file`, `delete_file`, `list_directory`, `run_terminal`, `run_tests`, `search_text`, `git_status`, `check_errors`, `get_live_activity`, `sequential_think`, `complete_thought`, `branch_thought`, `revise_thought`, `get_thinking_state`, `precheck_action`, `request_approval`.

## How It Works Now

### Unlimited Execution
The agent loop (`.ai/agent/agent_loop.py`) no longer has an artificial `max_tool_calls` ceiling. It runs continuously until the task is complete, an approval gate is hit, or it's manually stopped.

### Proactive Troubleshooting
On failure, the system doesn't just return an error; the Troubleshooter injects targeted fix suggestions (e.g., "install missing package", "check path", "verify syntax") so the model immediately recovers without human intervention.

### Deployment Gating
The agent is fully autonomous for all development tasks (building, testing, refactoring). Only mainnet deployments, production branch pushes, or other high-risk actions trigger a pause for human approval.

### Native Sequential Thinking
The agent uses `sequential_think` to build a complex reasoning chain before taking action, allowing for deeper analysis of complex problems than simple ReAct loops.

## Tooling Schema
All tool definitions are centralized in `.ai/agent/config.py` and implemented in `.ai/agent/tools.py`.
