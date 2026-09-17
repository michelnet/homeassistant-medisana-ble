# Testing Medisana BLE

## Automated simulation

Create the development environment described in the [README](../README.md), then
run the complete suite:

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
.venv/bin/python -m pytest tests/test_end_to_end.py -v
```

These use Home Assistant's discovery matching, configuration entries, coordinator
and sensor platform with a simulated Bluetooth transport.

## Physical device checks

Medisana BLE has completed successful Home Assistant hardware tests with both
supported devices:

- **BU-570 / BU 570 connect:** automatic discovery, setup and blood-pressure
  measurement transfer were successful.
- **TM 750 connect:** automatic discovery as `TS42B`, setup and temperature
  measurement transfer were successful.

These results validate the listed models. They do not establish compatibility
with other Medisana devices. The following checklist can be used to repeat the
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
8. For a blood pressure user filter, check the actual received user ID before
   selecting it. A filter change should clear old readings until a matching
   record arrives. Thermometers should offer no user filter.

Older records cannot overwrite newer device timestamps within the same running
integration. After a restart, restored values do not seed that timestamp ordering;
the first accepted memory record can temporarily replace a newer restored reading.
The integration does not import a complete device history into Recorder using
its original timestamps.

## Reporting a result

Record the device model, Home Assistant version and Bluetooth adapter, whether
discovery succeeded, and whether readings never arrived or stopped updating after
the first transfer.

For a failed test, enable debug logging from the integration's menu, repeat the
transfer, then disable debug logging to collect the log. Inspect logs before
sharing them and omit passwords, access tokens and measurements you do not wish
to share.
