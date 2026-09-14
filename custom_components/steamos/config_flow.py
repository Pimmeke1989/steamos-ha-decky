"""Config flow: zeroconf discovery or manual host, then pairing with an on-screen code."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import (
    CannotConnect,
    DeviceInfo,
    NotInGamingMode,
    PairingError,
    SteamOSClient,
    UnsupportedApi,
)
from .const import (
    CLIENT_NAME,
    CONF_MACHINE_ID,
    CONF_MODEL,
    CONF_TOKEN,
    DEFAULT_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(int, vol.Range(min=1, max=65535)),
    }
)
STEP_PAIR_SCHEMA = vol.Schema({vol.Required("code"): str})


class SteamOSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SteamOS."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._info: DeviceInfo | None = None
        self._client: SteamOSClient | None = None

    # ----------------------------------------------------------- discovery

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        machine_id = discovery_info.properties.get("id")
        if not machine_id:
            return self.async_abort(reason="no_id")
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        await self.async_set_unique_id(machine_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})

        name = discovery_info.properties.get("name") or discovery_info.hostname.rstrip(".")
        model = discovery_info.properties.get("model") or "SteamOS device"
        self.context["title_placeholders"] = {"name": name, "model": model}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._connect_and_start_pairing(errors)
            if result is not None:
                return result
        placeholders = self.context.get("title_placeholders", {})
        return self.async_show_form(
            step_id="zeroconf_confirm",
            errors=errors,
            description_placeholders={**placeholders, "host": self._host or ""},
        )

    # -------------------------------------------------------------- manual

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = user_input[CONF_PORT]
            result = await self._connect_and_start_pairing(errors)
            if result is not None:
                return result
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    # ------------------------------------------------------------- pairing

    async def _connect_and_start_pairing(self, errors: dict[str, str]) -> ConfigFlowResult | None:
        """Fetch /api/info, set unique id, ask the plugin to show a code.

        Returns None (with ``errors`` filled) when the host cannot be reached, so
        the calling step can re-show its own form.
        """
        assert self._host is not None
        self._client = SteamOSClient(async_get_clientsession(self.hass), self._host, self._port)
        try:
            self._info = await self._client.get_info()
        except CannotConnect:
            errors["base"] = "cannot_connect"
            return None
        except UnsupportedApi:
            return self.async_abort(reason="unsupported_api")

        await self.async_set_unique_id(self._info.machine_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})
        self.context["title_placeholders"] = {"name": self._info.name, "model": self._info.model}
        return await self.async_step_pair()

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._client is not None and self._info is not None
        errors: dict[str, str] = {}

        if user_input is None:
            # First visit: ask the plugin to put a code on screen.
            try:
                await self._client.pair_start()
            except NotInGamingMode:
                errors["base"] = "not_in_gaming_mode"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except PairingError:
                errors["base"] = "pair_failed"
        else:
            code = user_input["code"].replace(" ", "").strip()
            try:
                token = await self._client.pair(code, CLIENT_NAME)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except PairingError as err:
                errors["base"] = {
                    "wrong_code": "wrong_code",
                    "no_pairing_session": "pair_expired",
                    "too_many_attempts": "pair_expired",
                }.get(err.code, "pair_failed")
                if errors["base"] == "pair_expired":
                    # Start a fresh session so the user sees a new code.
                    try:
                        await self._client.pair_start()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                return self.async_create_entry(
                    title=self._info.name,
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_TOKEN: token,
                        CONF_MACHINE_ID: self._info.machine_id,
                        CONF_MODEL: self._info.model,
                    },
                )

        return self.async_show_form(
            step_id="pair",
            data_schema=STEP_PAIR_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._info.name, "host": self._host or ""},
        )
