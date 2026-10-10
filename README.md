# Satellite Orbital Tracker Integration for Home Assistant

[![HACS Default](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://my.home-assistant.io/redirect/hacs_repository/?owner=baylanger&repository=satellite_orbital_tracker)
[![STARS](https://img.shields.io/github/stars/baylanger/satellite_orbital_tracker?style=for-the-badge&label=Stars&color=yellow)](https://github.com/baylanger/satellite_orbital_tracker/stargazers)
[![DOWNLOADS](https://img.shields.io/github/downloads/baylanger/satellite_orbital_tracker/total?style=for-the-badge&label=Downloads&color=blue)](https://github.com/baylanger/satellite_orbital_tracker/releases)
[![VERSION](https://img.shields.io/github/v/release/baylanger/satellite_orbital_tracker?style=for-the-badge&color=purple)](https://github.com/baylanger/satellite_orbital_tracker/releases)

Track the International Space Station (ISS), the Hubble Space Telescope, or any custom satellite simultaneously in Home Assistant.

This integration operates entirely locally and does not rely on external cloud APIs except for fetching public orbital element sets (TLEs) from CelesTrak (not rate-limited).

It is an advanced, high-precision custom integration for Home Assistant that tracks any satellite using real-time orbital mechanics. It uses local SGP4 orbit propagation using [Skyfield](https://rhodesmill.org/skyfield/) and your Home Assistant zone coordinates to calculate live subpoint positions and predict upcoming overhead passes.

This integration utilizes the `skyfield` library and live CelesTrak TLE (Two-Line Element) data to perform **local SGP4 propagation**, giving you institutional-grade positional accuracy entirely offline and free of cloud dependencies.

---

## Features

- **Local SGP4 Tracking**: Computes position locally without relying on external cloud tracking APIs.
- **UI Config Flow**: Set up and configure satellites directly from the Home Assistant user interface.
- **Multi-Satellite Support**: Track multiple spacecraft concurrently.
- **Resilient Fallback & Caching**: Memory caches TLE data and persistent storage to handle intermittent network issues and utilizes a multi-pass lookahead engine to handle mid-pass edge cases.
- **Automatic Home Overhead Pass Detection**: Automatically calculates your upcoming local overhead passes using your Home Assistant zone coordinates (`latitude`, `longitude`, `elevation`), exposing transition times and maximum elevation attributes.
- **Automatic Satellite Identification & Naming**: Enter a NORAD ID (e.g., `25544` for the ISS), and the integration dynamically fetches the official satellite name directly from CelesTrak's GP data feed. This name is automatically used to register your device and format your sensor entity names (e.g., `Satellite ISS (Zarya)`).
- **Configurable Polling Interval**: Fine-tune your update frequency between 5 and 3600 seconds directly during setup or through the options flow.
- **Multi-Language Support**: Fully localized with built-in translation dictionaries for English (`en`), French (`fr`), German (`de`), Español (`es`)
- **Map Optimized**: Exposes standard latitude, longitude, and elevation attributes natively compatible with Home Assistant's Map Card.

---

## Installation

### Option 1: HACS (Recommended)

1. Ensure you have [HACS](https://hacs.xyz/) installed.
2. Open HACS in your Home Assistant dashboard and go to **Integrations**.
3. Click the three dots in the top right corner and select **Custom repositories**.
4. Paste your GitHub repository URL (`https://github.com/baylanger/satellite_orbital_tracker`) and select **Integration** as the category.
5. Click **Add**.
6. Find **ISS Predicted Tracker** in the list, click **Download**, and restart Home Assistant.

### Option 2: Manual Installation

1. Download or clone this repository.
2. Copy the `custom_components/satellite_orbital_tracker` folder into your Home Assistant configuration directory (`/config/custom_components/`).

---

*Restart* Home Assistant to complete the setup.

---

## Configuration
In Home Assistant,
1. go to Settings > Devices & Services.
2. Click Add Integration in the bottom right.
3. Search for Satellite Orbital Tracker
4. Configure the tracking parameters:

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **NORAD ID** | String | *Required* | The catalog number of the satellite (e.g., `25544`). Used to query CelesTrak and compute orbital paths. |
| **Update Interval** | Number | `120` | How often (in seconds) the sensor data and coordinates refresh (bounded between 5 and 3600 seconds). |

5. Click **Submit**.

### Managing Options
You can later adjust the polling update interval at any time by clicking the **Configure** (gear) button on the integration entry in your UI.

### Finding Satellite Catalog Numbers (`norad_id`)
You can look up the unique NORAD Catalog Number (CATNR) for any spacecraft using the [CelesTrak Satellite Catalog (SATCAT)](https://celestrak.org/satcat/search.php). 

* **Popular Presets:**
  * **ISS (Zarya):** `25544`
  * **Hubble Space Telescope:** `20580`
  * **Tiangong Space Station:** `48274`

---

## Generated Sensors & Naming Convention

Each satellite instance creates a dedicated device with three namespaced entities to prevent ID collisions when tracking multiple spacecraft:

1. Status Sensor
Entity ID Pattern: `sensor.[satellite_name]_status` (e.g., sensor.iss_zarya_status)
State: Current operational tracking state (`In Orbit`, `Approaching (...)`, `Overhead Now!`).
Attributes:
  - `latitude`, `longitude` : Live coordinates
  - `next_pass_time` : ISO8601 timestamp of the next upcoming orbital pass over your home coordinates
  - `next_pass_event` : The phase of the pass (Rise (Above 10°), Culmination (Highest Point), or Set
    - Rise (Above 10°): The ISS crosses above your 10° horizon threshold. It enters your visible sky.
    - Culmination (Highest Point): The ISS reaches its peak elevation angle in the sky directly above (or closest to) your home coordinates.
    - Set: The ISS dips back below the 10° threshold, ending the visible pass.
  - `source` : Tracking engine identifier (Local SGP4 Engine)

2. Orbital Altitude Sensor
Entity ID Pattern: sensor.[satellite_name]_orbital_altitude
State: Physical orbital height above the Earth's geoid.
Unit of Measurement: km
State Class: measurement

3. Next Pass Max Elevation Sensor
Entity ID Pattern: sensor.[satellite_name]_next_pass_max_elevation
State: Peak sky elevation angle for the next upcoming overhead pass.
Unit of Measurement: °
State Class: measurement

---

## Recommended Map Card Setup

To keep the map cleanly zoomed out on a global view and display the historical trail of the ISS with timestamps, add a Manual card to your dashboard using the following YAML configuration:

```yaml
type: map
auto_fit: false
default_zoom: 2
hours_to_show: 3.5
entities:
  - entity: sensor.satellite_iss_zarya_status
    name: ISS
    focus: true
    label_mode: icon
theme_mode: auto
```
Map Options Explained:
- `auto_fit: false`: Prevents the map from aggressively resetting or jumping focus every time coordinates update.
- `default_zoom: 2`: Keeps the map view zoomed out globally across the planet.
- `hours_to_show: 3.5`: Plots a trailing history line on the map so you can hover over previous points to see timestamps and direction of travel.
- `focus: true`: Keeps this entity in view when the map moves and zooms to show your entities.
- `theme_mode: auto`: Automatically adjusts to match your dashboard's light or dark theme mode.

---

## Public orbital element sets (TLEs)

For each satellite, the integration fetches the public orbital element sets (TLEs) every 12 hours (or immediately upon startup; if the cached data becomes older than 43,200 seconds).

- Why 12 Hours?
Orbital Drift: Satellites experience constant perturbations from atmospheric drag, Earth's non-uniform gravity field, and solar radiation pressure. Over time, predicting positions too far into the future using stale TLEs introduces increasing error.
- CelesTrak Updates: CelesTrak updates General Perturbations (GP) elements multiple times daily for active satellites. Refreshing every 12 hours strikes an ideal balance—it keeps your local SGP4 propagation accurate and up-to-date without spamming external servers or hitting rate limits.
- Resilient Caching: If CelesTrak experiences a brief downtime or network error during a scheduled 12-hour refresh, the coordinator falls back to your currently cached TLE data rather than crashing the sensor states.

---

## License
This project is licensed under the MIT License. See the LICENSE file for details.
