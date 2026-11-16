"""Low-latency continuous MJPEG broadcaster backed by the local FlvProxy."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from aiohttp import web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
READ_CHUNK_SIZE = 32768
IDLE_GRACE_SECONDS = 8.0
MAX_FRAME_BUFFER = 2 * 1024 * 1024
BOUNDARY = "frame"


class _DeviceMjpegBroadcaster:
    """Single FFmpeg decoder per camera feeding all connected MJPEG viewers."""

    def __init__(self, hass: HomeAssistant, flv_url: str, device_id: str) -> None:
        self._hass = hass
        self._flv_url = flv_url
        self._device_id = device_id
        self._subscribers: set[asyncio.Queue[bytes | None]] = set()
        self._task: asyncio.Task | None = None
        self._idle_timer: asyncio.TimerHandle | None = None
        self._latest_frame: bytes | None = None
        self._latest_ts: float = 0.0

    def latest_frame(self, max_age: float = 5.0) -> bytes | None:
        """Return the most recent decoded JPEG frame if still fresh."""
        if self._latest_frame is None:
            return None
        if time.monotonic() - self._latest_ts > max_age:
            return None
        return self._latest_frame

    def subscribe(self) -> asyncio.Queue[bytes | None]:
        """Register a viewer queue and ensure the FFmpeg worker is running."""
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)
        self._subscribers.add(queue)
        if self._latest_frame is not None:
            queue.put_nowait(self._latest_frame)
        if self._task is None or self._task.done():
            self._task = self._hass.async_create_background_task(
                self._run(), name=f"y108_mjpeg_{self._device_id}"
            )
        return queue

    def unsubscribe(self, queue: asyncio.Queue[bytes | None]) -> None:
        """Remove a viewer queue and schedule worker shutdown when idle."""
        self._subscribers.discard(queue)
        if not self._subscribers and self._idle_timer is None:
            self._idle_timer = self._hass.loop.call_later(
                IDLE_GRACE_SECONDS, self._stop_if_idle
            )

    def _stop_if_idle(self) -> None:
        self._idle_timer = None
        if not self._subscribers and self._task is not None and not self._task.done():
            self._task.cancel()

    async def stop(self) -> None:
        """Immediately stop the FFmpeg worker and notify all viewers."""
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        for q in list(self._subscribers):
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._subscribers.clear()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _publish(self, frame: bytes) -> None:
        self._latest_frame = frame
        self._latest_ts = time.monotonic()
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass

    def _ffmpeg_binary(self) -> str:
        try:
            return get_ffmpeg_manager(self._hass).binary
        except Exception:  # noqa: BLE001
            return "ffmpeg"

    async def _run(self) -> None:
        ffmpeg_bin = self._ffmpeg_binary()
        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "nobuffer+genpts+discardcorrupt",
            "-flags",
            "low_delay",
            "-probesize",
            "32768",
            "-analyzeduration",
            "0",
            "-f",
            "flv",
            "-i",
            self._flv_url,
            "-an",
            "-c:v",
            "mjpeg",
            "-q:v",
            "5",
            "-fps_mode",
            "passthrough",
            "-f",
            "image2pipe",
            "pipe:1",
        ]

        while self._subscribers:
            proc: asyncio.subprocess.Process | None = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                buf = bytearray()
                assert proc.stdout is not None
                while self._subscribers:
                    chunk = await proc.stdout.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if len(buf) > MAX_FRAME_BUFFER:
                        # Drop stale bytes up to the last SOI marker to keep latency minimal.
                        last_soi = buf.rfind(JPEG_SOI)
                        buf = bytearray(buf[last_soi:]) if last_soi != -1 else bytearray()
                    while True:
                        start = buf.find(JPEG_SOI)
                        if start == -1:
                            buf.clear()
                            break
                        if start > 0:
                            del buf[:start]
                        end = buf.find(JPEG_EOI, 2)
                        if end == -1:
                            break
                        frame = bytes(buf[: end + 2])
                        del buf[: end + 2]
                        self._publish(frame)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "y108_camera: MJPEG decoder error for %s: %s",
                    self._device_id,
                    err,
                )
            finally:
                if proc is not None:
                    if proc.returncode is None:
                        proc.kill()
                    try:
                        await proc.wait()
                    except Exception:  # noqa: BLE001
                        pass
            if self._subscribers:
                await asyncio.sleep(1.0)


class MjpegStreamHub:
    """Manages per-camera low-latency MJPEG broadcasters."""

    def __init__(self, hass: HomeAssistant, url_provider: Callable[[str], str]) -> None:
        self._hass = hass
        self._url_provider = url_provider
        self._broadcasters: dict[str, _DeviceMjpegBroadcaster] = {}

    def _broadcaster(self, device_id: str) -> _DeviceMjpegBroadcaster:
        b = self._broadcasters.get(device_id)
        if b is None:
            b = _DeviceMjpegBroadcaster(
                self._hass, self._url_provider(device_id), device_id
            )
            self._broadcasters[device_id] = b
        return b

    def latest_frame(self, device_id: str, max_age: float = 5.0) -> bytes | None:
        b = self._broadcasters.get(device_id)
        return b.latest_frame(max_age=max_age) if b is not None else None

    async def async_stream_mjpeg(
        self, device_id: str, request: web.Request
    ) -> web.StreamResponse:
        """Stream continuous multipart/x-mixed-replace JPEG frames to an HTTP client."""
        broadcaster = self._broadcaster(device_id)
        queue = broadcaster.subscribe()
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": f"multipart/x-mixed-replace;boundary={BOUNDARY}",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )
        await response.prepare(request)
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                header = (
                    f"--{BOUNDARY}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                ).encode("ascii")
                await response.write(header + frame + b"\r\n")
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            broadcaster.unsubscribe(queue)
        return response

    async def stop(self) -> None:
        for b in list(self._broadcasters.values()):
            await b.stop()
        self._broadcasters.clear()
