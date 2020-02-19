"""Config flow for Salus IT600 smart home gateway integration."""
import logging
from typing import Any, Dict, Optional

from pyit600 import (
    IT600Gateway,
    IT600AuthenticationError,
    IT600ConnectionError,
    IT600CommandError,
)
import voluptuous as vol

from homeassistant.config_entries import CONN_CLASS_LOCAL_POLL, ConfigFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import ConfigType
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_SERIAL_NUMBER,
    CONF_EUID,
    DOMAIN,
)  # pylint: disable=unused-import

_LOGGER = logging.getLogger(__name__)


class IT600FlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a Salus IT600 smart home gateway config flow."""

    VERSION = 1
    CONNECTION_CLASS = CONN_CLASS_LOCAL_POLL

    async def async_step_user(
        self, user_input: Optional[ConfigType] = None
    ) -> Dict[str, Any]:
        """Handle a flow initiated by the user."""
        if user_input is None:
            return self._show_setup_form()

        try:
            mac = await self._get_gateways_mac_address(
                user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_EUID]
            )
        except IT600ConnectionError:
            return self._show_setup_form({"base": "connection_error"})
        except IT600AuthenticationError:
            return self._show_setup_form({"base": "authentication_error"})
        except IT600CommandError:
            return self._show_setup_form({"base": "command_error"})

        # Check if already configured
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=mac,
            data={
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_EUID: user_input[CONF_EUID],
                CONF_SERIAL_NUMBER: mac,
            },
        )

    async def async_step_zeroconf(
        self, user_input: Optional[ConfigType] = None
    ) -> Dict[str, Any]:
        """Handle zeroconf discovery."""
        if user_input is None:
            return self.async_abort(reason="connection_error")

        if not user_input.get("name") or not user_input["name"].startswith("Gateway_"):
            return self.async_abort(reason="not_gateway")

        mac = user_input["name"].lstrip("Gateway_")

        # Check if already configured
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()

        # pylint: disable=no-member # https://github.com/PyCQA/pylint/issues/3167
        self.context.update(
            {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_SERIAL_NUMBER: mac,
                "title_placeholders": {"serial_number": mac},
            }
        )

        # Prepare configuration flow
        return self._show_confirm_dialog()

    # pylint: disable=no-member # https://github.com/PyCQA/pylint/issues/3167
    async def async_step_zeroconf_confirm(
        self, user_input: ConfigType = None
    ) -> Dict[str, Any]:
        """Handle a flow initiated by zeroconf."""
        if user_input is None:
            return self._show_confirm_dialog()

        try:
            mac = await self._get_gateways_mac_address(
                self.context.get(CONF_HOST),
                self.context.get(CONF_PORT),
                self.context.get(CONF_EUID),
            )
        except IT600ConnectionError:
            return self.async_abort(reason="connection_error")
        except IT600AuthenticationError:
            return self.async_abort(reason="authentication_error")
        except IT600CommandError:
            return self.async_abort(reason="command_error")

        # Check if already configured
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=self.context.get(CONF_EUID),
            data={
                CONF_HOST: self.context.get(CONF_HOST),
                CONF_PORT: self.context.get(CONF_PORT),
                CONF_EUID: self.context.get(CONF_EUID),
                CONF_SERIAL_NUMBER: self.context.get(CONF_SERIAL_NUMBER),
            },
        )

    def _show_setup_form(self, errors: Optional[Dict] = None) -> Dict[str, Any]:
        """Show the setup form to the user."""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=80): int,
                    vol.Required(CONF_EUID): str,
                }
            ),
            errors=errors or {},
        )

    def _show_confirm_dialog(self) -> Dict[str, Any]:
        """Show the confirm dialog to the user."""
        # pylint: disable=no-member # https://github.com/PyCQA/pylint/issues/3167
        serial_number = self.context.get(CONF_SERIAL_NUMBER)
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"serial_number": serial_number},
        )

    async def _get_gateways_mac_address(self, host: str, port: int, euid: str) -> str:
        """Get device information from an Elgato Key Light device."""
        session = async_get_clientsession(self.hass)
        gateway = IT600Gateway(euid=euid, host=host, port=port, session=session,)
        return await gateway.connect()
