"""Config flow for Y108 Smart Camera."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import v4_client
from .const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_ENABLE_AUDIO,
    CONF_PASSWORD,
    CONF_PROXY_HOST,
    CONF_PROXY_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_STREAM_MODE,
    DEFAULT_PROXY_HOST,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STREAM_MODE,
    DOMAIN,
    STREAM_MODE_MJPEG,
    STREAM_MODE_STREAM,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_DEVICE_ID, default=""): str,
    }
)


class Y108ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Y108 Smart Camera."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    v4_client.login,
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                    user_input.get(CONF_DEVICE_ID) or None,
                )
            except v4_client.EaseLifeAuthError as err:
                _LOGGER.warning("Y108 / Ease Life login failed: %s", err)
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Y108 / Ease Life login error")
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL], data=user_input
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Y108OptionsFlow()


class Y108OptionsFlow(config_entries.OptionsFlow):
    """Handle options for Y108 Smart Camera."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_STREAM_MODE,
                        default=self.config_entry.options.get(
                            CONF_STREAM_MODE, DEFAULT_STREAM_MODE
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=STREAM_MODE_MJPEG,
                                    label="MJPEG (Continuous, low latency)",
                                ),
                                selector.SelectOptionDict(
                                    value=STREAM_MODE_STREAM,
                                    label="HA Stream (HLS / go2rtc)",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): int,
                    vol.Optional(
                        CONF_ENABLE_AUDIO,
                        default=self.config_entry.options.get(
                            CONF_ENABLE_AUDIO, False
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_PROXY_HOST,
                        default=self.config_entry.options.get(
                            CONF_PROXY_HOST, DEFAULT_PROXY_HOST
                        ),
                    ): str,
                    vol.Optional(
                        CONF_PROXY_TOKEN,
                        default=self.config_entry.options.get(
                            CONF_PROXY_TOKEN, ""
                        ),
                    ): str,
                }
            ),
        )
