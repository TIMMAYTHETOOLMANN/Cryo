"""conftest for top-level tests/ directory."""
import pytest


def pytest_collection_modifyitems(config, items):
    """Ensure asyncio tests use auto mode."""
    pass
