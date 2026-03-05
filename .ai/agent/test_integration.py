"""Quick integration test for JARVIS NEXUS v2.0 subsystems."""
import sys
sys.path.insert(0, ".ai/agent")
from autonomous_controller import classify_action, ApprovalLevel, ForwardThinkingTroubleshooter
from sequential_thinking import SequentialThinkingEngine

# Test approval gate
tests = [
    ("forge create --rpc-url mainnet", "approve"),
    ("git push origin main", "approve"),
    ("npm publish", "approve"),
    ("pip install requests", "inform"),
    ("python -m pytest", "none"),
    ("echo hello", "none"),
    ("docker push myimg:latest", "approve"),
]
print("=== APPROVAL GATES ===")
for cmd, expected in tests:
    level, reason = classify_action(cmd)
    status = "PASS" if level.value == expected else "FAIL"
    print(f"  [{status}] {level.value:7s} | {cmd}")
assert all(classify_action(c)[0].value == e for c, e in tests), "Gate test failed"

# Test sequential thinking chain
print("\n=== SEQUENTIAL THINKING ===")
engine = SequentialThinkingEngine()
r1 = engine.add_thought("Analyze the codebase structure", "analysis", 0.9)
assert r1["step_id"] == "step_1"
assert r1["status"] == "active"
print(f"  step_1 created: {r1['status']}")

r2 = engine.add_thought("Plan: fix imports, add tests", "planning", 0.85)
assert r2["step_id"] == "step_2"
print(f"  step_2 created: {r2['status']}")

c1 = engine.complete_thought("step_1", "Found 3 modules with circular imports", True)
assert c1["status"] == "completed"
print(f"  step_1 completed: conf={c1['branch_confidence']:.0%}")

br = engine.branch_thought("alt_path", "Try rewriting utils from scratch")
assert "branch_id" in br
print(f"  branch created: {br['branch_id']}")

chain = engine.get_chain_of_thought()
assert "step_1" not in chain or "analysis" in chain.lower()
print(f"  chain:\n{chain}")

# Test troubleshooter
print("\n=== FORWARD-THINKING TROUBLESHOOTER ===")
ts = ForwardThinkingTroubleshooter()

# Deployment should be blocked
res = ts.analyze_before_execute("run_terminal", {"command": "forge create --rpc-url mainnet"})
assert res.approval_required == True
assert res.proceed == False
print(f"  Mainnet deploy: BLOCKED (correct)")

# Normal command should proceed
res2 = ts.analyze_before_execute("run_terminal", {"command": "python -m pytest"})
assert res2.proceed == True
assert res2.approval_required == False
print(f"  pytest: PROCEED (correct)")

# Edit with short match should warn
res3 = ts.analyze_before_execute("edit_file", {"path": "main.py", "old_string": "x=1"})
assert len(res3.warnings) > 0
print(f"  Short edit: WARNING (correct) - {res3.warnings[0][:60]}")

# Auto-continuation prompt
prompt = ts.get_auto_continuation_prompt({"success": True, "output": "ok"}, "[5 calls]")
assert "CONTINUE" in prompt
print(f"  Success continuation: OK")

prompt = ts.get_auto_continuation_prompt({"success": False, "output": "ModuleNotFoundError: web3"}, "[6 calls]")
assert "pip install" in prompt.lower()
print(f"  Error continuation: OK (suggests pip install)")

print("\n=== ALL INTEGRATION TESTS PASSED ===")
