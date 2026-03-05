@echo off
REM ═══════════════════════════════════════════════════════════════
REM  JARVIS NEXUS — Live Activity Monitor Launcher
REM  Double-click this or run from terminal to watch agent activity
REM ═══════════════════════════════════════════════════════════════
title JARVIS NEXUS — Live Monitor
cd /d "%~dp0"
python .ai\agent\live_monitor.py %*
pause
