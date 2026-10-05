"""Characterization tests for the sensor platform running inside Home Assistant."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
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

from custom_components.local_luftdaten.const import DOMAIN

from .conftest import load_fixture_json, load_fixture_text

HOST = "192.168.0.123"
URL = f"http://{HOST}/data.json"
NAME = "Feinstaub"
# HA's default polling interval for sensor entities. The integration does not
# set SCAN_INTERVAL on the platform, so entities poll every 30 s and the
# client gates actual requests to the configured scan_interval.
HA_POLL = timedelta(seconds=30)

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
    "scan_interval": 180,
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


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, times: int = 1):
    """Advance time by `times` HA polling intervals."""
    for _ in range(times):
        freezer.tick(HA_POLL)
        async_fire_time_changed(hass)
        # Entity polling runs as a background task.
        await hass.async_block_till_done(wait_background_tasks=True)


def respond(aioclient_mock: AiohttpClientMocker, **kwargs: Any) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, **kwargs)


async def test_entities_unavailable_until_first_poll(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Entities are added without an initial update."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)

    # Characterization: current behavior, possibly unintended
    # async_add_entities(..., False): no request during setup, so every
    # entity is unavailable for the first 30 s.
    assert aioclient_mock.call_count == 0
    for entity_id in ENTITY_IDS.values():
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await poll(hass, freezer)
    assert aioclient_mock.call_count == 1
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"


async def test_setup_creates_entity_per_condition(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """One entity per monitored condition with name, unit and device class."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    entry = await setup_entry(hass)
    await poll(hass, freezer)

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
        state = hass.states.get(ENTITY_IDS[key])
        assert state.attributes[ATTR_FRIENDLY_NAME] == friendly_name
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == unit
        assert state.attributes[ATTR_DEVICE_CLASS] == device_class
        assert state.attributes["state_class"] == "measurement"

    signal = entity_registry.async_get(ENTITY_IDS["signal"])
    assert signal.entity_category == er.EntityCategory.DIAGNOSTIC


async def test_state_strings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Raw string values from data.json become the state unchanged."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)
    await poll(hass, freezer)

    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"
    assert hass.states.get(ENTITY_IDS["SDS_P2"]).state == "9.93"
    assert hass.states.get(ENTITY_IDS["BME280_temperature"]).state == "-7.99"
    assert hass.states.get(ENTITY_IDS["BME280_humidity"]).state == "78.89"
    assert hass.states.get(ENTITY_IDS["signal"]).state == "-86"


async def test_pressure_shown_in_hpa(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Pressure arrives in Pa and is converted to hPa via the suggested unit."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)
    await poll(hass, freezer)

    state = hass.states.get(ENTITY_IDS["BME280_pressure"])
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "hPa"
    # 99043.78 Pa -> hPa; the state keeps full precision, rounding to the
    # suggested 1 decimal happens only in the frontend.
    assert state.state == "990.4378"

    reg_entry = er.async_get(hass).async_get(ENTITY_IDS["BME280_pressure"])
    assert reg_entry.options["sensor.private"]["suggested_unit_of_measurement"] == "hPa"
    assert reg_entry.options["sensor"]["suggested_display_precision"] == 1


async def test_display_precision(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """1 decimal is suggested for PM/temperature/humidity/pressure, not signal."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)
    await poll(hass, freezer)

    registry = er.async_get(hass)
    for key, entity_id in ENTITY_IDS.items():
        options = registry.async_get(entity_id).options
        if key == "signal":
            assert "sensor" not in options
        else:
            assert options["sensor"]["suggested_display_precision"] == 1


async def test_pm_icons(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """PM10 and PM1/PM2.5/PM4 get distinct icons, others none."""
    respond(aioclient_mock, text=load_fixture_text("sps30_issue80.json"))
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
    await setup_entry(hass, data)
    await poll(hass, freezer)

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


async def test_unique_id_format(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """unique_id is '{name}-{key}' (based on the name, not the host)."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
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
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Two devices with the same name produce the same unique_ids."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
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
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Keys sharing a description name end up as name, name_2, ..."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    data = {**BASE_DATA, "monitored_conditions": ["BME280_temperature", "temperature"]}
    await setup_entry(hass, data)
    await poll(hass, freezer)

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
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """All entities hang off a single device identified by the host."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    entry = await setup_entry(hass)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, HOST)}
    assert device.name == NAME
    assert device.manufacturer == "Luftdaten"
    assert device.model == "Local Sensor"

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(entities) == 6
    assert {e.device_id for e in entities} == {device.id}


async def test_one_request_per_scan_interval_for_all_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Six entities share one client; HA polls every 30 s, the device is hit
    once per scan_interval (minus the 5 s correction)."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)

    await poll(hass, freezer)  # t=30: first fetch
    assert aioclient_mock.call_count == 1

    await poll(hass, freezer, 5)  # t=180: 155 s since lastUpdate (t=25)
    assert aioclient_mock.call_count == 1

    await poll(hass, freezer)  # t=210: >= 180 s since t=25
    assert aioclient_mock.call_count == 2


async def test_unreachable_holds_values_then_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Values are kept while the device is down, unavailable after 3 intervals."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)
    await poll(hass, freezer)  # t=30: success, lastSuccess = t30
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"

    respond(aioclient_mock, exc=aiohttp.ClientError("unreachable"))

    await poll(hass, freezer, 6)  # t=210: first failed fetch (3 attempts)
    assert aioclient_mock.call_count == 3
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"

    await poll(hass, freezer, 6)  # t=390: second failed fetch
    assert aioclient_mock.call_count == 6
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"

    await poll(hass, freezer, 5)  # t=540: age 510 s < 540 s
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"

    await poll(hass, freezer)  # t=570: age 540 s, third failed fetch
    assert aioclient_mock.call_count == 9
    for entity_id in ENTITY_IDS.values():
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # Recovery on the next due fetch.
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await poll(hass, freezer, 6)  # t=750
    assert aioclient_mock.call_count == 1
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"


async def test_missing_value_type_keeps_old_value(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A value_type missing from a later response keeps its previous value."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)
    await poll(hass, freezer)

    data = load_fixture_json("radsens_issue45.json")
    values = [v for v in data["sensordatavalues"] if v["value_type"] != "SDS_P1"]
    for v in values:
        if v["value_type"] == "SDS_P2":
            v["value"] = "1.11"
    respond(aioclient_mock, text=json.dumps({**data, "sensordatavalues": values}))
    await poll(hass, freezer, 6)
    assert aioclient_mock.call_count == 1

    # Characterization: current behavior, possibly unintended
    # SDS_P1 keeps the old reading and stays available indefinitely.
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"
    assert hass.states.get(ENTITY_IDS["SDS_P2"]).state == "1.11"

    await poll(hass, freezer, 30)
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"


async def test_invalid_json_makes_entities_unavailable_immediately(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """An unparseable body discards data; entities go unavailable at once."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)
    await poll(hass, freezer)

    respond(aioclient_mock, text="<html>busy</html>")
    await poll(hass, freezer, 6)

    # Characterization: current behavior, possibly unintended
    # Contrast with network errors, which keep values for 3 intervals.
    assert aioclient_mock.call_count == 1
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == STATE_UNAVAILABLE


async def test_object_without_sensordatavalues_keeps_entities_available(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """'{}' counts as a successful fetch, then the entity update raises."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    await setup_entry(hass)
    await poll(hass, freezer)

    respond(aioclient_mock, text="{}")
    await poll(hass, freezer, 6)

    # Characterization: current behavior, possibly unintended
    # KeyError('sensordatavalues') escapes async_update and is logged by HA;
    # the old value stays and, since lastSuccess was refreshed, the entity
    # stays available.
    assert "sensordatavalues" in caplog.text
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"

    await poll(hass, freezer, 30)
    assert hass.states.get(ENTITY_IDS["SDS_P1"]).state == "20.17"


async def test_options_override_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Values in entry.options take precedence over entry.data."""
    other_url = "http://10.0.0.5/data.json"
    aioclient_mock.get(other_url, text=load_fixture_text("radsens_issue45.json"))
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

    await poll(hass, freezer)
    assert aioclient_mock.mock_calls[0][1].human_repr() == other_url
    assert hass.states.get("sensor.balkon_pm2_5").state == "9.93"

    await poll(hass, freezer, 2)  # t=90: 65 s since lastUpdate (t=25)
    assert aioclient_mock.call_count == 2

    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.identifiers == {(DOMAIN, "10.0.0.5")}
    assert device.name == "Balkon"


async def test_unload_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """Unloading stops polling and leaves the entities unavailable."""
    respond(aioclient_mock, text=load_fixture_text("radsens_issue45.json"))
    entry = await setup_entry(hass)
    await poll(hass, freezer)
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    for entity_id in ENTITY_IDS.values():
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await poll(hass, freezer, 10)
    assert aioclient_mock.call_count == 1
