"""Y108 Smart Camera integration for Home Assistant."""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import timedelta

import requests
import voluptuous as vol
from homeassistant.components import tts
from homeassistant.components.tts.const import DATA_TTS_MANAGER
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import ptz_client, talk, v4_client, vrs_live
from .const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_ENABLE_AUDIO,
    CONF_PASSWORD,
    CONF_PROXY_HOST,
    CONF_PROXY_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_PROXY_HOST,
    DEFAULT_PROXY_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .flv_proxy import FlvProxy
from .mjpeg_hub import MjpegStreamHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["camera", "button"]

SERVICE_PTZ = "ptz"
SERVICE_SPEAK = "speak"

PTZ_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("direction"): vol.In(["left", "right", "up", "down", "home"]),
        vol.Optional("steps", default=1): vol.All(int, vol.Range(min=1, max=10)),
    }
)
SPEAK_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("message"): str,
        vol.Optional("engine"): str,
        vol.Optional("language"): str,
    }
)


class Y108Coordinator(DataUpdateCoordinator):
    """Coordinates auth, device state, FLV proxy, and MJPEG hub."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self._session = requests.Session()
        self._token: str | None = None
        self._refresh: str | None = None
        self.flv_proxy: FlvProxy | None = None
        self.mjpeg_hub: MjpegStreamHub | None = None
        interval = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    def _blocking_login(self) -> None:
        data = v4_client.login(
            self.entry.data[CONF_EMAIL],
            self.entry.data[CONF_PASSWORD],
            device_id=self.entry.data.get(CONF_DEVICE_ID) or None,
            session=self._session,
        )
        self._token = data["token"]
        self._refresh = data.get("refreshToken")

    def _blocking_refresh(self) -> None:
        if not self._refresh:
            self._blocking_login()
            return
        data = v4_client.refresh_token(self._refresh, session=self._session)
        self._token = data.get("token", self._token)
        self._refresh = data.get("refreshToken", self._refresh)

    def _with_retry(self, call):
        try:
            return call(self._session)
        except v4_client.EaseLifeAuthError:
            self._blocking_refresh()
            return call(self._session)
        except requests.exceptions.RequestException:
            self._session.close()
            self._session = requests.Session()
            return call(self._session)

    def _device_list(self):
        if not self._token:
            self._blocking_login()
        return self._with_retry(
            lambda session: v4_client.device_list(self._token, session=session)
        )

    async def _async_update_data(self):
        try:
            return await self.hass.async_add_executor_job(self._device_list)
        except Exception as err:
            raise UpdateFailed(str(err)) from err

    async def async_live_params(self, device_id: str) -> dict:
        def work():
            if not self._token:
                self._blocking_login()
            device = next(
                (d for d in (self.data or []) if d.get("deviceId") == device_id), None
            )
            if device is None:
                device = next(
                    (d for d in self._device_list() if d.get("deviceId") == device_id),
                    None,
                )
            region = (device or {}).get("region") or ""
            return {
                "vrs_host": region.split("/")[0],
                "token": self._token,
                "product_key": v4_client.PRODUCT_KEY,
                "relay_server": "",
            }

        return await self.hass.async_add_executor_job(work)

    async def async_thumbnail(self, device_id: str, width: int = 640) -> bytes | None:
        def work():
            devices = self.data or []
            device = next((d for d in devices if d.get("deviceId") == device_id), None)
            if device is None:
                device = next(
                    (d for d in self._device_list() if d.get("deviceId") == device_id),
                    None,
                )
            if device is None or not device.get("thumbnailUrlList"):
                return None
            return self._with_retry(
                lambda session: v4_client.thumbnail_bytes(
                    device, width=width, session=session
                )
            )

        return await self.hass.async_add_executor_job(work)

    async def async_ptz(self, device_id: str, direction: str, steps: int = 1) -> None:
        value = ptz_client.PTZ_DIRECTIONS[direction]
        params = await self.async_live_params(device_id)
        for i in range(steps):
            if i > 0:
                await asyncio.sleep(0.4)
            await ptz_client.async_ptz_move(
                params["token"],
                device_id,
                params["product_key"],
                value,
            )

    async def async_speak(
        self,
        device_id: str,
        message: str,
        engine: str | None = None,
        language: str | None = None,
    ) -> None:
        if not engine:
            manager = self.hass.data.get(DATA_TTS_MANAGER)
            engine = manager.default_engine if manager else None
            if not engine:
                raise HomeAssistantError("No TTS engine configured in Home Assistant")
        _, audio_bytes = await tts.async_get_media_source_audio(
            self.hass, message, engine, language=language
        )

        def decode_pcm() -> bytes:
            try:
                import av
            except ImportError as err:
                raise HomeAssistantError(
                    "PyAV not installed; cannot decode TTS audio"
                ) from err
            resampler = av.AudioResampler(format="s16", layout="mono", rate=8000)
            out = bytearray()
            with av.open(io.BytesIO(audio_bytes)) as container:
                for frame in container.decode(audio=0):
                    for resampled in resampler.resample(frame):
                        out.extend(bytes(resampled.planes[0]))
                for flushed in resampler.resample(None):
                    out.extend(bytes(flushed.planes[0]))
            return bytes(out)

        pcm = await self.hass.async_add_executor_job(decode_pcm)

        live_sender = None
        started_hidden = False
        if self.flv_proxy is not None:
            live_sender = self.flv_proxy.talk_sender(device_id)
            if live_sender is None:
                live_sender, started_hidden = await self.flv_proxy.hidden_talk_sender(
                    device_id
                )

        if live_sender is not None:
            try:
                await talk.async_speak_via(live_sender, pcm)
                return
            except vrs_live.TalkSendError as err:
                _LOGGER.warning(
                    "y108_camera: live speak ride failed for %s (%s); "
                    "retrying on standalone session",
                    device_id,
                    err,
                )
            finally:
                if started_hidden and self.flv_proxy is not None:
                    await self.flv_proxy.stop_device(device_id)

        if self.flv_proxy is not None:
            await self.flv_proxy.stop_device(device_id)

        params = await self.async_live_params(device_id)
        if not params["vrs_host"]:
            raise HomeAssistantError(f"Camera {device_id} has no live VRS host")
        try:
            await talk.async_speak(
                params["vrs_host"],
                params["token"],
                device_id,
                pcm,
                product_key=params["product_key"],
            )
        except Exception as err:
            if "cannot write to closing transport" in str(err).lower():
                await asyncio.sleep(1.5)
                params = await self.async_live_params(device_id)
                await talk.async_speak(
                    params["vrs_host"],
                    params["token"],
                    device_id,
                    pcm,
                    product_key=params["product_key"],
                )
                return
            raise


def _resolve_device(hass: HomeAssistant, entity_id: str):
    reg = er.async_get(hass)
    ent = reg.async_get(entity_id)
    if ent is None or ent.platform != DOMAIN:
        raise HomeAssistantError(f"{entity_id} is not a Y108 camera entity")
    for coordinator in hass.data.get(DOMAIN, {}).values():
        if not isinstance(coordinator, Y108Coordinator):
            continue
        for d in coordinator.data or []:
            if d.get("deviceId") == ent.unique_id:
                return coordinator, d["deviceId"]
    raise HomeAssistantError(f"Device for {entity_id} not loaded")


async def _handle_ptz(call: ServiceCall) -> None:
    coordinator, device_id = _resolve_device(call.hass, call.data["entity_id"])
    await coordinator.async_ptz(
        device_id, call.data["direction"], call.data.get("steps", 1)
    )


async def _handle_speak(call: ServiceCall) -> None:
    coordinator, device_id = _resolve_device(call.hass, call.data["entity_id"])
    await coordinator.async_speak(
        device_id,
        call.data["message"],
        engine=call.data.get("engine"),
        language=call.data.get("language"),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = Y108Coordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    proxy = FlvProxy(
        hass,
        coordinator.async_live_params,
        host=entry.options.get(CONF_PROXY_HOST, DEFAULT_PROXY_HOST),
        port=DEFAULT_PROXY_PORT,
        keep_audio=entry.options.get(CONF_ENABLE_AUDIO, False),
        proxy_token=entry.options.get(CONF_PROXY_TOKEN, ""),
    )
    await proxy.start()
    coordinator.flv_proxy = proxy
    coordinator.mjpeg_hub = MjpegStreamHub(hass, proxy.url_for)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_PTZ):
        hass.services.async_register(DOMAIN, SERVICE_PTZ, _handle_ptz, schema=PTZ_SCHEMA)
    if not hass.services.has_service(DOMAIN, SERVICE_SPEAK):
        hass.services.async_register(
            DOMAIN, SERVICE_SPEAK, _handle_speak, schema=SPEAK_SCHEMA
        )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: Y108Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        if coordinator.mjpeg_hub is not None:
            await coordinator.mjpeg_hub.stop()
        if coordinator.flv_proxy is not None:
            await coordinator.flv_proxy.stop()
        if not any(
            isinstance(v, Y108Coordinator) for v in hass.data.get(DOMAIN, {}).values()
        ):
            hass.services.async_remove(DOMAIN, SERVICE_PTZ)
            hass.services.async_remove(DOMAIN, SERVICE_SPEAK)
    return ok
