"""Buttons: sleep / shut down / restart via the Steam client, turn on via Wake-on-LAN."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from wakeonlan import send_magic_packet

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import NotInGamingMode, SteamOSError
from .const import CONF_MAC, CONF_WOL_BROADCAST, DEFAULT_WOL_BROADCAST, DOMAIN
from .coordinator import SteamOSConfigEntry, SteamOSCoordinator
from .entity import SteamOSEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SteamOSButtonDescription(ButtonEntityDescription):
    press: Callable[[SteamOSCoordinator], Awaitable[None]]
    needs_gaming: bool = True


async def _power(coordinator: SteamOSCoordinator, action: str) -> None:
    try:
        await coordinator.client.send_power(action)
    except NotInGamingMode as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_in_gaming_mode") from err
    except SteamOSError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="power_failed", translation_placeholders={"error": str(err)}
        ) from err


async def _wake(coordinator: SteamOSCoordinator) -> None:
    entry = coordinator.config_entry
    mac = coordinator.data.mac or entry.data.get(CONF_MAC)
    if not mac:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_mac")
    broadcast = entry.options.get(CONF_WOL_BROADCAST) or DEFAULT_WOL_BROADCAST
    _LOGGER.debug("Sending magic packet to %s via %s", mac, broadcast)
    await coordinator.hass.async_add_executor_job(lambda: send_magic_packet(mac, ip_address=broadcast))


BUTTONS: tuple[SteamOSButtonDescription, ...] = (
    SteamOSButtonDescription(
        key="sleep", translation_key="sleep", icon="mdi:power-sleep", press=lambda c: _power(c, "suspend")
    ),
    SteamOSButtonDescription(
        key="shutdown", translation_key="shutdown", icon="mdi:power", press=lambda c: _power(c, "shutdown")
    ),
    SteamOSButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_registry_enabled_default=False,
        press=lambda c: _power(c, "reboot"),
    ),
    SteamOSButtonDescription(
        key="turn_on", translation_key="turn_on", icon="mdi:power-on", press=_wake, needs_gaming=False
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SteamOSConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SteamOSButton(coordinator, description) for description in BUTTONS)


class SteamOSButton(SteamOSEntity, ButtonEntity):
    entity_description: SteamOSButtonDescription

    def __init__(self, coordinator: SteamOSCoordinator, description: SteamOSButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.needs_gaming:
            return super().available
        # Turn on: the whole point is that the machine is off; needs a MAC address though.
        return bool(self.coordinator.data.mac or self.coordinator.config_entry.data.get(CONF_MAC))

    async def async_press(self) -> None:
        await self.entity_description.press(self.coordinator)
