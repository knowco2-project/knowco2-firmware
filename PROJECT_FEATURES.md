# KnowCO2 Project Feature Inventory

This document is a living reference for all major features in the KnowCO2 project.
Update it whenever behavior or interfaces change so future AI-assisted updates
preserve full functionality.

## Firmware (CircuitPython, ESP32-S3 Reverse TFT)

### Hardware Targets
- Board: Adafruit Feather ESP32-S3 Reverse TFT.
- Sensor: Sensirion SCD4x (CO₂, temperature, humidity).
- Battery fuel gauge support (voltage + percent reporting).
- Buttons: three inputs (A/B/C) used for UI navigation and Wi‑Fi mode switching.

### Startup & Reliability
- Boot splash screen with centered bitmap (`/assets/splash.bmp`).
- Hardware watchdog enabled in the main loop to reset the MCU on hard stalls.
- Memory monitoring (periodic GC + min/max/EMA tracking).
- Socket default timeout to prevent indefinite blocking.

### Sensor & Data Pipeline
- Periodic SCD4x measurements with adjustable measurement interval.
- CO₂ history buffer for plotting (rolling window).
- Instantaneous rate-of-change calculation for trend display.
- CRC failure tracking with automatic sensor recovery.
- Stale-sample watchdog to detect frozen sensor data.
- Recovery escalation: soft reset of SCD4x, then MCU reset after repeated recoveries.

### Display & UI
- Three main display modes:
  - Text summary (CO₂ + temp/humidity).
  - Big CO₂ display.
  - Live graph (bar plot with thresholds).
- Graph scaling modes: fixed, wide, and auto.
- Threshold indicators (LOW/MED/HIGH) and axis labels.
- Color-coded CO₂ state and alert banner messages.

### Controls (Buttons)
- Button A: toggle temperature unit (°C/°F).
- Button B: cycle display mode.
- Button C: short press toggles main/AP info screens; long press toggles Wi‑Fi mode.

### Networking
- STA mode (client) with SSID/password configuration.
- AP mode (access point) with SSID/password and captive portal style info screen.
- mDNS hostname advertising when available.
- HTTP server for configuration portal and diagnostics.
- QR codes for AP credentials and portal URL.

### Cloud & Time
- NTP time sync (STA mode only), with status indicators.
- Optional HTTPS cloud upload (payload includes CO₂, temp, humidity, battery, device IDs).
- HMAC authentication for cloud payloads when configured.

### Configuration & Persistence
- Settings stored in JSON with defaults and validation:
  - Wi‑Fi credentials and AP settings.
  - Display mode and graph scaling.
  - Alert thresholds and enable/disable.
  - Calibration parameters (ASC enable, altitude, ambient pressure).
  - Device ID and cloud enable.
- Safe handling of read‑only filesystems (USB mode warning).

### Calibration
- Forced calibration via portal with range checks.
- ASC (automatic self calibration) toggle.
- Altitude and ambient pressure compensation with clamped limits.

### Diagnostics & Status
- On-screen status messages with timeouts.
- Small Wi‑Fi/NTP/cloud status tags in the UI.
- Logging with throttling to avoid flooding the serial console.

## Cloud (knowco2-cloud)

### Lambda Services
- AWS Lambda functions for device data ingestion and authentication.
- Bundled crypto and HTTP libraries for HMAC verification and secure transport.

### Portal & Infrastructure
- Web portal for cloud-side management (static site assets under `portal`).
- Terraform definitions for cloud infrastructure.

## App (knowco2-app)

### iOS App
- Native iOS app project structure with assets.
- Intended for viewing device data and onboarding (details evolve with app code).

## Website (knowco2-website)

### Public Web Content
- Static website with product information, calibration guides, and demos.
- Interactive demo assets and branding resources.

## Design & Hardware

### CAD / 3D Models
- 3D model versions under `knowco2-3d-models`.

### Hardware Docs
- PDFs and design references under `knowco2-hardware` and root.

## Testing & Backups

- `testing-firmware` contains historical backups and assets used for regression checks.
