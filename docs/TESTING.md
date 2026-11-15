# Testing Medisana BLE

Only direct BLE measurement transfer is in scope. Use a Home Assistant Bluetooth
adapter or Bluetooth proxy. Devices that expose BLE solely for WLAN setup are
excluded; WLAN and cloud data transfer are not part of this project.

## Automated simulation

Create the development environment described in the [README](../README.md) using
Python 3.14.2 or newer. The official `pytest-homeassistant[bluetooth]` package
provides fixtures and Bluetooth test dependencies for Home Assistant 2026.11.1.
Then run the complete suite:

```sh
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Tests exercise protocol parsing, Home Assistant discovery and configuration,
connection sessions, sensor values and restored state. Bluetooth communication is
simulated. Test packets are synthetic inputs, not recordings of a person's
measurements. Passing tests do not verify radio range, pairing behavior or
physical device transmission timing.

The focused end-to-end tests can be run with:

```sh
.venv/bin/python -m pytest tests/test_end_to_end.py tests/test_profile_sessions.py -v
```

These use Home Assistant's discovery matching, configuration entries, coordinator
and sensor platform with a simulated Bluetooth transport.

## Marketplace installation and migration

On Home Assistant 2026.11 or newer, check the distribution separately from the
simulated Bluetooth tests:

1. Open **Settings → Marketplace**, accept its first-use notice and add
   `https://github.com/michelnet/homeassistant-medisana-ble` using **Add from link**.
   Connect GitHub if requested and select **Integration** if a type is needed.
2. Install **Medisana BLE**. Follow any restart prompt, then verify that
   **Medisana BLE** appears under **Settings → Devices & services → Add integration**.
3. Complete the physical-device checks below.
4. For an installation upgraded from HACS, confirm that Medisana BLE remains
   installed and its custom repository and update entity have migrated. Existing
   device configuration and entity IDs should be preserved without reinstallation.
5. Install an available integration update through the Marketplace and restart
   Home Assistant. Confirm that the configured devices and retained readings
   remain available.

The repository must retain `hacs.json` at its root and the complete integration
under `custom_components/medisana_ble`, including its versioned manifest,
translations and `brand` images. The existing `hacs/action` and Hassfest CI jobs
validate publisher requirements; the Marketplace performs its own checks during
installation. See the [official validation reference](https://contributors.home-assistant.io/marketplace/reference/validation/).

## Physical device checks

The original profiles have completed successful Home Assistant hardware tests:

- **BU-570 / BU 570 connect:** automatic discovery, setup and blood-pressure
  measurement transfer were successful.
- **TM 750 connect:** automatic discovery as `TS42B`, setup and temperature
  measurement transfer were successful.

These results validate the listed models. They do not establish compatibility
with other Medisana devices. The new classic scale, pulse-oximeter and glucose
profiles in 1.1.0 currently have simulated validation only; no new physical-device
validation is implied. Additional user reports are listed in the README.
The following checklist can be used to repeat the
physical tests on another Home Assistant installation:

1. Check the Home Assistant version (2026.9.0 or newer), install the component
   using the README instructions, and restart Home Assistant if newly installed.
2. Confirm that a Home Assistant Bluetooth adapter is available. Close VitaDock+
   and make sure no other integration is connected to the device.
3. Wake the device and start its measurement or Bluetooth transfer. Check
   **Settings → Devices & services** for its discovery card and confirm it.
   If missing, try **Add integration → Medisana BLE → Find a nearby device**. For
   manual setup, enter its Bluetooth address and select the correct device type.
4. Check the sensors for the selected device type:
   - **Blood pressure:** compare systolic pressure, diastolic pressure and pulse
     with the monitor. Check the timestamp and user ID when transmitted.
   - **Thermometer:** compare the temperature with the device in the same unit.
     Check the timestamp and `temperature_type` attribute when available.
5. Check battery level and device information when the device supplies them.
6. Let the device disconnect. Its last values should stay visible. Start another
   measurement or transfer and verify that the values and reception time update.
7. Restart Home Assistant while the device is asleep. Previous readings should
   restore, then update again on the next transfer.
8. For a blood pressure or scale user filter, check the received user ID before
   selecting it. A filter change should clear old readings until a matching
   record arrives. Thermometers should offer no user filter.

For a blood pressure model that needs time synchronization, enable
**Configure → Synchronize device clock**. Check that the device exposes writable
Current Time 2A2B, receives the correct local time and continues sending 2A35
measurements. This option is disabled by default; a missing or read-only time
characteristic must not prevent measurement reception.

For a **BS 410/430/440/444 scale**, choose its clock format during setup:
2010 for BS 410/444, 1970 for BS 430/440. Confirm that the weighing timestamp is
correct. The clock is set before stored records are requested. Stand on the scale
until it recognizes a user slot and completes body analysis; guest weighings may
contain only weight. The connection starts after a configurable delay (12 seconds
by default); allow about 15–20 seconds after weighing. Compare weight, fat, water,
muscle, bone mass and basal metabolism with the scale/app. BMI requires a
transmitted height. Test two users and confirm composition is not carried into a
different weighing that supplies weight only. Repeat with the scale's saved
readings arriving in another order.

For a **standard PM 100 connect / PLX oximeter**, verify service 1822 and either
2A5E or 2A5F. Compare oxygen saturation and pulse with the display; test optional
pulse amplitude index when available. Check the `measurement_type`,
`measurement_status` and `device_sensor_status` attributes. Device-marked invalid,
unavailable, demonstration/testing readings must not become normal numeric values.
The legacy CDxx proprietary oximeter path is not supported.

For a **MediTouch 2 connect / dual protocol candidate**, verify service 1808 and
measurement 2A18; check optional 2A34 context and 2A52 RACP. Model and firmware
compatibility still need physical validation. Check concentration and native
units, device timestamps, sequence number and meal marker against the meter/app.
Test automatic transfer without RACP if the device uses it. RACP requests report
stored records without deleting them; authentication requirements can prevent
stored-record retrieval. A context packet for a different sequence must not be
joined to the reading. Fault-marked and control-solution values remain unknown.
The driver carries the time offset during each session and orders records by
sequence number rather than a possibly adjusted device clock.

Older records cannot overwrite newer device timestamps within the same running
integration for pressure, temperature and scales; glucose uses sequence numbers.
After a restart, restored blood-pressure data also prevents the exact
same or older stored records from being accepted again. Different records sharing
a device timestamp remain valid. The integration does not import a complete device
history into Recorder using its original timestamps.

## Reporting a result

Record the device model, Home Assistant version and Bluetooth adapter, whether
discovery succeeded, and whether readings never arrived or stopped updating after
the first transfer.

For a newly tested profile, also record the firmware version, advertised name,
service/characteristic UUIDs, selected scale clock format and connection delay.
Models with Lefu BLE or other BLE packet layouts should remain unconfirmed until
their direct measurement format has been established. A device with BLE used
only for WLAN setup must be excluded rather than added as a future measurement
driver candidate.

For a failed test, enable debug logging from the integration's menu, repeat the
transfer, then disable debug logging to collect the log. Inspect logs before
sharing them and omit passwords, access tokens and measurements you do not wish
to share.
