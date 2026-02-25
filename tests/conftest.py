"""Shared fixtures for the slack-bridge-for-claude-code test suite."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_say() -> MagicMock:
    """Create a mock Say callable for testing Slack message handlers."""
    return MagicMock()


@pytest.fixture
def mock_ack() -> MagicMock:
    """Create a mock Ack callable for testing action handlers."""
    return MagicMock()


@pytest.fixture
def mock_respond() -> MagicMock:
    """Create a mock Respond callable for testing action handlers."""
    return MagicMock()
