"""pytest configuration for omni_channel tests."""
import pytest


def pytest_configure(config):
    """Configure asyncio mode for async tests."""
    # Enable auto asyncio mode so all async test functions run correctly
    # without needing @pytest.mark.asyncio on each one.
    try:
        config.option.asyncio_mode = "auto"
    except AttributeError:
        pass  # pytest-asyncio not installed
