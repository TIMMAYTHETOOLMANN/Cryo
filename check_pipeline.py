#!/usr/bin/env python3
"""Quick status check for the running pipeline."""
import sys, os

log_file = os.path.join(os.path.dirname(__file__), "cryo.log")
if not os.path.exists(log_file):
    log_file = os.path.join(os.path.dirname(__file__), "cryo1_live.log")
if not os.path.exists(log_file):
    log_file = os.path.join(os.path.dirname(__file__), "module1_liquidation_engine.log")

try:
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
except FileNotFoundError:
    print("No log file found. Is the pipeline running?")
    sys.exit(1)

print(f"Log file: {log_file}")
print(f"Total log lines: {len(lines)}")
print()

# Extract heartbeats
heartbeats = [l.strip() for l in lines if "Cycle" in l and "found=" in l and "INFO" in l]
if heartbeats:
    print(f"Scan cycles: {len(heartbeats)}")
    print(f"  First: {heartbeats[0]}")
    print(f"  Last:  {heartbeats[-1]}")
else:
    print("No scan cycles recorded yet.")

# Extract key events
print()
events = {
    "Opportunities": [l for l in lines if "Stage 1:" in l and "opportunit" in l.lower()],
    "Profitable":    [l for l in lines if "Stage 2:" in l and "approved" in l.lower()],
    "Executions":    [l for l in lines if "ON-CHAIN" in l or "Executing" in l],
    "Profits":       [l for l in lines if "PROFIT:" in l],
    "Bootstrapped":  [l for l in lines if "Bootstrap" in l and "borrower" in l.lower()],
    "New borrowers": [l for l in lines if "+new borrower" in l.lower() or "new borrower" in l.lower()],
    "Errors":        [l for l in lines if "ERROR" in l and "Traceback" not in l],
}

for label, evts in events.items():
    if evts:
        print(f"{label}: {len(evts)}")
        for e in evts[-3:]:
            print(f"  {e.strip()}")
    else:
        print(f"{label}: 0")
    print()

# Check if process is running
import subprocess
import platform
try:
    if platform.system() == "Windows":
        result = subprocess.run(["tasklist"], capture_output=True, text=True)
        py_procs = [l for l in result.stdout.split("\n") if "python" in l.lower()]
    else:
        result = subprocess.run(["pgrep", "-a", "python"], capture_output=True, text=True)
        py_procs = [l for l in result.stdout.strip().split("\n") if l]
    print(f"Python processes running: {len(py_procs)}")
except Exception:
    print("Python processes running: unknown (could not query)")
