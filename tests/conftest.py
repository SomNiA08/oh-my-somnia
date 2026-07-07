"""Pytest configuration and fixtures for oh-my-somnia tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Prepend src/ to sys.path so tests run against the local tree, resolved
# relative to this conftest file.
REPO_ROOT = Path(__file__).parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def tmp_home(tmp_path):
    """Fixture that provides an isolated temp home directory for tests.

    Sets OH_MY_SOMNIA_HOME to a temp directory for the duration of the test,
    ensuring tests don't touch the user's real config/genome/history.
    """
    original = os.environ.get("OH_MY_SOMNIA_HOME")
    temp_home = tmp_path / "somnia_home"
    temp_home.mkdir()
    os.environ["OH_MY_SOMNIA_HOME"] = str(temp_home)
    yield temp_home
    # Restore original
    if original is not None:
        os.environ["OH_MY_SOMNIA_HOME"] = original
    else:
        os.environ.pop("OH_MY_SOMNIA_HOME", None)
