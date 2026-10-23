"""Characterization tests for the sensor platform running inside Home Assistant.

Unmarked tests assert observable behavior only (states, attributes, registry
entries, and request cadence within tolerances, see tests/timing.py) and must
survive a refactor to a DataUpdateCoordinator. Tests marked
implementation_detail pin today's scheduling exactly.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_ICON,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.local_luftdaten.const import DOMAIN

from .conftest import load_fixture_json, load_fixture_text
from .timing import (
    FIRST_FETCH_WITHIN,
    HA_POLL,
    LATE_TOLERANCE,
    FakeDevice,
    advance,
    advance_to,
    assert_fetch_cadence,
)

HOST = "192.168.0.123"
URL = f"http://{HOST}/data.json"
NAME = "Feinstaub"
SCAN_INTERVAL = timedelta(seconds=180)
RADSENS = load_fixture_text("radsens_issue45.json")

BASE_DATA: dict[str, Any] = {
    "host": HOST,
    "name": NAME,
    "resource": "http://{}/data.json",
    "verify_ssl": True,
    "monitored_conditions": [
        "SDS_P1",
        "SDS_P2",
        "BME280_temperature",
        "BME280_humidity",
        "BME280_pressure",
        "signal",
    ],
    "scan_interval": int(SCAN_INTERVAL.total_seconds()),
}

# Entity ids derived from "{name} {description.name}".
ENTITY_IDS = {
    "SDS_P1": "sensor.feinstaub_pm10",
    "SDS_P2": "sensor.feinstaub_pm2_5",
    "BME280_temperature": "sensor.feinstaub_temperature",
    "BME280_humidity": "sensor.feinstaub_humidity",
    "BME280_pressure": "sensor.feinstaub_pressure",
    "signal": "sensor.feinstaub_wifi_signal",
}


@pytest.fixture(autouse=True)
def no_retry_delay():
    """Skip the 3 s pause between request retries."""
    with patch("custom_components.local_luftdaten.sensor.REQUEST_RETRY_DELAY", 0):
        yield


@pytest.fixture
def device(aioclient_mock: AiohttpClientMocker) -> FakeDevice:
    """The sensor at HOST, answering with the RadSens sample by default."""
    fake = FakeDevice(aioclient_mock, URL)
    fake.respond(text=RADSENS)
    return fake


async def setup_entry(
    hass: HomeAssistant,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    unique_id: str = HOST,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        title=(data or BASE_DATA)["name"],
        data=data or BASE_DATA,
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def setup_with_data(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, **kwargs: Any
) -> MockConfigEntry:
    """Set up the entry and wait until the first fetch is reflected."""
    entry = await setup_entry(hass, **kwargs)
    await advance(hass, freezer, FIRST_FETCH_WITHIN)
    return entry


def state(hass: HomeAssistant, key: str) -> str:
    return hass.states.get(ENTITY_IDS[key]).state


async def test_values_present_shortly_after_setup(
    hass: HomeAssistant, device: FakeDevice, freezer
) -> None:
    """Within one HA poll after setup the device was read and values show."""
    start = dt_util.utcnow()
    await setup_with_data(hass, freezer)

    assert len(device.rounds()) == 1
    assert device.rounds()[0][0] - start <= FIRST_FETCH_WITHIN
    assert state(hass, "SDS_P1") == "20.17"


async def test_setup_creates_entity_per_condition(
    hass: HomeAssistant, device: FakeDevice, freezer
) -> None:
    """One entity per monitored condition with name, unit and device class."""
    entry = await setup_with_data(hass, freezer)

    expected = {
        "SDS_P1": ("Feinstaub PM10", "μg/m³", "pm10"),
        "SDS_P2": ("Feinstaub PM2.5", "μg/m³", "pm25"),
        "BME280_temperature": ("Feinstaub Temperature", "°C", "temperature"),
        "BME280_humidity": ("Feinstaub Humidity", "%", "humidity"),
        "BME280_pressure": ("Feinstaub Pressure", "hPa", "pressure"),
        "signal": ("Feinstaub WiFi signal", "dBm", "signal_strength"),
    }
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    assert {e.entity_id for e in entries} == set(ENTITY_IDS.values())

    for key, (friendly_name, unit, device_class) in expected.items():
        st = hass.states.get(ENTITY_IDS[key])
        assert st.attributes[ATTR_FRIENDLY_NAME] == friendly_name
        assert st.attributes[ATTR_UNIT_OF_MEASUREMENT] == unit
        assert st.attributes[ATTR_DEVICE_CLASS] == device_class
        assert st.attributes["state_class"] == "measurement"

    signal = entity_registry.async_get(ENTITY_IDS["signal"])
    assert signal.entity_category == er.EntityCategory.DIAGNOSTIC


async def test_state_strings(hass: HomeAssistant, device: FakeDevice, freezer) -> None:
    """Raw string values from data.json become the state unchanged."""
    await setup_with_data(hass, freezer)

    assert state(hass, "SDS_P1") == "20.17"
    assert state(hass, "SDS_P2") == "9.93"
    assert state(hass, "BME280_temperature") == "-7.99"
    assert state(hass, "BME280_humidity") == "78.89"
    assert state(hass, "signal") == "-86"


async def test_pressure_shown_in_hpa(
    hass: HomeAssistant, device: FakeDevice, freezer
) -> None:
    """Pressure arrives in Pa and is converted to hPa via the suggested unit."""
    await setup_with_data(hass, freezer)

    st = hass.states.get(ENTITY_IDS["BME280_pressure"])
    assert st.attributes[ATTR_UNIT_OF_MEASUREMENT] == "hPa"
    # 99043.78 Pa -> hPa; the state keeps full precision, rounding to the
    # suggested 1 decimal happens only in the frontend.
    assert st.state == "990.4378"

    reg_entry = er.async_get(hass).async_get(ENTITY_IDS["BME280_pressure"])
    assert reg_entry.options["sensor.private"]["suggested_unit_of_measurement"] == "hPa"
    assert reg_entry.options["sensor"]["suggested_display_precision"] == 1


async def test_display_precision(
    hass: HomeAssistant, device: FakeDevice, freezer
) -> None:
    """1 decimal is suggested for PM/temperature/humidity/pressure, not signal."""
    await setup_with_data(hass, freezer)

    registry = er.async_get(hass)
    for key, entity_id in ENTITY_IDS.items():
        options = registry.async_get(entity_id).options
        if key == "signal":
            assert "sensor" not in options
        else:
            assert options["sensor"]["suggested_display_precision"] == 1


async def test_pm_icons(hass: HomeAssistant, device: FakeDevice, freezer) -> None:
    """PM10 and PM1/PM2.5/PM4 get distinct icons, others none."""
    device.respond(text=load_fixture_text("sps30_issue80.json"))
    data = {
        **BASE_DATA,
        "monitored_conditions": [
            "SPS30_P0",
            "SPS30_P1",
            "SPS30_P2",
            "SPS30_P4",
            "signal",
        ],
    }
    await setup_with_data(hass, freezer, data=data)

    def icon(entity_id: str) -> str | None:
        return hass.states.get(entity_id).attributes.get(ATTR_ICON)

    assert icon("sensor.feinstaub_pm1") == "mdi:thought-bubble-outline"
    assert icon("sensor.feinstaub_pm10") == "mdi:thought-bubble"
    assert icon("sensor.feinstaub_pm2_5") == "mdi:thought-bubble-outline"
    # Characterization: current behavior, possibly unintended
    # PM4 uses device class PM25 (no PM4 class exists), so it shows as pm25.
    assert icon("sensor.feinstaub_pm4") == "mdi:thought-bubble-outline"
    assert hass.states.get("sensor.feinstaub_pm4").attributes[ATTR_DEVICE_CLASS] == "pm25"
    assert icon("sensor.feinstaub_wifi_signal") is None


async def test_unique_id_format(hass: HomeAssistant, device: FakeDevice) -> None:
    """unique_id is '{name}-{key}' (based on the name, not the host)."""
    entry = await setup_entry(hass)

    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert unique_ids == {f"{NAME}-{key}" for key in BASE_DATA["monitored_conditions"]}
    assert registry.async_get_entity_id("sensor", DOMAIN, "Feinstaub-SDS_P1") == (
        "sensor.feinstaub_pm10"
    )


async def test_same_name_on_two_hosts_collides(
    hass: HomeAssistant, device: FakeDevice, aioclient_mock: AiohttpClientMocker
) -> None:
    """Two devices with the same name produce the same unique_ids."""
    aioclient_mock.get("http://192.168.0.200/data.json", text=RADSENS)
    first = await setup_entry(hass)
    second = await setup_entry(
        hass, {**BASE_DATA, "host": "192.168.0.200"}, unique_id="192.168.0.200"
    )

    registry = er.async_get(hass)
    # Characterization: current behavior, possibly unintended
    # The second entry's entities are rejected as duplicate unique_ids.
    assert len(er.async_entries_for_config_entry(registry, first.entry_id)) == 6
    assert len(er.async_entries_for_config_entry(registry, second.entry_id)) == 0
    assert second.state is ConfigEntryState.LOADED


async def test_duplicate_description_names_get_suffixed_entity_ids(
    hass: HomeAssistant, device: FakeDevice, freezer
) -> None:
    """Keys sharing a description name end up as name, name_2, ..."""
    data = {**BASE_DATA, "monitored_conditions": ["BME280_temperature", "temperature"]}
    await setup_with_data(hass, freezer, data=data)

    registry = er.async_get(hass)
    # Characterization: current behavior, possibly unintended
    # Both are named "Feinstaub Temperature"; the entity_id depends on order.
    assert registry.async_get_entity_id("sensor", DOMAIN, "Feinstaub-BME280_temperature") == (
        "sensor.feinstaub_temperature"
    )
    assert registry.async_get_entity_id("sensor", DOMAIN, "Feinstaub-temperature") == (
        "sensor.feinstaub_temperature_2"
    )
    # A monitored key missing from data.json stays "unknown" but available.
    assert hass.states.get("sensor.feinstaub_temperature_2").state == STATE_UNKNOWN


async def test_all_entities_on_one_device(
    hass: HomeAssistant, device: FakeDevice
) -> None:
    """All entities hang off a single device identified by the host."""
    entry = await setup_entry(hass)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    dev = devices[0]
    assert dev.identifiers == {(DOMAIN, HOST)}
    assert dev.name == NAME
    assert dev.manufacturer == "Luftdaten"
    assert dev.model == "Local Sensor"

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(entities) == 6
    assert {e.device_id for e in entities} == {dev.id}


@pytest.mark.parametrize("scan_seconds", [60, 100, 180])
async def test_fetch_cadence_follows_scan_interval(
    hass: HomeAssistant, device: FakeDevice, freezer, scan_seconds: int
) -> None:
    """All entities share one request per fetch; at most one fetch per
    scan_interval and a new one at the latest after scan_interval."""
    scan_interval = timedelta(seconds=scan_seconds)
    start = dt_util.utcnow()
    await setup_entry(hass, {**BASE_DATA, "scan_interval": scan_seconds})

    await advance(hass, freezer, scan_interval * 10)

    rounds = device.rounds()
    assert all(len(r) == 1 for r in rounds), "one request per fetch for 6 entities"
    assert_fetch_cadence(rounds, start, dt_util.utcnow(), scan_interval)


async def test_unreachable_holds_values_then_unavailable(
    hass: HomeAssistant, device: FakeDevice, freezer
) -> None:
    """Values are kept while the device is down; unavailable once the last
    success is 3 scan_intervals old; recovery on the next fetch."""
    start = dt_util.utcnow()
    await setup_with_data(hass, freezer)
    last_success = device.rounds()[0][0]
    device.respond(exc=aiohttp.ClientError("unreachable"))

    # 1 scan_interval without success: old values, available.
    await advance_to(hass, freezer, last_success + SCAN_INTERVAL + LATE_TOLERANCE)
    assert len(device.rounds(since=last_success)) >= 2, "failed fetch was attempted"
    assert all(state(hass, key) != STATE_UNAVAILABLE for key in ENTITY_IDS)
    assert state(hass, "SDS_P1") == "20.17"

    # Just before 3 scan_intervals: still the old values.
    await advance_to(hass, freezer, last_success + SCAN_INTERVAL * 3 - timedelta(seconds=1))
    assert state(hass, "SDS_P1") == "20.17"

    # 3 scan_intervals without success: unavailable.
    await advance_to(hass, freezer, last_success + SCAN_INTERVAL * 3 + LATE_TOLERANCE)
    assert all(state(hass, key) == STATE_UNAVAILABLE for key in ENTITY_IDS)

    # 4 scan_intervals: still unavailable, still retrying at the normal pace.
    await advance_to(hass, freezer, last_success + SCAN_INTERVAL * 4 + LATE_TOLERANCE)
    assert all(state(hass, key) == STATE_UNAVAILABLE for key in ENTITY_IDS)
    assert_fetch_cadence(device.rounds(), start, dt_util.utcnow(), SCAN_INTERVAL)

    # Device back: values return within one scan_interval.
    device.respond(text=RADSENS)
    await advance(hass, freezer, SCAN_INTERVAL + LATE_TOLERANCE)
    assert state(hass, "SDS_P1") == "20.17"
    assert all(state(hass, key) != STATE_UNAVAILABLE for key in ENTITY_IDS)


async def test_missing_value_type_keeps_old_value(
    hass: HomeAssistant, device: FakeDevice, freezer
) -> None:
    """A value_type missing from a later response keeps its previous value."""
    await setup_with_data(hass, freezer)

    data = load_fixture_json("radsens_issue45.json")
    values = [v for v in data["sensordatavalues"] if v["value_type"] != "SDS_P1"]
    for v in values:
        if v["value_type"] == "SDS_P2":
            v["value"] = "1.11"
    device.respond(text=json.dumps({**data, "sensordatavalues": values}))
    await advance(hass, freezer, SCAN_INTERVAL + LATE_TOLERANCE)
    assert len(device.rounds()) >= 2

    # Characterization: current behavior, possibly unintended
    # SDS_P1 keeps the old reading and stays available indefinitely.
    assert state(hass, "SDS_P1") == "20.17"
    assert state(hass, "SDS_P2") == "1.11"

    await advance(hass, freezer, SCAN_INTERVAL * 5)
    assert state(hass, "SDS_P1") == "20.17"


@pytest.mark.parametrize("payload", ["<html>busy</html>", "[1, 2, 3]"])
async def test_invalid_json_makes_entities_unavailable(
    hass: HomeAssistant, device: FakeDevice, freezer, payload: str
) -> None:
    """An unparseable or non-object body discards data; entities go
    unavailable on the next fetch instead of after 3 scan_intervals."""
    await setup_with_data(hass, freezer)

    device.respond(text=payload)
    await advance(hass, freezer, SCAN_INTERVAL + LATE_TOLERANCE)

    # Characterization: current behavior, possibly unintended
    # Contrast with network errors, which keep values for 3 intervals.
    assert len(device.rounds()) >= 2
    assert state(hass, "SDS_P1") == STATE_UNAVAILABLE


async def test_object_without_sensordatavalues_keeps_entities_available(
    hass: HomeAssistant,
    device: FakeDevice,
    freezer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """'{}' counts as a successful fetch, then reading the values fails."""
    await setup_with_data(hass, freezer)

    device.respond(text="{}")
    await advance(hass, freezer, SCAN_INTERVAL + LATE_TOLERANCE)

    # Characterization: current behavior, possibly unintended
    # KeyError('sensordatavalues') is logged; the old value stays and, since
    # the fetch counted as success, the entity stays available.
    assert "sensordatavalues" in caplog.text
    assert state(hass, "SDS_P1") == "20.17"

    await advance(hass, freezer, SCAN_INTERVAL * 5)
    assert state(hass, "SDS_P1") == "20.17"


async def test_options_override_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Values in entry.options take precedence over entry.data."""
    other = FakeDevice(aioclient_mock, "http://10.0.0.5/data.json")
    other.respond(text=RADSENS)
    start = dt_util.utcnow()
    entry = await setup_entry(
        hass,
        options={
            **BASE_DATA,
            "host": "10.0.0.5",
            "name": "Balkon",
            "monitored_conditions": ["SDS_P2"],
            "scan_interval": 60,
        },
    )

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert [e.unique_id for e in entities] == ["Balkon-SDS_P2"]
    assert entities[0].entity_id == "sensor.balkon_pm2_5"

    await advance(hass, freezer, timedelta(seconds=60) * 5)
    assert hass.states.get("sensor.balkon_pm2_5").state == "9.93"
    # Only the options host is queried, at the options scan_interval.
    assert all(str(call[1]) == other.url for call in aioclient_mock.mock_calls)
    assert_fetch_cadence(
        other.rounds(), start, dt_util.utcnow(), timedelta(seconds=60)
    )

    [dev] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert dev.identifiers == {(DOMAIN, "10.0.0.5")}
    assert dev.name == "Balkon"


async def test_unload_entry(hass: HomeAssistant, device: FakeDevice, freezer) -> None:
    """Unloading stops fetching and leaves the entities unavailable."""
    entry = await setup_with_data(hass, freezer)
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    for entity_id in ENTITY_IDS.values():
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    requests_at_unload = len(device.request_times)
    await advance(hass, freezer, SCAN_INTERVAL * 3)
    assert len(device.request_times) == requests_at_unload


# --- Implementation details -------------------------------------------------
# Exact request counts on HA's 30 s entity-polling grid and the client's 5 s
# lastUpdate correction. A coordinator fetches on its own schedule, so these
# are expected to break.


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, times: int = 1):
    """Advance time by `times` HA entity-polling intervals."""
    await advance(hass, freezer, HA_POLL * times)


def respond(aioclient_mock: AiohttpClientMocker, **kwargs: Any) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, **kwargs)


@pytest.mark.implementation_detail
async def test_impl_entities_unavailable_until_first_entity_poll(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    Entities are added without an initial update.
    """
    respond(aioclient_mock, text=RADSENS)
    await setup_entry(hass)

    # Characterization: current behavior, possibly unintended
    # async_add_entities(..., False): no request during setup, so every
    # entity is unavailable until the first entity poll 30 s later.
    assert aioclient_mock.call_count == 0
    for entity_id in ENTITY_IDS.values():
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await poll(hass, freezer)
    assert aioclient_mock.call_count == 1
    assert state(hass, "SDS_P1") == "20.17"


@pytest.mark.implementation_detail
async def test_impl_request_counts_on_30s_poll_grid(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    HA polls every 30 s; the client fetches once lastUpdate (fetch time - 5 s)
    is a scan_interval old.
    """
    respond(aioclient_mock, text=RADSENS)
    await setup_entry(hass)

    await poll(hass, freezer)  # t=30: first fetch
    assert aioclient_mock.call_count == 1

    await poll(hass, freezer, 5)  # t=180: 155 s since lastUpdate (t=25)
    assert aioclient_mock.call_count == 1

    await poll(hass, freezer)  # t=210: >= 180 s since t=25
    assert aioclient_mock.call_count == 2


@pytest.mark.implementation_detail
async def test_impl_unreachable_request_counts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Pins current implementation; delete or rewrite when refactoring to a coordinator.

    Exact timeline: success at t=30, failed fetches (3 attempts each) at
    t=210/390/570, unavailable from the poll at t=570.
    """
    respond(aioclient_mock, text=RADSENS)
    await setup_entry(hass)
    await poll(hass, freezer)  # t=30: success, lastSuccess = t30

    respond(aioclient_mock, exc=aiohttp.ClientError("unreachable"))

    await poll(hass, freezer, 6)  # t=210
    assert aioclient_mock.call_count == 3
    await poll(hass, freezer, 6)  # t=390
    assert aioclient_mock.call_count == 6
    await poll(hass, freezer, 5)  # t=540: age 510 s < 540 s
    assert state(hass, "SDS_P1") == "20.17"
    await poll(hass, freezer)  # t=570: age 540 s
    assert aioclient_mock.call_count == 9
    assert state(hass, "SDS_P1") == STATE_UNAVAILABLE

    respond(aioclient_mock, text=RADSENS)
    await poll(hass, freezer, 6)  # t=750
    assert aioclient_mock.call_count == 1
    assert state(hass, "SDS_P1") == "20.17"
