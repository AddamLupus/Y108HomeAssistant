"""Camera platform for Y108 Smart Camera (Hangzhou Ku Shi / Ease Life)."""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ptz_client
from .const import (
    CONF_STREAM_MODE,
    DEFAULT_STREAM_MODE,
    DOMAIN,
    STREAM_MODE_STREAM,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        Y108Camera(coordinator, entry, device)
        for device in (coordinator.data or [])
    ]
    async_add_entities(entities, update_before_add=True)


class Y108Camera(CoordinatorEntity, Camera):
    """Home Assistant camera entity with continuous low-latency MJPEG stream."""

    def __init__(self, coordinator, entry: ConfigEntry, device: dict) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._entry = entry
        self._device_id = device.get("deviceId")
        self._attr_unique_id = self._device_id
        self._attr_name = (
            device.get("deviceName") or device.get("deviceTitle") or self._device_id
        )
        self._attr_model = device.get("cameraModelId") or "Y108"
        self._attr_brand = "Hangzhou Ku Shi Technology"
        self._last_device = device
        self._attr_is_on = device.get("onlineStatus") == "available"

        stream_mode = entry.options.get(CONF_STREAM_MODE, DEFAULT_STREAM_MODE)
        self._stream_mode = stream_mode
        if stream_mode == STREAM_MODE_STREAM:
            self._attr_supported_features = CameraEntityFeature.STREAM
        else:
            self._attr_supported_features = CameraEntityFeature(0)

    @property
    def device_info(self) -> DeviceInfo:
        d = self._current_device() or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._attr_name,
            manufacturer="Hangzhou Ku Shi Technology Co., Ltd.",
            model=d.get("cameraModelId") or "Y108 (2BDY8-Y108)",
            serial_number=d.get("serialNumber"),
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def _current_device(self) -> dict:
        for d in self.coordinator.data or []:
            if d.get("deviceId") == self._device_id:
                return d
        return self._last_device

    @property
    def extra_state_attributes(self) -> dict:
        d = self._current_device() or {}
        caps = ptz_client.ptz_capabilities(d.get("comment") or "")
        proxy = getattr(self.coordinator, "flv_proxy", None)
        return {
            "serial_number": d.get("serialNumber"),
            "online_status": d.get("onlineStatus"),
            "region": d.get("nowRegion"),
            "cloud_did": d.get("cloudDid"),
            "ptz_supported": caps["pan"] or caps["tilt"],
            "stream_mode": self._stream_mode,
            "flv_proxy_url": proxy.url_for(self._device_id) if proxy else None,
        }

    async def stream_source(self) -> str | None:
        proxy = getattr(self.coordinator, "flv_proxy", None)
        if proxy is None:
            return None
        return proxy.url_for(self._device_id)

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Serve continuous real-time MJPEG frames without HLS segment buffering."""
        hub = getattr(self.coordinator, "mjpeg_hub", None)
        if hub is None:
            return None
        return await hub.async_stream_mjpeg(self._device_id, request)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        hub = getattr(self.coordinator, "mjpeg_hub", None)
        if hub is not None:
            live_frame = hub.latest_frame(self._device_id)
            if live_frame is not None:
                return live_frame
        try:
            return await self.coordinator.async_thumbnail(
                self._device_id, width=width or 640
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("thumbnail fetch failed for %s: %s", self._device_id, err)
            return None
