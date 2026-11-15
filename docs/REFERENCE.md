# Medisana BLE device reference

Detailed setup, protocol and troubleshooting notes. For installation and the
compatibility table, see the [README](../README.md). Run development commands
from the repository root.

## Device variants

Medisana identifies the European **BW 360 connect** (article **51076**) as type
**AOJ-35C** in its [instruction manual](https://cloud.vitadock.com/doc/download/51076/default/manual/GB_DE_FR_NL_ES_IT_PT_GR_FI_SE_NO_DK_PL_CZ_SK_SI_HR_HU_RO_BG_EE_LV_LT_RU_AR_FA_UR_TR).
The user confirmation in this repository concerns the US variant. The separate
[BW 360 external report](https://github.com/bkbilly/medisanabp_ble/pull/12) documents
real hardware in another integration's proposed branch; it is not a test of
Medisana BLE. The US cordless monitor
remains identified as `ARM-30H`; no equivalent European product name has been
verified.

Both US-variant blood pressure monitors were reported to work with the
integration's existing automatic discovery. The report does not identify which advertisement field
triggered discovery. Their Bluetooth names are **`AOJ-35C`** (wrist) and
**`ARM-30H`** (arm), with reported addresses starting with **`A4:C1:38`**.
If manual setup is needed, enter the device's **full six-byte MAC address** from
a BLE scan and select **Blood pressure monitor**; the prefix alone is not a
complete address. The MAC prefix is only a hint for locating the device in a scan;
automatic discovery uses the signatures in the next section.

## Automatic discovery

The integration recognizes these connectable advertisement signatures:

| Device type | Advertisement field | Value |
| --- | --- | --- |
| Blood pressure | Manufacturer data ID | `18498` (`0x4842`) |
| Blood pressure | Manufacturer data ID | `31256` (`0x7A18`) |
| Blood pressure | Local name | `1872B` |
| Blood pressure | Blood Pressure service UUID | `00001810-0000-1000-8000-00805f9b34fb` |
| Thermometer | Local name | `TS42B` |
| Thermometer | Health Thermometer service UUID | `00001809-0000-1000-8000-00805f9b34fb` |
| Classic scale | Scale service UUID | `000078b2-0000-1000-8000-00805f9b34fb` |
| Pulse oximeter | Pulse Oximeter service UUID | `00001822-0000-1000-8000-00805f9b34fb` |
| Glucose meter | Glucose service UUID | `00001808-0000-1000-8000-00805f9b34fb` |

The known manufacturer identifiers and local names allow discovery when an
advertisement omits the service UUID. The standard service UUIDs can also match
another manufacturer's device: confirm only your Medisana device.
Yolanda WLAN setup endpoints whose name begins with `000000` are excluded from
classic scale discovery. Older PM 150 services contain a variable device-specific
UUID suffix, so they have no generic automatic-discovery manifest matcher. Use
**Find a nearby device** while it advertises, or enter its address manually and
select **Pulse oximeter**. The driver validates the complete legacy service before
sending its start command.

Once connected, the device must expose a measurement characteristic that supports
indications or notifications:

| Device type | Measurement characteristic |
| --- | --- |
| Blood pressure | `00002a35-0000-1000-8000-00805f9b34fb` |
| Thermometer | `00002a1c-0000-1000-8000-00805f9b34fb` |
| Classic scale | `8A21` weight, optional `8A22` composition and `8A82` user profile; writable `8A81` command endpoint required |
| Pulse oximeter | `2A5E` spot-check and/or `2A5F` continuous |
| Legacy PM 150 | `CD04` measurement; `CD01`/`CD02`/`CD03` control notifications and writable `CD20` required |
| Glucose meter | `2A18` measurement, optional `2A34` context and `2A52` record access |

Short UUIDs in this table use the base `0000xxxx-0000-1000-8000-00805f9b34fb`.

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
| Weight, body fat, water, muscle, bone mass | Classic scale | Composition is associated with exactly the same user and timestamp as the weight |
| BMI, basal metabolism | Classic scale | BMI is calculated from the transmitted height; basal metabolism is supplied by the scale |
| Oxygen saturation, pulse, pulse amplitude index | Pulse oximeter | Standard PLX values with optional quality/status; legacy PM 150 supplies saturation and pulse only |
| Blood glucose | Glucose meter | mg/dL or mmol/L, with unit conversion supported by Home Assistant |
| Battery level | All | Battery percentage, when the standard BLE battery characteristic is available |
| Last measurement | All | Device timestamp; classic scale timestamps use their configured epoch, other device clocks use Home Assistant's time zone |
| Last received | All | Time Home Assistant accepted the displayed measurement |

**Last measurement** reflects the date and time transmitted by the device. The
integration applies Home Assistant's configured time zone but cannot detect an
incorrectly set device clock. On both the **BU 570 connect** and the
**TM 750 connect thermometer**, date and time can be set locally using the
device's buttons, without pairing with VitaDock+. Set the device clock to the
local date and time for Home Assistant's configured time zone. The integration
reads the timestamp of these blood pressure monitors and thermometers; their
clocks are not synchronized by default. For blood pressure monitors, **Configure →
Synchronize device clock** enables a local-time write to standard Current Time
characteristic `2A2B` when writable. This may be needed by additional models such
as BU 572; it is disabled by default. Classic scales use their own clock-setting command.
For the BU 570 connect, use the date/time settings described in its instruction
manual.

On the TM 750 connect, set the clock after first use
and after replacing the batteries. With the device switched on, hold **SET** for
about five seconds; use **SET** to change a value and the memory button to confirm
the year, month, day, hour and minute. See the section *Setting the unit of measure,
the time and the date* in the
[TM 750 connect instruction manual](https://cloud.vitadock.com/doc/download/76145TM750connect24sprgWEBV2_1AM20210107.pdf).

The temperature sensor exposes the optional `temperature_type` attribute when
available. Standard device information includes manufacturer, model, firmware,
serial number and hardware revision when supplied by the device.

For blood pressure monitors and classic scales, **Configure** options provide a
user filter. `-1` accepts all users; `0`–`254` filters for that transmitted Bluetooth
user ID (scales: `0`–`8`). The ID can differ from the number on the device. Readings without a
user ID are accepted only with `-1`. Changing the filter clears displayed values
until a matching reading arrives, preventing the previous user's values from
being restored under a different filter.

With `-1`, the main sensors follow all users. Enable **Separate sensors for each
user** to add child devices labeled with each raw Bluetooth ID as it is received.
Blood pressure IDs **0–254** and scale IDs **1–8** get independent groups. No
Bluetooth ID is translated into a person's identity. Scale guest ID **255** is
unassigned (`user_id` unknown, `raw_user_id: 255`); it supplies weight only. Scale
ID **0** remains usable in the main group but is not assigned a personal group.
Existing main sensor IDs remain unchanged. User groups restore independently,
even if their record is outside the retained history window or retention is off.
The user filter applies to the main group; **history, events and separate user
groups receive all transmitted users**. Thermometers, oximeters and glucose meters
have connection/history options but no user filter.

All profiles offer a per-attempt connection timeout (**3–60 seconds**, default
20), maximum attempts (**1–10**, default 2) and optional Bluetooth pairing
(default off). Pairing requires support from the adapter/backend. For a **BSA 45**,
try a **3-second** timeout and **10** attempts if transfers fail. This follows a
conditional external report and needs a test on the actual firmware. Scales also
keep their separate pre-connection delay; these settings have different purposes.
For **BU 572**, try pairing plus clock synchronization and **Set clock before
measurement notifications** when needed. The clock write remains optional and its
default ordering follows measurement subscription. Settings are explicit because
a device name does not establish its firmware behavior.

### Local history and CSV export

The integration retains **200 measurements per configured device** by default in
Home Assistant's private `.storage` directory. **Maximum local history records**
sets a limit of **0–1000**; zero disables retention and removes the stored history.
Removing the device's integration entry also removes its history. Home Assistant
backups may contain these files and Recorder keeps its own independent history.

Records include the device timestamp (when supplied), reception time, raw user ID,
native units, values and available status/context. The main sensor's user filter
and newest-record ordering do not discard older or other-user records from this
bounded history. Replayed timestamped records within the retained window are
deduplicated. Scale composition updates the same record as its weight. Repeated
thermometer indications and untimestamped samples remain separate records.

As an administrator, open **Developer tools → Actions**, choose
**Medisana BLE: Export measurement history**, select the device, and run it.
The response contains `csv`, `record_count` and `filename`. Save the **decoded
content of `csv`** as a UTF-8 `.csv` file; no public web file is created. An optional
Bluetooth user-ID filter limits the export. Omitting it includes unassigned guests.
The device timestamps in this export are separate from Recorder's reception-time
statistics; this integration does not backfill Recorder.

### Measurement events and diagnostics

Subscribe to **`medisana_ble_measurement`** for each new or enriched retained
reading, including downloaded older records and users excluded by the main filter.
Event data contains `entry_id`, `device_type` and `measurement`. The latter has a
stable `id` for timestamped stored records, original `timestamp`, `received_at`,
values and `updated`. For a scale, weight can fire first (`updated: false`), then
composition enriches the same `id` (`updated: true`). These events mean data was
received or enriched; they do not prove a new physical measurement just occurred.
Automations should filter `entry_id` and `measurement.user_id` as needed.

Use **Download diagnostics** on the integration entry for connection counters,
error categories, selected settings, model/firmware information and GATT endpoints
from its last successful connection. Measurements, MAC addresses, serial numbers
and the device-specific portion of legacy service UUIDs are omitted. A sleeping
device may have no collected GATT endpoints yet.

## Measurement handling

All device types use bounded session handling. A complete valid reading updates
sensors immediately. The connection remains open for further records until the
device disconnects or a three-minute session limit expires. The next attempt is
allowed after at least 15 seconds if Home Assistant still knows a reachable
Bluetooth path. Only one session runs per device.

Blood pressure packets use IEEE-11073 SFLOAT decoding with mmHg/kPa conversion.
Temperature packets use IEEE-11073 32-bit FLOAT decoding with Celsius/Fahrenheit
conversion. Packet lengths and optional fields are validated before readings are
accepted.

Classic scale weight and composition can arrive in either order. Records are
joined using user ID and timestamp, and missing composition fields become unknown
for the next weighing. A scale profile arriving later can supply the height for
BMI calculation. The integration requests existing records without deleting them.

Glucose measurements use the device sequence number for ordering, even if its
clock moves backwards. When a record promises context, it is published after
matching context arrives. Time offsets are carried within a connection. Optional
RACP requests report stored records; an authentication failure keeps live reception
available. Control-solution tests and fault-marked values are excluded from numeric
glucose readings. Only the meal marker is exposed from optional context fields.

The experimental legacy PM 150 path subscribes to CD01/CD02/CD03 control and CD04
measurement notifications before writing its start command to CD20. It decodes
only saturation and pulse; no device timestamp or undocumented quality flags are
invented. Only a matching service with all five endpoints is initialized.

PLX decoding supports spot-check and continuous layouts, including optional
timestamps, status and pulse amplitude index. The `measurement_type` attribute
distinguishes them. Invalid/unavailable, demonstration or testing values become
unknown; raw quality flags remain available as attributes.

Within a running integration, older device timestamps cannot replace newer readings
for blood pressure, temperature and scales; glucose uses its sequence number.
For blood pressure monitors, the restored timestamp and measurement values are
retained across a Home Assistant restart so the monitor's last stored record is
not saved again. A different measurement with the same timestamp remains valid.
Every thermometer indication is accepted even when its value and device timestamp
equal the previous packet; the TM 750 can legitimately repeat both. Missing
optional measurement fields become unknown instead of inheriting values from a
different measurement. Disconnects do not publish synthetic zero readings.
Battery and device-information characteristics are optional.

Every accepted thermometer reading produces a Home Assistant state update, even
when the numeric temperature or device timestamp is unchanged. This updates
`last_updated` and allows automations to react to consecutive equal measurements.
Connection, battery and device-information refreshes alone do not mark the retained
temperature as a new measurement.

Restored sensor values survive Home Assistant restarts. Older blood-pressure
records and an exact replay of the restored record are ignored until a newer record
arrives. If the device clock is moved backwards during operation, reload the
integration to reset timestamp ordering. The component does not import a complete
device history into Recorder with its original timestamps.

All numeric measurement sensors use `state_class: measurement` so Home Assistant Recorder can compile
long-term statistics. User ID and timestamp sensors do not have a state class.
Statistics use Home Assistant reception times and time-weighted averages of the
retained values, rather than an average of individual device readings. They do
not backfill measurements at their original device timestamps or preserve every
individual reading after Recorder purges detailed history. This driver's logs
do not include raw measurement packets or health values.

A BU 584 that has never been paired with VitaDock+ may have an unset device clock
starting in 2021. In that case, `last_measurement` reflects that device clock,
while `last_received` shows when Home Assistant received the reading. The reading
is still accepted; an old device timestamp alone does not make it invalid.
For the BU 570 connect and TM 750 connect, an incorrect clock can be corrected
locally on the device as described above. If a BU 570 connect clock is moved
backwards, reload the integration afterwards to reset timestamp ordering.

## Troubleshooting

### Dependency installation errors

If startup reports `Requirements for medisana_ble not found` for
`bleak-retry-connector==4.7.0`, install version 1.0.4 or later and restart Home
Assistant. Medisana BLE uses the connector version supplied by Home Assistant's
Bluetooth integration through its `bluetooth_adapters` dependency instead of
installing a second, fixed version. If the error persists, check the preceding
package installation logs for download failures or dependency conflicts. Other
custom Bluetooth integrations with fixed connector requirements may also need
updating.

## Development

See [Testing on your installation](TESTING.md) for physical-device checks.

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_test.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Use Python **3.14.2 or newer** for the test environment. Tests use the official
`pytest-homeassistant` fixtures with the Bluetooth extra, pinned to Home Assistant
2026.11.1. When reusing an older development environment, uninstall
`pytest-homeassistant-custom-component` before installing `requirements_test.txt`;
the two fixture packages must not be installed together.

Automated tests cover protocol decoding, discovery, configuration, user filters,
Bluetooth sessions, state restoration and sensor setup. The Bluetooth transport
and measurements are synthetic; those tests do not verify physical device timing
or radio behavior.

The pure protocol tests also run without Home Assistant:

```sh
python3 -m unittest discover -s tests -p test_parser.py
python3 -m unittest discover -s tests -p test_thermometer.py
```

GitHub Actions are provided for tests, Ruff, Hassfest and Marketplace/HACS
validation, including brand images. The Marketplace still uses the root
`hacs.json` file and `hacs/action`; neither is renamed. The integration's manifest
declares the **Health & wellbeing** category. See the
[Marketplace publisher documentation](https://contributors.home-assistant.io/marketplace/guides/integrations/).

Protocol references: [Home Assistant Bluetooth APIs](https://developers.home-assistant.io/docs/core/bluetooth/api/),
[Bluetooth integration guidance](https://developers.home-assistant.io/docs/bluetooth/),
and the [Bluetooth SIG GATT Specification Supplement](https://btprodspecificationrefs.blob.core.windows.net/gatt-specification-supplement/GATT_Specification_Supplement.pdf).

## Protocol sources and licenses

The classic scale driver adapts the original MIT-licensed
[keptenkurk/BS440 implementation](https://github.com/keptenkurk/BS440).
Its original copyright and full permission notice are included in
[`THIRD_PARTY_NOTICES.txt`](../custom_components/medisana_ble/THIRD_PARTY_NOTICES.txt)
inside the distributed integration directory.

The glucose and pulse-oximeter parsers are independent implementations based on
the published [Glucose Service](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/GLS_v1.0.1/out/en/index-en.html),
[Pulse Oximeter Service](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/PLXS_v1.0.1/out/en/index-en.html)
and GATT field definitions. The VitaDock research informed the compatibility
assessment; no decompiled app implementation or native body-composition library
is bundled with this integration.
