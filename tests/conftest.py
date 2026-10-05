"""Shared fixtures for the Local Luftdaten tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# custom_components/ has no __init__.py, so it is a namespace package. PHACC
# ships a regular custom_components package in its testing_config, and a
# regular package wins over namespace portions during import. Importing ours
# first caches it in sys.modules so Home Assistant's loader finds the
# integration.
import custom_components.local_luftdaten  # noqa: F401

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Every data.json sample in tests/fixtures/, used for parametrized tests.
FIXTURE_FILES = sorted(p.name for p in FIXTURES_DIR.glob("*.json"))


def load_fixture_text(filename: str) -> str:
    """Return the raw text of a fixture file."""
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def load_fixture_json(filename: str) -> dict[str, Any]:
    """Return a fixture file parsed as JSON."""
    return json.loads(load_fixture_text(filename))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load custom_components/local_luftdaten."""
    yield


@pytest.fixture
def fixture_text():
    """Return a loader for raw fixture text."""
    return load_fixture_text


@pytest.fixture
def fixture_json():
    """Return a loader for parsed fixture JSON."""
    return load_fixture_json
