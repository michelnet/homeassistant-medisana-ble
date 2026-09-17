"""Shared Home Assistant fixtures."""

import pytest


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Enable loading the custom component under test."""
    yield
