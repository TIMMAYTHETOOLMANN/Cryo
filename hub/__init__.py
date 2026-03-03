"""
hub — Consolidated Command Center
====================================
Unifies all CRYO system modules (detection, reconnaissance, analysis,
execution, monitoring) behind a single registry with selectable
deployment strategies.

Public API:
    ModuleRegistry  — discovers and manages every subsystem
    ReconCoordinator — ties all recon / analysis pipelines together
    DeploymentStrategy — enum of available deployment modes
    deploy           — launch the system with a chosen strategy
"""

from .registry import ModuleRegistry, ModuleInfo, ModuleStatus
from .recon_coordinator import ReconCoordinator
from .deployment import DeploymentStrategy, deploy

__all__ = [
    "ModuleRegistry",
    "ModuleInfo",
    "ModuleStatus",
    "ReconCoordinator",
    "DeploymentStrategy",
    "deploy",
]
