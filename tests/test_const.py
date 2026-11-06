"""Consistency checks for constants, README and translations."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.local_luftdaten.const import SENSOR_DESCRIPTIONS

REPO_ROOT = Path(__file__).parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "local_luftdaten"


def _readme_sensor_keys() -> set[str]:
    """Return the keys listed under the README's supported-sensor list."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("At the moment following sensor data can be read:", 1)[1]
    section = section.split("Sensor type `signal`", 1)[0]
    return set(re.findall(r"^- (\S+)\s*$", section, flags=re.MULTILINE))


@pytest.mark.parametrize("key", list(SENSOR_DESCRIPTIONS))
def test_dict_key_matches_description_key(key: str) -> None:
    """Each SENSOR_DESCRIPTIONS entry is keyed by its own description.key."""
    assert SENSOR_DESCRIPTIONS[key].key == key


@pytest.mark.parametrize("key", list(SENSOR_DESCRIPTIONS))
def test_key_listed_in_readme(key: str) -> None:
    """Every supported key is documented in the README sensor list."""
    assert key in _readme_sensor_keys()


def test_readme_lists_no_unknown_keys() -> None:
    """The README does not advertise keys the integration cannot read."""
    assert _readme_sensor_keys() - set(SENSOR_DESCRIPTIONS) == set()


def test_strings_and_en_translation_identical() -> None:
    """strings.json and translations/en.json have identical content."""
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text("utf-8"))
    en = json.loads((INTEGRATION_DIR / "translations" / "en.json").read_text("utf-8"))
    assert strings == en
