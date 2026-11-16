"""Native PTZ button entities for Y108 Smart Camera."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


@dataclass(frozen=True)
class PtzButtonDescription:
    direction: str
    label: str
    icon: str


PTZ_BUTTONS: tuple[PtzButtonDescription, ...] = (
    PtzButtonDescription("left", "PTZ Left", "mdi:arrow-left-bold"),
    PtzButtonDescription("right", "PTZ Right", "mdi:arrow-right-bold"),
    PtzButtonDescription("up", "PTZ Up", "mdi:arrow-up-bold"),
    PtzButtonDescription("down", "PTZ Down", "mdi:arrow-down-bold"),
    PtzButtonDescription("home", "PTZ Home", "mdi:home"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[Y108PtzButton] = []
    for device in coordinator.data or []:
        for desc in PTZ_BUTTONS:
            entities.append(Y108PtzButton(coordinator, device, desc))
    async_add_entities(entities)


class Y108PtzButton(CoordinatorEntity, ButtonEntity):
    """Button entity to trigger a single PTZ step on the Y108 camera."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device: dict, desc: PtzButtonDescription) -> None:
        super().__init__(coordinator)
        self._device_id = device.get("deviceId")
        self._device = device
        self._direction = desc.direction
        self._attr_unique_id = f"{self._device_id}_ptz_{desc.direction}"
        self._attr_name = desc.label
        self._attr_icon = desc.icon

    @property
    def device_info(self) -> DeviceInfo:
        cam_name = (
            self._device.get("deviceName")
            or self._device.get("deviceTitle")
            or self._device_id
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=cam_name,
            manufacturer="Hangzhou Ku Shi Technology Co., Ltd.",
            model=self._device.get("cameraModelId") or "Y108 (2BDY8-Y108)",
            serial_number=self._device.get("serialNumber"),
        )

    async def async_press(self) -> None:
        await self.coordinator.async_ptz(self._device_id, self._direction, steps=1)
