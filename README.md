# Y108 Smart Camera for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-blue.svg)](https://www.home-assistant.io/)

Unofficial Home Assistant custom integration for the **Hangzhou Ku Shi Technology Co., Ltd. Y108** smart camera (**FCC ID: `2BDY8-Y108`**), commonly sold under **BoyKeep** / **Ease Life** branding and managed through the **Ease Life** mobile app.

> **Disclaimer:** This is an unofficial project built on reverse-engineered Ease Life / VRS cloud protocols (inspired by [`asmsaifs/ease_life`](https://github.com/asmsaifs/ease_life)). It requires an Ease Life account and an online camera; video is relayed through the vendor cloud because these cameras do not expose a native local RTSP/ONVIF stream.

---

## Why This Integration?

Standard Home Assistant cloud-camera integrations rely exclusively on `CameraEntityFeature.STREAM`, which pushes HTTP-FLV through Home Assistant's `stream` component and converts it into **HLS segments**—introducing **2–6+ seconds of buffering delay** (or falling back to static cloud thumbnails on dashboard cards).

**Y108 Smart Camera** solves this:

- **Continuous, Low-Latency Live Video (Default):** Uses a shared per-camera FFmpeg MJPEG broadcaster (`handle_async_mjpeg_stream`) with `-fflags nobuffer+genpts+discardcorrupt -flags low_delay`, delivering smooth, continuous real-time frames directly to Home Assistant dashboards with **no HLS segment buffering**.
- **Shared Decoder & Live Snapshots:** Multiple dashboard/browser viewers share a single FFmpeg decoder and a single upstream WebSocket session. When idle, the decoder shuts down automatically after 8 seconds. `async_camera_image` serves the latest live decoded frame when active and falls back to the cloud thumbnail when idle.
- **Native PTZ Button Entities:** Exposes 5 ready-to-use `button` entities directly on the camera device (**PTZ Left**, **PTZ Right**, **PTZ Up**, **PTZ Down**, **PTZ Home**), in addition to the `y108_camera.ptz` service for automations.
- **Two-Way Audio (`speak`):** Includes `y108_camera.speak` to play Home Assistant TTS announcements through the camera speaker.
- **Optional HLS / go2rtc / Frigate Mode:** You can switch to `HA Stream (HLS / go2rtc)` in the integration options, and the local HTTP-FLV proxy URL (`http://127.0.0.1:8766/live/<device_id>.flv`) is always available in the camera entity's `flv_proxy_url` attribute.

---

## Requirements

- Home Assistant with the built-in `ffmpeg` integration enabled (included in all standard HA installations).
- An **Ease Life** account (email + password) with your **Y108 (`2BDY8-Y108`)** camera already paired in the Ease Life app.

---

## Installation

### Option 1: HACS (Custom Repository)

1. Open **HACS → Integrations → ⋮ (top right) → Custom repositories**.
2. Add your GitHub repository URL and select **Integration** as the category.
3. Click **Download** on **Y108 Smart Camera**, then restart Home Assistant.

### Option 2: Manual Installation

1. Copy the `custom_components/y108_camera` folder into your Home Assistant config directory:
   ```text
   <ha_config>/custom_components/y108_camera/
   ```
2. Restart Home Assistant.

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Y108 Smart Camera**.
3. Enter your **Ease Life** email and password.

### Options

Go to **Settings → Devices & Services → Y108 Smart Camera → Configure**:

| Option | Default | Description |
| :--- | :--- | :--- |
| `stream_mode` | `mjpeg` | `mjpeg` for continuous low-latency live video (recommended), or `stream` for HA `stream` (HLS / go2rtc). |
| `scan_interval` | `60` | Cloud device list & thumbnail poll interval in seconds. |
| `enable_audio` | `false` | Transcode the camera's G.711 audio to AAC in the FLV proxy stream. |
| `proxy_host` | `127.0.0.1` | Bind address for the local HTTP-FLV bridge (port `8766`). Set to `0.0.0.0` for external Frigate/go2rtc access. |
| `proxy_token` | _(empty)_ | Optional shared secret token for the FLV proxy URL. **Recommended if binding to `0.0.0.0`.** |

---

## Dashboard Card Example (Live Video + PTZ Controls)

Use `camera_view: live` on a Picture Glance card for continuous real-time video with one-tap PTZ buttons:

```yaml
type: picture-glance
title: Y108 Camera
camera_image: camera.y108_camera
camera_view: live
entities:
  - entity: button.y108_camera_ptz_left
  - entity: button.y108_camera_ptz_up
  - entity: button.y108_camera_ptz_home
  - entity: button.y108_camera_ptz_down
  - entity: button.y108_camera_ptz_right
```

---

## Services

### `y108_camera.ptz`

Move the camera in a direction (`left`, `right`, `up`, `down`, `home`) with configurable step count (`1–10`):

```yaml
action: y108_camera.ptz
data:
  entity_id: camera.y108_camera
  direction: left
  steps: 2
```

### `y108_camera.speak`

Render a TTS message and play it through the camera speaker:

```yaml
action: y108_camera.speak
data:
  entity_id: camera.y108_camera
  message: "Hello from Home Assistant!"
```

---

## Frigate / go2rtc Integration

If you also want to feed the stream into **Frigate** or **go2rtc**:

1. In integration options, set `proxy_host` to `0.0.0.0` and set a strong `proxy_token`.
2. Use the FLV proxy URL from your camera entity's `flv_proxy_url` attribute:
   ```yaml
   cameras:
     y108:
       enabled: true
       ffmpeg:
         inputs:
           - path: http://<YOUR_HA_IP>:8766/live/<DEVICE_ID>.flv?token=<YOUR_TOKEN>
             input_args: -avoid_negative_ts make_zero -fflags +genpts+discardcorrupt
             roles:
               - detect
               - record
   ```

---

## Author & Credits

- **Creator:** [@AddamLupus](https://github.com/AddamLupus)
- **Protocol Reference:** Based on the reverse-engineered Ease Life / VRS cloud protocol from [`asmsaifs/ease_life`](https://github.com/asmsaifs/ease_life)

