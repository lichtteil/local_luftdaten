"""Characterization tests for the config flow, YAML import and options flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.local_luftdaten.const import DOMAIN

from .conftest import load_fixture_text

HOST = "192.168.0.123"

USER_INPUT: dict[str, Any] = {
    "host": HOST,
    "name": "Feinstaub",
    "resource": "http://{}/data.json",
    "verify_ssl": True,
    "monitored_conditions": ["SDS_P1", "SDS_P2"],
    "scan_interval": 180,
}


@pytest.fixture(autouse=True)
def device_responds(aioclient_mock: AiohttpClientMocker) -> None:
    """Any data.json request gets a valid answer."""
    aioclient_mock.get(
        f"http://{HOST}/data.json", text=load_fixture_text("radsens_issue45.json")
    )


async def test_user_step_shows_form_with_defaults(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    defaults = {
        str(key): key.default() for key in result["data_schema"].schema
    }
    assert defaults == {
        "host": "",
        "name": "Luftdaten Sensor",
        "resource": "http://{}/data.json",
        "verify_ssl": True,
        "monitored_conditions": ["SDS_P1", "SDS_P2", "signal"],
        "scan_interval": 180,
    }


async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    """The host becomes the entry unique_id; input is stored as data."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "scan_interval": "60"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Feinstaub"
    assert result["data"] == {**USER_INPUT, "scan_interval": 60}

    entry = result["result"]
    assert entry.unique_id == HOST
    assert entry.options == {}
    assert entry.state is ConfigEntryState.LOADED


async def test_user_step_does_not_check_connectivity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    # Characterization: current behavior, possibly unintended
    # An unreachable or misspelled host is accepted without any request.
    # Entry setup is patched out so only requests made by the flow itself
    # count (a coordinator may legitimately fetch during setup).
    aioclient_mock.clear_requests()
    with patch(
        "custom_components.local_luftdaten.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**USER_INPUT, "host": "does-not-exist.invalid"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert aioclient_mock.call_count == 0


async def test_user_step_duplicate_host_aborts(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=HOST, data=USER_INPUT).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "name": "Other"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_import_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Feinstaub"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == HOST
    assert result["result"].source == config_entries.SOURCE_IMPORT


async def test_reimport_updates_existing_entry(hass: HomeAssistant) -> None:
    """A second import for the same host updates data instead of duplicating."""
    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=USER_INPUT
    )
    await hass.async_block_till_done()

    changed = {**USER_INPUT, "name": "Renamed", "monitored_conditions": ["signal"]}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=changed
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.data == changed
    # Characterization: current behavior, possibly unintended
    # The title is not updated with the new name.
    assert entry.title == "Feinstaub"
    # The entry was reloaded: entities now follow the new data.
    assert entry.state is ConfigEntryState.LOADED
    unique_ids = {
        e.unique_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    assert "Renamed-signal" in unique_ids


async def test_yaml_platform_import(hass: HomeAssistant) -> None:
    """The legacy sensor platform config is imported with defaults filled in."""
    yaml = {
        "sensor": [
            {
                "platform": DOMAIN,
                "host": HOST,
                "scan_interval": 60,
                "monitored_conditions": ["SDS_P1", "temperature"],
            }
        ]
    }
    assert await async_setup_component(hass, "sensor", yaml)
    await hass.async_block_till_done()

    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.source == config_entries.SOURCE_IMPORT
    assert entry.unique_id == HOST
    assert entry.title == "Luftdaten Sensor"
    assert entry.data == {
        "host": HOST,
        "monitored_conditions": ["SDS_P1", "temperature"],
        "name": "Luftdaten Sensor",
        "resource": "http://{}/data.json",
        "verify_ssl": True,
        "scan_interval": 60,
    }
    assert entry.state is ConfigEntryState.LOADED


async def test_yaml_platform_reimport_on_restart_does_not_duplicate(
    hass: HomeAssistant,
) -> None:
    """An existing imported entry is updated from YAML, not duplicated."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        source=config_entries.SOURCE_IMPORT,
        title="Luftdaten Sensor",
        data={**USER_INPUT, "name": "Luftdaten Sensor"},
    ).add_to_hass(hass)

    yaml = {
        "sensor": [
            {"platform": DOMAIN, "host": HOST, "name": "Garten", "monitored_conditions": ["SDS_P2"]}
        ]
    }
    assert await async_setup_component(hass, "sensor", yaml)
    await hass.async_block_till_done()

    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.data["name"] == "Garten"
    assert entry.data["monitored_conditions"] == ["SDS_P2"]
    assert entry.data["scan_interval"] == 180


async def _setup(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=overrides.pop("unique_id", HOST),
        title="Feinstaub",
        data={**USER_INPUT, **overrides},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_options_flow_prefills_and_saves_and_reloads(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == USER_INPUT

    new_options = {**USER_INPUT, "monitored_conditions": ["SDS_P1", "signal"], "scan_interval": 300}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], new_options
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == new_options
    # data is left untouched; options override it at setup time.
    assert entry.data == USER_INPUT
    assert entry.state is ConfigEntryState.LOADED

    # Reload happened: the new signal entity exists.
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "Feinstaub-signal") is not None

    # A second options flow is prefilled from options, not data.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == new_options


async def test_options_flow_rename_orphans_old_entities(hass: HomeAssistant) -> None:
    """Changing the name changes every unique_id ('{name}-{key}')."""
    entry = await _setup(hass)
    registry = er.async_get(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {**USER_INPUT, "name": "Balkon"}
    )
    await hass.async_block_till_done()

    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    # Characterization: current behavior, possibly unintended
    # Old entities stay registered next to the new ones (history is split).
    assert unique_ids == {
        "Feinstaub-SDS_P1",
        "Feinstaub-SDS_P2",
        "Balkon-SDS_P1",
        "Balkon-SDS_P2",
    }
    assert hass.states.get("sensor.feinstaub_pm10").state == "unavailable"


async def test_options_flow_host_change_keeps_entry_unique_id(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {**USER_INPUT, "host": "10.0.0.99"}
    )
    await hass.async_block_till_done()

    # Characterization: current behavior, possibly unintended
    # The entry still claims the old host, so the old host cannot be added
    # again, while the new host can be added a second time.
    assert entry.unique_id == HOST
    assert entry.options["host"] == "10.0.0.99"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "name": "Old host"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "host": "10.0.0.99", "name": "New host"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert len(hass.states.async_entity_ids("sensor")) == 2

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    # Update listener is removed on unload: changing options does not reload.
    hass.config_entries.async_update_entry(entry, options={**USER_INPUT, "name": "X"})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
