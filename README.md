# Medisana BLE

<p align="center">
  <img src="custom_components/medisana_ble/brand/medisana.png" alt="Medisana BLE" width="180" height="180">
</p>

A local Home Assistant Bluetooth integration for **Medisana blood pressure monitors
and thermometers**, with automatic discovery and HACS custom-repository support.
No Medisana account or cloud service is required.

The VitaDock+ icon with the Medisana wordmark comes from the
[official Medisana app listing](https://play.google.com/store/apps/details?id=de.medisana.vitadockplus)
and is bundled locally for identification of the supported device manufacturer.
VitaDock+ and Medisana are trademarks of Medisana GmbH.

## Device compatibility

| Model | Protocol and validation |
| --- | --- |
| BU-570 / BU 570 connect | Standard Blood Pressure service. The original blood pressure integration reports a successful physical Home Assistant test. |
| TM 750 connect | Standard Health Thermometer service; advertises as `TS42B`. The original thermometer integration reports testing this device. |
| BU-575, BU-584 and other models | Compatibility has not been confirmed. |

The combined integration is covered by automated tests with **simulated Bluetooth
hardware**. A physical test of this combined version is still needed for each
device type. The Medisana name does not imply compatibility with every Medisana
device.

## Features

- Automatic discovery of blood pressure monitors and thermometers, including known local names and manufacturer identifiers.
- Standard Home Assistant discovery card with a confirmation step and automatic device-type detection.
- Manual setup with Bluetooth address, device name and device type.
- Blood pressure: systolic, diastolic and mean arterial pressure, pulse and user ID.
- Thermometer: temperature, with optional temperature-type information.
- Shared battery level, measurement time and reception time sensors.
- Optional Bluetooth user filter for blood pressure monitors, including valid user ID `0`.
- Last readings stay available while the device sleeps and are restored after a Home Assistant restart.
- Standard device information, when supplied by the device.
- Independent connection sessions, bounded retries, and cleanup on unload or shutdown.
- English and German interface translations.

## Requirements

- Home Assistant **2026.9.0 or newer**; the automated test environment uses 2026.9.2.
- A Bluetooth adapter managed by Home Assistant.
- The device must be awake and within Bluetooth range during data transfer.

Close VitaDock+ and disable any previous integration connected to the same device.
Other clients can otherwise compete for its Bluetooth connection.

## Installation

### HACS

1. Open **HACS → Custom repositories**.
2. Add `https://github.com/michelnet/homeassistant-medisana-ble` with category **Integration**.
3. Download **Medisana BLE** and restart Home Assistant.
4. Wake your device and start a measurement or Bluetooth transfer.
5. Open **Settings → Devices & services** and configure the discovered device.

This repository is installed as a HACS custom repository. Inclusion in the default
HACS catalogue is not required.

### Manual installation

1. Copy [`custom_components/medisana_ble`](custom_components/medisana_ble) into
    `/config/custom_components/medisana_ble` on Home Assistant. When using a source
   archive, extract it first and copy that component directory.
2. Restart Home Assistant.
3. Start a measurement or Bluetooth transfer on the device.
4. Configure its discovery card under **Settings → Devices & services**.

If no discovery card appears, use **Add integration → Medisana BLE → Find a nearby
device**. The fallback **Enter a Bluetooth address** accepts a known Bluetooth MAC
address, a name and the device type (**Blood pressure monitor** or **Thermometer**).
The device can be asleep during manual setup; values remain unknown until the
first complete measurement arrives.
The integration recognizes these connectable advertisement signatures:

| Device type | Advertisement field | Value |
| --- | --- | --- |
| Blood pressure | Manufacturer data ID | `18498` (`0x4842`) |
| Blood pressure | Manufacturer data ID | `31256` (`0x7A18`) |
| Blood pressure | Local name | `1872B` |
| Blood pressure | Blood Pressure service UUID | `00001810-0000-1000-8000-00805f9b34fb` |
| Thermometer | Local name | `TS42B` |
| Thermometer | Health Thermometer service UUID | `00001809-0000-1000-8000-00805f9b34fb` |

The blood pressure manufacturer IDs and local name originate from the discovery
signatures used by the [Medisana BP BLE integration](https://github.com/bkbilly/medisanabp_ble).
The thermometer signature comes from the original Medisana thermometer integration.
These signatures allow discovery when an advertisement omits the service UUID.
The standard service UUIDs can also match another manufacturer's device: confirm
only your Medisana device.

Once connected, the device must expose a measurement characteristic that supports
indications or notifications:

| Device type | Measurement characteristic |
| --- | --- |
| Blood pressure | `00002a35-0000-1000-8000-00805f9b34fb` |
| Thermometer | `00002a1c-0000-1000-8000-00805f9b34fb` |

Discovery requires an advertisement while Home Assistant is running. Installing
an integration cannot wake a powered-off Bluetooth device. Home Assistant presents
its normal confirmation card; discovery does not silently create an entry.

## Sensors and options

| Sensor | Device type | Meaning |
| --- | --- | --- |
| Systolic blood pressure | Blood pressure | Systolic value, displayed in mmHg by default |
| Diastolic blood pressure | Blood pressure | Diastolic value, displayed in mmHg by default |
| Mean arterial pressure | Blood pressure | MAP value supplied by the monitor |
| Pulse | Blood pressure | Pulse rate in bpm, when present |
| User ID | Blood pressure | Bluetooth user ID, when present |
| Temperature | Thermometer | Temperature, with Celsius/Fahrenheit conversion |
| Battery level | Both | Battery percentage, when the standard BLE battery characteristic is available |
| Last measurement | Both | Device timestamp, interpreted in Home Assistant's time zone |
| Last received | Both | Time Home Assistant accepted the displayed measurement |

The temperature sensor exposes the optional `temperature_type` attribute when
available. Standard device information includes manufacturer, model, firmware,
serial number and hardware revision when supplied by the device.

For blood pressure monitors, the integration's **Configure** options provide a
user filter. `-1` accepts all users; `0`–`254` filters for that transmitted Bluetooth
user ID. The ID can differ from the user number on the monitor. Readings without a
user ID are accepted only with `-1`. Changing the filter clears displayed values
until a matching reading arrives, preventing the previous user's values from
being restored under a different filter.

With `-1`, all users share one sensor group. This version does not create separate
sensor groups per user. Select a fixed user when using these sensors for an
individual's history. Thermometers have no user filter or other configurable
options.

## Measurement handling

Both device types use the blood pressure integration's session logic. A complete
valid packet updates sensors immediately. The connection remains open for further
records until the device disconnects or a three-minute session limit expires. The
next attempt is allowed after at least 15 seconds if Home Assistant still knows a
reachable Bluetooth path. Only one session runs per device.

Blood pressure packets use IEEE-11073 SFLOAT decoding with mmHg/kPa conversion.
Temperature packets use IEEE-11073 32-bit FLOAT decoding with Celsius/Fahrenheit
conversion. Packet lengths and optional fields are validated before readings are
accepted.

Within a running integration, older device timestamps cannot replace newer readings;
duplicate timestamped packets are ignored. Missing optional measurement fields
become unknown instead of inheriting values from a different measurement.
Disconnects do not publish synthetic zero readings. Battery and device-information
characteristics are optional.

Restored sensor values survive Home Assistant restarts. After a restart, the first
received memory record can temporarily replace a newer restored reading, until a
newer record arrives. If the device clock is moved backwards during operation,
reload the integration to reset timestamp ordering. The component does not import
a complete device history into Recorder with its original timestamps.

These are retained individual measurements, so sensors do not generate long-term
averages over the time they remain displayed. Normal Home Assistant history,
Recorder and backups can contain the readings. This driver's logs do not include
raw measurement packets or health values.

## Development

See [Testing on your installation](docs/TESTING.md) for physical-device checks.

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_test.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Automated tests cover protocol decoding, discovery, configuration, user filters,
Bluetooth sessions, state restoration and sensor setup. The Bluetooth transport
and measurements are synthetic; those tests do not verify physical device timing
or radio behavior.

The pure protocol tests also run without Home Assistant:

```sh
python3 -m unittest discover -s tests -p test_parser.py
```

GitHub Actions are provided for tests, Ruff, Hassfest and HACS validation. The HACS
Brands check is excluded for this custom repository.

Protocol references: [Home Assistant Bluetooth APIs](https://developers.home-assistant.io/docs/core/bluetooth/api/),
[Bluetooth integration guidance](https://developers.home-assistant.io/docs/bluetooth/),
and the [Bluetooth SIG GATT Specification Supplement](https://btprodspecificationrefs.blob.core.windows.net/gatt-specification-supplement/GATT_Specification_Supplement.pdf).
