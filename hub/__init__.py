"""
hub — Consolidated Command Center
====================================
Unifies all CRYO system modules (detection, reconnaissance, analysis,
execution, monitoring) behind a single registry with selectable
deployment strategies.

Public API:
    ModuleRegistry    — discovers and manages every subsystem
    ReconCoordinator  — ties all recon / analysis pipelines together
    DeploymentStrategy — enum of available deployment modes
    deploy            — launch the system with a chosen strategy
    SignalBridge      — bidirectional Module 9 ↔ Omni-Channel signal translation
    m9_to_omni        — convert a single Module 9 signal to Omni-Channel format
    omni_to_m9        — convert a single Omni-Channel signal to Module 9 format
    TacticalOps       — step-by-step tactical operations framework
"""

from .registry import ModuleRegistry, ModuleInfo, ModuleStatus
from .recon_coordinator import ReconCoordinator
from .deployment import DeploymentStrategy, deploy
from .signal_adapter import SignalBridge, m9_to_omni, omni_to_m9
from .tactical_ops import TacticalOps, OpReport, StepResult

__all__ = [
    "ModuleRegistry",
    "ModuleInfo",
    "ModuleStatus",
    "ReconCoordinator",
    "DeploymentStrategy",
    "deploy",
    "SignalBridge",
    "m9_to_omni",
    "omni_to_m9",
    "TacticalOps",
    "OpReport",
    "StepResult",
]
