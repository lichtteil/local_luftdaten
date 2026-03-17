"""
Support for Luftdaten sensors.

Copyright (c) 2019 Mario Villavecchia

Licensed under MIT. All rights reserved.

https://github.com/lichtteil/local_luftdaten/
"""

from __future__ import annotations

import logging
import asyncio
from typing import Any, Optional
import aiohttp
import datetime

import json

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from .const import (
    DOMAIN,
    DEFAULT_NAME,
    DEFAULT_RESOURCE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    SENSOR_DESCRIPTIONS,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_MONITORED_CONDITIONS,
    CONF_NAME,
    CONF_RESOURCE,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL
)
import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo


_LOGGER = logging.getLogger(__name__)


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_MONITORED_CONDITIONS):
        vol.All(cv.ensure_list, [vol.In(SENSOR_DESCRIPTIONS)]),
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_RESOURCE, default=DEFAULT_RESOURCE): cv.string,
    vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
    vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): cv.time_period
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Import YAML config into a config entry."""
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data={
            CONF_HOST: config[CONF_HOST],
            CONF_MONITORED_CONDITIONS: config[CONF_MONITORED_CONDITIONS],
            CONF_NAME: config[CONF_NAME],
            CONF_RESOURCE: config[CONF_RESOURCE],
            CONF_VERIFY_SSL: config[CONF_VERIFY_SSL],
            CONF_SCAN_INTERVAL: int(config[CONF_SCAN_INTERVAL].total_seconds()),
        },
    )
    return


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the sensor platform from a config entry."""
    data = {**entry.data, **entry.options}

    name = data[CONF_NAME]
    host = data[CONF_HOST]
    scan_interval = datetime.timedelta(seconds=data[CONF_SCAN_INTERVAL])
    verify_ssl = data[CONF_VERIFY_SSL]
    resource = data[CONF_RESOURCE].format(host)

    session = async_get_clientsession(hass, verify_ssl)
    rest_client = LuftdatenClient(session, resource, scan_interval)

    entities = [
        LuftdatenSensor(rest_client, name, host, SENSOR_DESCRIPTIONS[variable])
        for variable in data[CONF_MONITORED_CONDITIONS]
    ]
    async_add_entities(entities, True)


class LuftdatenSensor(SensorEntity):
    """Implementation of a LuftdatenSensor sensor."""

    _name: str
    _host: str
    _native_value: Optional[Any]
    _rest_client: "LuftdatenClient"

    def __init__(self, rest_client, name, host, description):
        """Initialize the LuftdatenSensor sensor."""
        self._rest_client = rest_client
        self._name = name
        self._host = host
        self._native_value = None

        self.entity_description = description

    @property
    def unique_id(self):
        """Return a unique ID."""
        return '{}-{}'.format(self._name, self.entity_description.key)

    @property
    def name(self):
        """Return the name of the sensor."""
        return '{} {}'.format(self._name, self.entity_description.name)

    @property
    def native_value(self):
        """Return the value reported by the sensor."""
        return self._native_value

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        if self.device_class in [SensorDeviceClass.PM1, SensorDeviceClass.PM25]:
            return 'mdi:thought-bubble-outline'
        elif self.device_class == SensorDeviceClass.PM10:
            return 'mdi:thought-bubble'

        return None

    @property
    def suggested_display_precision(self) -> int | None:
        """Suggest 1 decimal for humidity, PM, pressure, and temperature."""
        if self.device_class in {
            SensorDeviceClass.HUMIDITY,
            SensorDeviceClass.PM1,
            SensorDeviceClass.PM25,
            SensorDeviceClass.PM10,
            SensorDeviceClass.PRESSURE,
            SensorDeviceClass.TEMPERATURE,
        }:
            return 1
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for grouping entities into one device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=self._name,
            manufacturer="Luftdaten",
            model="Local Sensor",
        )

    async def async_update(self):
        """Get the latest data from REST API and update the state."""
        try:
            await self._rest_client.async_update()
        except LuftdatenError:
            return
        parsed_json = self._rest_client.data

        if parsed_json is None:
            return

        sensordata_values = parsed_json['sensordatavalues']
        for sensordata_value in sensordata_values:
            if sensordata_value['value_type'] == self.entity_description.key:
                self._native_value = sensordata_value['value']


class LuftdatenError(Exception):
    pass


class LuftdatenClient(object):
    """Class for handling the data retrieval."""

    def __init__(self, session, resource, scan_interval):
        """Initialize the data object."""
        self._session = session
        self._resource = resource
        self.lastUpdate = datetime.datetime.now()
        self.scan_interval = scan_interval
        self.data = None
        self.lock = asyncio.Lock()

    async def async_update(self):
        """Get the latest data from Luftdaten service."""

        async with self.lock:
            # Time difference since last data update
            callTimeDiff = datetime.datetime.now() - self.lastUpdate
            # Fetch sensor values only once per scan_interval
            if callTimeDiff < self.scan_interval:
                if self.data is not None:
                    return

            # Handle calltime differences: substract 5 second from current time
            self.lastUpdate = datetime.datetime.now() - datetime.timedelta(seconds=5)

            # Query local device
            responseData = None
            try:
                _LOGGER.debug("Get data from %s", str(self._resource))
                async with asyncio.timeout(30):
                    response = await self._session.get(self._resource)
                responseData = await response.text()
                _LOGGER.debug("Received data: %s", str(self.data))
            except aiohttp.ClientError as err:
                _LOGGER.warning("REST request error: {0}".format(err))
                self.data = None
                raise LuftdatenError
            except asyncio.TimeoutError:
                _LOGGER.warning("REST request timeout")
                self.data = None
                raise LuftdatenError

            # Parse REST response
            try:
                parsed_json = json.loads(responseData)
                if not isinstance(parsed_json, dict):
                    _LOGGER.warning("JSON result was not a dictionary")
                    self.data = None
                    return
                # Set parsed json as data
                self.data = parsed_json
            except ValueError:
                _LOGGER.warning("REST result could not be parsed as JSON")
                _LOGGER.debug("Erroneous JSON: %s", responseData)
                self.data = None
                return
