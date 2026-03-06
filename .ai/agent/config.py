"""
JARVIS NEXUS — Agent Configuration
Centralized configuration for the autonomous agent system.
All settings can be overridden via environment variables or .env file.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ════════════════════════════════════════════════════════════════════
#  PATH RESOLUTION
# ════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # CryoSUPER/
AGENT_DIR = Path(__file__).resolve().parent                    # .ai/agent/
MCP_DIR = AGENT_DIR.parent / "mcp"                             # .ai/mcp/
SESSIONS_DIR = AGENT_DIR / "sessions"
LOGS_DIR = AGENT_DIR / "logs"


@dataclass
class AgentConfig:
    """
    Complete configuration for the autonomous agent loop.
    Designed for local LLaMA inference servers (Ollama, llama.cpp, LM Studio, vLLM).
    """

    # ── LLM Endpoint ──────────────────────────────────────────────
    # Default: Ollama running locally. Works with any OpenAI-compatible API.
    #   Ollama:     http://localhost:11434/v1
    #   llama.cpp:  http://localhost:8080/v1
    #   LM Studio:  http://localhost:1234/v1
    #   vLLM:       http://localhost:8000/v1
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY", "not-needed")
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "llama3")
    )

    # ── Context Window ────────────────────────────────────────────
    # Set this to match your model's actual context length.
    # Llama 3 8B = 8192, Llama 3 70B = 8192, Llama 3.1 = 131072
    # CodeLlama = 16384, Mistral = 32768, Qwen2.5-Coder = 131072
    max_context_tokens: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_CONTEXT", "8192"))
    )
    max_output_tokens: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_OUTPUT", "4096"))
    )

    # ── Agent Behavior ────────────────────────────────────────────
    # Maximum tool calls per task (no artificial ceiling — set high)
    max_tool_calls: int = field(
        default_factory=lambda: int(os.getenv("AGENT_MAX_TOOL_CALLS", "500"))
    )
    # Temperature for generation (lower = more deterministic for code)
    temperature: float = field(
        default_factory=lambda: float(os.getenv("AGENT_TEMPERATURE", "0.1"))
    )

    # ── Self-Heal Settings ────────────────────────────────────────
    # After N consecutive identical errors, switch strategy
    identical_error_threshold: int = field(
        default_factory=lambda: int(os.getenv("AGENT_ERROR_THRESHOLD", "5"))
    )
    # Max total retries before escalating to user
    max_total_retries: int = field(
        default_factory=lambda: int(os.getenv("AGENT_MAX_RETRIES", "25"))
    )

    # ── Workspace ─────────────────────────────────────────────────
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    # ── Safety ────────────────────────────────────────────────────
    # Directories the agent is NOT allowed to modify
    protected_paths: list = field(default_factory=lambda: [
        ".git",
        ".env",
        "node_modules",
        "__pycache__",
    ])
    # File extensions the agent can edit
    editable_extensions: list = field(default_factory=lambda: [
        ".py", ".js", ".ts", ".jsx", ".tsx", ".sol",
        ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
        ".md", ".txt", ".rst", ".html", ".css", ".scss",
        ".sh", ".bash", ".bat", ".ps1",
        ".dockerfile", ".env.template",
    ])
    # Commands that require confirmation before execution
    dangerous_commands: list = field(default_factory=lambda: [
        "rm -rf /", "del /s /q C:\\", "format",
        "DROP DATABASE", "DROP TABLE",
    ])

    # ── Logging ───────────────────────────────────────────────────
    log_level: str = field(
        default_factory=lambda: os.getenv("AGENT_LOG_LEVEL", "INFO")
    )
    persist_sessions: bool = True
    session_log_format: str = "jsonl"  # "jsonl" or "markdown"

    # ── MCP Server ────────────────────────────────────────────────
    mcp_server_host: str = field(
        default_factory=lambda: os.getenv("MCP_HOST", "localhost")
    )
    mcp_server_port: int = field(
        default_factory=lambda: int(os.getenv("MCP_PORT", "3100"))
    )

    def __post_init__(self):
        """Ensure directories exist."""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, dotenv_path: Optional[Path] = None) -> "AgentConfig":
        """Load config, optionally reading a .env file first."""
        if dotenv_path and dotenv_path.exists():
            from dotenv import load_dotenv
            load_dotenv(dotenv_path)
        elif (PROJECT_ROOT / ".env").exists():
            from dotenv import load_dotenv
            load_dotenv(PROJECT_ROOT / ".env")
        return cls()


# ════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS (OpenAI function-calling schema)
# ════════════════════════════════════════════════════════════════════

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's contents. Specify line range for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or project-relative file path"},
                    "start_line": {"type": "integer", "description": "Start line (1-based). Default: 1", "default": 1},
                    "end_line": {"type": "integer", "description": "End line (1-based). Default: -1 (entire file)", "default": -1},
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to create/overwrite"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a specific string in a file with new content. Include enough context (3-5 lines) to make the match unique.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit"},
                    "old_string": {"type": "string", "description": "Exact text to find and replace"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file or empty directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to file or empty directory to delete"},
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and subdirectories in a directory. Dirs end with /.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path. Default: project root", "default": "."},
                    "recursive": {"type": "boolean", "description": "List recursively (max depth 3)", "default": False},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "Execute a shell command in bash and return stdout+stderr. Supports chaining with && or ;. Long-running commands auto-timeout at 120s.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "working_dir": {"type": "string", "description": "Working directory. Default: project root", "default": "."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds. Default: 120", "default": 120},
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run test suite. Supports pytest, forge test, or custom commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "test_path": {"type": "string", "description": "Specific test file/dir. Default: auto-detect", "default": ""},
                    "framework": {"type": "string", "enum": ["pytest", "forge", "custom"], "description": "Test framework", "default": "pytest"},
                    "pattern": {"type": "string", "description": "Test name pattern filter", "default": ""},
                    "verbose": {"type": "boolean", "description": "Verbose output", "default": True},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": "Search for text/regex in project files. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text or regex pattern to search for"},
                    "include_pattern": {"type": "string", "description": "Glob pattern to filter files (e.g., '*.py')", "default": ""},
                    "is_regex": {"type": "boolean", "description": "Treat query as regex", "default": False},
                    "max_results": {"type": "integer", "description": "Max results. Default: 50", "default": 50},
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_operation",
            "description": "Execute git operations: status, diff, log, add, commit, branch, checkout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["status", "diff", "log", "add", "commit", "branch", "checkout", "stash"], "description": "Git operation"},
                    "args": {"type": "string", "description": "Additional arguments", "default": ""},
                },
                "required": ["operation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_errors",
            "description": "Check a Python file for syntax/import errors by attempting to compile it. For Solidity, runs forge build.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File to check for errors"},
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": "Signal that the current task is complete. Include a summary of what was done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Summary of what was accomplished"},
                },
                "required": ["summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sequential_think",
            "description": "Add a thought step to the sequential reasoning chain. Use to plan, analyze, and track multi-step reasoning. Types: analysis, planning, hypothesis, execution, validation, precheck, risk_assessment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "The thought or reasoning step"},
                    "thought_type": {"type": "string", "enum": ["analysis", "planning", "hypothesis", "execution", "validation", "revision", "precheck", "risk_assessment"], "default": "analysis"},
                    "confidence": {"type": "number", "description": "Confidence 0.0-1.0", "default": 0.8},
                },
                "required": ["thought"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_thought",
            "description": "Mark a thought step as completed or failed with its result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string", "description": "The step ID to complete"},
                    "result": {"type": "string", "description": "Result or outcome"},
                    "success": {"type": "boolean", "default": True},
                },
                "required": ["step_id", "result"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "precheck_action",
            "description": "Forward-thinking pre-validation. Analyze a planned action BEFORE executing it. Returns risk level and whether approval is needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intended_action": {"type": "string", "description": "What you plan to do"},
                    "tool_name": {"type": "string", "description": "The tool you plan to call"},
                    "arguments": {"type": "object", "description": "The arguments you plan to pass"},
                },
                "required": ["intended_action", "tool_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "branch_thought",
            "description": "Create a new branch in the thinking chain to explore an alternative solution path. Use when you want to test multiple hypotheses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch_name": {"type": "string", "description": "Name for this branch (e.g., 'approach_a', 'fallback_strategy')"},
                    "description": {"type": "string", "description": "What this branch explores"},
                    "from_step": {"type": "string", "description": "Step ID to branch from (optional, defaults to last step)"},
                },
                "required": ["branch_name", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "revise_thought",
            "description": "Revise a previous thought with updated understanding. Use when you gain new information that changes earlier reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "original_step_id": {"type": "string", "description": "The step ID to revise"},
                    "revised_thought": {"type": "string", "description": "The updated reasoning"},
                    "reason": {"type": "string", "description": "Why you're revising (e.g., 'new error found', 'test results show...')"},
                    "confidence": {"type": "number", "description": "New confidence 0.0-1.0", "default": 0.8},
                },
                "required": ["original_step_id", "revised_thought"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_thinking_state",
            "description": "Retrieve the current state of your thinking chain (all steps, branches, status). Use to review progress and dependencies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch_id": {"type": "string", "description": "Optional: get state for specific branch (default: current branch)"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_branch",
            "description": "Merge the winning branch back into main. Use after exploring alternatives with branch_thought to consolidate the best path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "winning_branch": {"type": "string", "description": "The branch ID that had the best approach"},
                    "reason": {"type": "string", "description": "Why this branch won"},
                },
                "required": ["winning_branch"]
            }
        }
    },
]
