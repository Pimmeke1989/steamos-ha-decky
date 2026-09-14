"""Config flow: zeroconf discovery or manual host, then pairing with an on-screen code."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from . import artwork as artwork_mod
from .artwork import SteamGridDBAuthError, SteamGridDBClient, SteamGridDBError
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
    CONF_API_KEY,
    CONF_ARTWORK_OVERRIDES,
    CONF_HAS_BATTERY,
    CONF_MAC,
    CONF_MACHINE_ID,
    CONF_MODEL,
    CONF_TOKEN,
    CONF_WOL_BROADCAST,
    DEFAULT_PORT,
    DEFAULT_WOL_BROADCAST,
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
SGDB_API_KEY_URL = "https://www.steamgriddb.com/profile/preferences/api"
STEP_ARTWORK_SCHEMA = vol.Schema(
    {vol.Optional(CONF_API_KEY, default=""): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


def _pick_host(info: ZeroconfServiceInfo) -> str:
    """Prefer a routable IPv4 address; the plugin's server listens on IPv4."""
    for addr in info.ip_addresses:
        if addr.version == 4 and not addr.is_link_local:
            return str(addr)
    for addr in info.ip_addresses:
        if not addr.is_link_local:
            return str(addr)
    return info.host


async def _validate_api_key(hass, api_key: str, errors: dict[str, str]) -> bool:
    client = SteamGridDBClient(async_get_clientsession(hass), api_key, artwork_mod.SGDB_BASE_URL)
    try:
        await client.validate()
    except SteamGridDBAuthError:
        errors[CONF_API_KEY] = "invalid_api_key"
    except SteamGridDBError:
        errors[CONF_API_KEY] = "sgdb_unreachable"
    return not errors


class SteamOSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SteamOS."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._info: DeviceInfo | None = None
        self._client: SteamOSClient | None = None
        self._token: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SteamOSOptionsFlow:
        return SteamOSOptionsFlow()

    # ----------------------------------------------------------- discovery

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        machine_id = discovery_info.properties.get("id")
        if not machine_id:
            return self.async_abort(reason="no_id")
        self._host = _pick_host(discovery_info)
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

        # A manual add may take over from a discovery flow that is stuck on an
        # unreachable address; the discovery flow is aborted once this one finishes.
        await self.async_set_unique_id(self._info.machine_id, raise_on_progress=self.source != SOURCE_USER)
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
                self._token = token
                return await self.async_step_artwork()

        return self.async_show_form(
            step_id="pair",
            data_schema=STEP_PAIR_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._info.name, "host": self._host or ""},
        )

    # ------------------------------------------------------------- artwork

    async def async_step_artwork(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Optional SteamGridDB API key; leave empty to skip artwork entirely."""
        assert self._info is not None and self._token is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if not api_key or await _validate_api_key(self.hass, api_key, errors):
                return self.async_create_entry(
                    title=self._info.name,
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_TOKEN: self._token,
                        CONF_MACHINE_ID: self._info.machine_id,
                        CONF_MODEL: self._info.model,
                        CONF_MAC: self._info.mac,
                        CONF_HAS_BATTERY: self._info.has_battery,
                    },
                    options={CONF_API_KEY: api_key} if api_key else {},
                )
        return self.async_show_form(
            step_id="artwork",
            data_schema=STEP_ARTWORK_SCHEMA,
            errors=errors,
            description_placeholders={"sgdb_url": SGDB_API_KEY_URL},
        )


class SteamOSOptionsFlow(OptionsFlow):
    """SteamGridDB API key (empty = artwork off) and title → id overrides."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            overrides = (user_input.get(CONF_ARTWORK_OVERRIDES) or "").strip()
            broadcast = (user_input.get(CONF_WOL_BROADCAST) or DEFAULT_WOL_BROADCAST).strip()
            if not api_key or await _validate_api_key(self.hass, api_key, errors):
                return self.async_create_entry(
                    data={CONF_API_KEY: api_key, CONF_ARTWORK_OVERRIDES: overrides, CONF_WOL_BROADCAST: broadcast}
                )
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY, default=options.get(CONF_API_KEY, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_ARTWORK_OVERRIDES, default=options.get(CONF_ARTWORK_OVERRIDES, "")): TextSelector(
                    TextSelectorConfig(multiline=True)
                ),
                vol.Optional(CONF_WOL_BROADCAST, default=options.get(CONF_WOL_BROADCAST, DEFAULT_WOL_BROADCAST)): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
