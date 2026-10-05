"""Check real-world data.json samples against the supported sensor keys.

Sources:
- sds011_bme280.json: issue #1 (SDS011 + BME280)
- radsens_issue45.json: issue #45 (SDS011 + BME280 + ClimateGuard RadSens)
- dnms_issue52.json: issue #52 (DNMS noise sensor)
- sps30_issue80.json: issue #80 (Sensirion SPS30)
No SEN5X sample was found in the issue tracker.
"""

from __future__ import annotations

import pytest

from custom_components.local_luftdaten.const import SENSOR_DESCRIPTIONS
from custom_components.local_luftdaten.sensor import LuftdatenSensor

from .conftest import FIXTURE_FILES, load_fixture_json

# Firmware housekeeping values present in every data.json; not sensor readings.
FIRMWARE_METADATA_KEYS = {"samples", "min_micro", "max_micro", "interval"}

# Value types seen in real devices that the integration does not expose yet.
KNOWN_UNSUPPORTED_KEYS = {
    # Remove once PR #81 (RadSens, closes #45/#51) is merged.
    "RadSens_radiation_static",
    # Remove once PR #82 (DNMS, closes #52) is merged.
    "DNMS_noise_LAeq",
    "DNMS_noise_LA_min",
    "DNMS_noise_LA_max",
    # SPS30 number concentrations and typical particle size, requested in #80.
    "SPS30_N05",
    "SPS30_N1",
    "SPS30_N25",
    "SPS30_N4",
    "SPS30_N10",
    "SPS30_TS",
}


def _value_type_cases() -> list[tuple[str, str]]:
    return [
        (filename, item["value_type"])
        for filename in FIXTURE_FILES
        for item in load_fixture_json(filename)["sensordatavalues"]
    ]


def test_expected_fixtures_present() -> None:
    assert FIXTURE_FILES == [
        "dnms_issue52.json",
        "radsens_issue45.json",
        "sds011_bme280.json",
        "sps30_issue80.json",
    ]


@pytest.mark.parametrize("filename", FIXTURE_FILES)
def test_fixture_shape(filename: str) -> None:
    """Each sample is an object with a list of value_type/value string pairs."""
    data = load_fixture_json(filename)
    assert isinstance(data["sensordatavalues"], list)
    for item in data["sensordatavalues"]:
        assert set(item) == {"value_type", "value"}
        assert isinstance(item["value"], str)


@pytest.mark.parametrize(("filename", "value_type"), _value_type_cases())
def test_value_type_is_known(filename: str, value_type: str) -> None:
    """Every value_type is either supported or explicitly listed as not."""
    assert (
        value_type in SENSOR_DESCRIPTIONS
        or value_type in FIRMWARE_METADATA_KEYS
        or value_type in KNOWN_UNSUPPORTED_KEYS
    ), f"{value_type} from {filename} is neither supported nor listed as unsupported"


def test_unsupported_lists_do_not_overlap_supported() -> None:
    """Fails once a listed key becomes supported, so the list gets cleaned up."""
    assert (KNOWN_UNSUPPORTED_KEYS | FIRMWARE_METADATA_KEYS) & set(
        SENSOR_DESCRIPTIONS
    ) == set()


class _StubClient:
    """Client stub that already holds parsed data."""

    def __init__(self, data):
        self.data = data
        self.is_data_fresh = True

    async def async_update(self):
        return None


@pytest.mark.parametrize(
    ("filename", "value_type"),
    [case for case in _value_type_cases() if case[1] in SENSOR_DESCRIPTIONS],
)
async def test_supported_value_is_read_as_raw_string(
    filename: str, value_type: str
) -> None:
    """A supported value_type ends up in native_value unchanged (as a string)."""
    data = load_fixture_json(filename)
    expected = next(
        item["value"]
        for item in data["sensordatavalues"]
        if item["value_type"] == value_type
    )
    sensor = LuftdatenSensor(
        _StubClient(data), "Test", "192.168.0.1", SENSOR_DESCRIPTIONS[value_type]
    )

    await sensor.async_update()

    # Characterization: current behavior, possibly unintended
    # native_value is the raw string from data.json, not a float.
    assert sensor.native_value == expected
    assert isinstance(sensor.native_value, str)
