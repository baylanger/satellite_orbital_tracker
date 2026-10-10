"""Pytest configuration for Satellite Orbital Tracker."""
import sys
from pathlib import Path
import pytest

# Add the repository root to sys.path so custom_components can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

# Enable custom integration loading for pytest-homeassistant-custom-component
pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield
