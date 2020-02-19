"""Support for Salus IT600 smart thermostat."""
import logging
from random import randrange

from pyit600 import (
    IT600Gateway,
    IT600AuthenticationError,
    IT600Error,
)

from .const import CONF_EUID

import voluptuous as vol

from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateDevice
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    TEMP_CELSIUS,
)
from homeassistant.exceptions import PlatformNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({vol.Required(CONF_EUID): cv.string})

MAP_IH_TO_HVAC_MODE = {
    "heat": HVAC_MODE_HEAT,
    "off": HVAC_MODE_OFF,
}

MAP_HVAC_MODE_TO_IH = {v: k for k, v in MAP_IH_TO_HVAC_MODE.items()}

MAP_STATE_ICONS = {
    HVAC_MODE_HEAT: "mdi:white-balance-sunny",
    HVAC_MODE_OFF: None,
}

IH_HVAC_MODES = [
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
]


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Create Salus IT600 climate devices."""
    euid = config[CONF_EUID]

    gateway = IT600Gateway(euid, hass.loop)
    try:
        await gateway.poll_status()
    except IT600AuthenticationError:
        _LOGGER.error("Invalid EUID")
        return
    except IT600Error:
        _LOGGER.error("Error connecting to server")
        raise PlatformNotReady

    climate_devices = gateway.get_climate_devices()
    if climate_devices:
        async_add_entities(
            [
                IT600Climate(climate_device_id, device, gateway)
                for climate_device_id, device in climate_devices.items()
            ],
            True,
        )
    else:
        _LOGGER.error("Error getting device list API")
        await gateway.close()


class IT600Climate(ClimateDevice):
    """Represents a Salus IT600 smart thermostat."""

    def __init__(self, device_id, device, gateway):
        """Initialize the thermostat."""
        self._gateway = gateway
        self._device_id = device_id
        self._device = device
        self._device_name = device.get("name")
        self._connected = None
        self._setpoint_step = 0.5
        self._current_temp = device.get("current_temperature")
        self._min_temp = device.get("min_temp")
        self._max_temp = device.get("max_temp")
        self._target_temp = device.get("target_temperature")
        self._rssi = None
        self._power = False
        self._hvac_mode = device.get("hvac_mode")

    async def async_added_to_hass(self):
        """Subscribe to event updates."""
        _LOGGER.debug("Added climate device with state: %s", repr(self._device))
        await self._gateway.add_climate_update_callback(self.async_update_callback)
        try:
            await self._gateway.connect()
        except IT600Error as ex:
            _LOGGER.error("Exception connecting to gateway: %s", ex)

    @property
    def name(self):
        """Return the name of the AC device."""
        return self._device_name

    @property
    def temperature_unit(self):
        """Intesishome API uses celsius on the backend."""
        return TEMP_CELSIUS

    @property
    def device_state_attributes(self):
        """Return the device specific state attributes."""
        attrs = {}
        return attrs

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._device_id

    @property
    def target_temperature_step(self) -> float:
        """Return whether setpoint should be whole or half degree precision."""
        return self._setpoint_step

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)

        if hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)

        if temperature:
            _LOGGER.debug("Setting %s to %s degrees", self._device_id, temperature)
            await self._gateway.set_climate_device_temperature(
                self._device_id, temperature
            )
            self._target_temp = temperature

        # Write updated temperature to HA state to avoid flapping (API confirmation is slow)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        """Set operation mode."""
        _LOGGER.debug("Setting %s to %s mode", self._device_type, hvac_mode)
        if hvac_mode == HVAC_MODE_OFF:
            self._power = False
            await self._gateway.set_power_off(self._device_id)
            # Write changes to HA, API can be slow to push changes
            self.async_write_ha_state()
            return

        # First check device is turned on
        if not self._gateway.is_on(self._device_id):
            self._power = True
            await self._gateway.set_power_on(self._device_id)

        # Set the mode
        await self._gateway.set_mode(self._device_id, MAP_HVAC_MODE_TO_IH[hvac_mode])

        # Send the temperature again in case changing modes has changed it
        if self._target_temp:
            await self._gateway.set_temperature(self._device_id, self._target_temp)

        # Updates can take longer than 2 seconds, so update locally
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()

    async def async_update(self):
        """Copy values from gateway dictionary to climate device."""
        # Update values from gateway's device dictionary
        self._connected = self._gateway.is_connected
        self._setpoint_step = 0.5
        self._current_temp = self._gateway.get_temperature(self._device_id)
        self._min_temp = self._gateway.get_min_setpoint(self._device_id)
        self._max_temp = self._gateway.get_max_setpoint(self._device_id)
        self._target_temp = self._gateway.get_setpoint(self._device_id)
        self._rssi = self._gateway.get_rssi(self._device_id)
        self._power = self._gateway.get_power(self._device_id)

        # Operation mode
        mode = self._gateway.get_mode(self._device_id)
        self._hvac_mode = MAP_IH_TO_HVAC_MODE.get(mode)

    async def async_will_remove_from_hass(self):
        """Shutdown the gateway when the device is being removed."""
        await self._gateway.close()

    @property
    def icon(self):
        """Return the icon for the current state."""
        icon = None
        if self._power:
            icon = MAP_STATE_ICONS.get(self._hvac_mode)
        return icon

    async def async_update_callback(self, device_id=None):
        """Let HA know there has been an update from the gateway."""
        # Track changes in connection state
        if not self._gateway.is_connected and self._connected:
            # Connection has dropped
            self._connected = False
            reconnect_minutes = 1 + randrange(10)
            _LOGGER.error(
                "Connection to API was lost. Reconnecting in %i minutes",
                reconnect_minutes,
            )
            # Schedule reconnection
            async_call_later(self.hass, reconnect_minutes * 60, self._gateway.connect())

        if self._gateway.is_connected and not self._connected:
            # Connection has been restored
            self._connected = True
            _LOGGER.debug("Connection to API was restored")

        if not device_id or self._device_id == device_id:
            # Update all devices if no device_id was specified
            _LOGGER.debug(
                "API sent a status update for device %s", device_id,
            )
            self.async_schedule_update_ha_state(True)

    @property
    def min_temp(self):
        """Return the minimum temperature for the current mode of operation."""
        return self._min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature for the current mode of operation."""
        return self._max_temp

    @property
    def should_poll(self):
        """Poll for updates if pyIntesisHome doesn't have a socket open."""
        return False

    @property
    def hvac_modes(self):
        """List of available operation modes."""
        return IH_HVAC_MODES

    @property
    def available(self) -> bool:
        """If the device hasn't been able to connect, mark as unavailable."""
        return self._connected or self._connected is None

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temp

    @property
    def hvac_mode(self):
        """Return the current mode of operation if unit is on."""
        if self._power:
            return self._hvac_mode
        return HVAC_MODE_OFF

    @property
    def target_temperature(self):
        """Return the current setpoint temperature if unit is on."""
        return self._target_temp

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return device.get("supported_features")
