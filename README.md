# Medisana BLE

<p align="center">
  <img src="https://raw.githubusercontent.com/michelnet/homeassistant-medisana-ble/main/custom_components/medisana_ble/brand/icon%402x.png" alt="Medisana BLE" width="180" height="180">
</p>

A local Home Assistant Bluetooth Low Energy (BLE) integration for **Medisana blood pressure monitors,
thermometers, classic body analysis scales, pulse oximeters and glucose meters**, with automatic
discovery and support for the Home Assistant Marketplace and HACS custom repositories.
No Medisana account or cloud service is required.

This project manages devices whose measurements are transferred directly over
**BLE GATT**, using Home Assistant's Bluetooth adapters or Bluetooth proxies.
WLAN and cloud device management are outside the project scope. A BLE interface
used only to configure a device's WLAN connection does not qualify as a supported
measurement interface. A device with multiple radios qualifies only through its
direct BLE measurement protocol.

The Home Assistant domain is **`medisana_ble`**. The supported profiles share
connection, retry and state-restoration handling. Device-specific validation
is listed below; a supported protocol is not a hardware compatibility guarantee.

The VitaDock+ icon with the Medisana wordmark comes from the
[official Medisana app listing](https://play.google.com/store/apps/details?id=de.medisana.vitadockplus)
and is bundled locally for identification of the supported device manufacturer.
VitaDock+ and Medisana are trademarks of Medisana GmbH.

## Device compatibility

The table separates the implemented driver from validation on each model.
Optional measurements depend on the fields transmitted by the device.
**Simulated** means protocol and GATT-session tests, not a physical-device test.
**Candidate** means model compatibility has not been established.

| Device type | Model | BLE service | Driver measurements | Validation | Model notes |
| --- | --- | --- | --- | --- | --- |
| Blood pressure | BU 570 connect | `1810` | Pressure, pulse, user ID | Hardware tested | Discovery, setup and transfer tested |
| Blood pressure | BU 584 connect | `1810` | Pressure, pulse, user ID | [User-confirmed](https://github.com/michelnet/homeassistant-medisana-ble/issues/1) | HA 2026.9.3; ESPHome proxy; transfer and retry without pairing |
| Blood pressure | Medisana wrist monitor (US variant) | `1810` | Pressure, pulse, user ID | [User-confirmed](https://community.home-assistant.io/t/medisana-ble-blood-pressure-and-temperature-in-home-assistant-local-and-cloud-free/1027847/2) | BLE name `AOJ-35C`; discovery and transfer via ESP32 proxy |
| Blood pressure | Medisana cordless upper-arm monitor (US variant) | `1810` | Pressure, pulse, user ID | [User-confirmed](https://community.home-assistant.io/t/medisana-ble-blood-pressure-and-temperature-in-home-assistant-local-and-cloud-free/1027847/2) | BLE name `ARM-30H`; discovery and transfer via ESP32 proxy |
| Blood pressure | BU 572 connect | `1810` | Pressure, pulse, user ID | Simulated; model unconfirmed | Optional clock sync; [independent integration](https://github.com/jagula/home-assistant-medisana-bu572) reports this profile |
| Blood pressure | BW 360 connect (European variant) | To verify | Unconfirmed | Candidate | Type `AOJ-35C`; US report does not validate the European model |
| Blood pressure | BU 546 connect | To verify | Unconfirmed | Candidate | Standard profile and firmware behavior need validation |
| Blood pressure | BU 575 connect | To verify | Unconfirmed | Candidate | Standard profile and firmware behavior need validation |
| Blood pressure | BU 580 connect | To verify | Unconfirmed | Candidate | Standard profile and firmware behavior need validation |
| Blood pressure | BUA 52 connect | To verify | Unconfirmed | Candidate | Standard profile and firmware behavior need validation |
| Thermometer | TM 750 connect — multifunction thermometer | `1809` | Temperature, temperature type | Hardware tested | BLE name `TS42B`; discovery, setup and transfer tested |
| Body analysis scale | BS 410 | `78B2` | Weight, composition, BMI, user ID | Simulated; model unconfirmed | Select clock origin **2010** |
| Body analysis scale | BS 430 connect | `78B2` | Weight, composition, BMI, user ID | Simulated; model unconfirmed | Select clock origin **1970** |
| Body analysis scale | BS 440 connect | `78B2` | Weight, composition, BMI, user ID | Simulated; model unconfirmed | Select clock origin **1970** |
| Body analysis scale | BS 444 connect | `78B2` | Weight, composition, BMI, user ID | Simulated; model unconfirmed | Select clock origin **2010** |
| Body analysis scale | BS 550 | To verify | Unconfirmed | Candidate | Classic BLE packet compatibility needs validation |
| Body analysis scale | BSA 45 | To verify | Unconfirmed | Candidate | Classic BLE packet compatibility needs validation |
| Pulse oximeter | PM 100 connect — standard-profile variant | `1822` | Oxygen saturation, pulse, pulse amplitude index | Simulated; model unconfirmed | Spot-check and continuous; proprietary variants require another driver |
| Glucose meter | MediTouch 2 connect | `1808` | Glucose, timestamp, sequence, meal context | Experimental; simulated | Standard-profile candidate; exact firmware layout unconfirmed |
| Glucose meter | MediTouch 2 connect dual | `1808` | Glucose, timestamp, sequence, meal context | Experimental; simulated | Standard-profile candidate; dual variant not separately validated |

Version **1.1.0** adds the scale, pulse-oximeter and glucose profiles and optional
blood-pressure clock synchronization. **The new profiles have not yet been
validated on physical devices with this integration.** The classic scale driver
is based on the original MIT [BS440 project](https://github.com/keptenkurk/BS440).
Composition includes body fat, water, muscle, bone mass and basal metabolism;
BMI requires a transmitted height. Glucose transfer supports automatic reception
and optional standard RACP record retrieval.

Other BU/BW monitors can use the blood pressure driver if they expose its standard
service and measurement format. Candidate rows do not imply confirmed support.
Additional models require a documented direct BLE measurement protocol before
they can be added to the compatibility list. Newer Lefu BLE protocols and legacy
proprietary BLE oximeters still lack a complete implemented driver. Yolanda
endpoints used only for WLAN configuration are excluded from this project.
The shared scale advertisement UUID alone cannot identify a compatible device.

Medisana identifies the European **BW 360 connect** (article **51076**) as type
**AOJ-35C** in its [instruction manual](https://cloud.vitadock.com/doc/download/51076/default/manual/GB_DE_FR_NL_ES_IT_PT_GR_FI_SE_NO_DK_PL_CZ_SK_SI_HR_HU_RO_BG_EE_LV_LT_RU_AR_FA_UR_TR).
The user confirmation above concerns the US variant; the European BW 360 connect
has not been separately validated with this integration. The US cordless monitor
remains identified as `ARM-30H`; no equivalent European product name has been
verified.

The physical validation results apply to the models explicitly marked as tested
or user-confirmed above. The integration is also covered by tests with **simulated
Bluetooth hardware**. The Medisana name does not imply compatibility with every
Medisana device.

## Features

- Automatic discovery of all five implemented profiles, including known local names and manufacturer identifiers.
- Standard Home Assistant discovery card with a confirmation step and automatic device-type detection.
- Manual setup with Bluetooth address, device name and device type.
- Blood pressure: systolic, diastolic and mean arterial pressure, pulse and user ID.
- Thermometer: temperature, with optional temperature-type information.
- Classic scales: weight and optional composition measurements, BMI and user ID.
- Pulse oximeters: oxygen saturation, pulse and optional pulse amplitude index.
- Glucose meters: concentration, measurement status, sequence number and meal context.
- Shared battery level, measurement time and reception time sensors.
- Home Assistant long-term statistics for blood pressure, pulse, temperature and battery level.
- Optional Bluetooth user filter for blood pressure monitors and classic scales, including user ID `0`.
- Last readings stay available while the device sleeps and are restored after a Home Assistant restart.
- Standard device information, when supplied by the device.
- Independent connection sessions, bounded retries, and cleanup on unload or shutdown.
- English and German interface translations.

## Requirements

- Home Assistant **2026.9.0 or newer**; the automated test environment uses 2026.11.1.
- A Bluetooth adapter managed by Home Assistant.
- The device must be awake and within Bluetooth range during data transfer.

Close VitaDock+ and disable any other integration connected to the same device.
Other clients can otherwise compete for its Bluetooth connection.

<!-- ha-marketplace:exclude-start -->

## Installation

### Marketplace (Home Assistant 2026.11 or newer)

1. Open **Settings → Marketplace**. On first use, read and accept the Marketplace
   notice to enable installation and updates.
2. Select **Add from link** and enter
   `https://github.com/michelnet/homeassistant-medisana-ble`.
3. Connect GitHub if prompted, then select **Add**. If a content type is requested,
   select **Integration**.
4. Open **Medisana BLE**, select **Install** and follow the installation prompts.
   Restart Home Assistant if requested.
5. Continue with [Setup](#setup) below.

This repository can be added from a link; inclusion in the default Marketplace
catalog is not required. A GitHub connection is needed to add and update custom
repositories.

When upgrading Home Assistant from HACS to 2026.11 or newer, the Marketplace
automatically takes over installed integrations, custom repositories and update
entities. Medisana BLE does not need to be reinstalled or set up again. Open the
Marketplace once and accept its notice to enable further updates. See the
[official migration guide](https://www.home-assistant.io/integrations/marketplace/#coming-from-hacs).

### HACS (Home Assistant 2026.9–2026.10)

1. Open **HACS → Custom repositories**.
2. Add `https://github.com/michelnet/homeassistant-medisana-ble` with category **Integration**.
3. Download **Medisana BLE** and restart Home Assistant.
4. Continue with [Setup](#setup) below.

On Home Assistant 2026.11 or newer, use the built-in Marketplace described above.

### Manual installation

1. Copy [`custom_components/medisana_ble`](custom_components/medisana_ble) into
   `/config/custom_components/medisana_ble` on Home Assistant. When using a source
   archive, extract it first and copy that component directory.
2. Restart Home Assistant.
3. Continue with [Setup](#setup) below.

<!-- ha-marketplace:exclude-end -->

## Setup

Wake your device and start a measurement or Bluetooth transfer. Open
**Settings → Devices & services** and configure its discovery card. If installation
opens the setup flow directly, follow that flow to select your device.

If no discovery card appears, use **Add integration → Medisana BLE → Find a nearby
device**. The fallback **Enter a Bluetooth address** accepts a known Bluetooth MAC
address, a name and one of the five supported device types.
The device can be asleep during manual setup; values remain unknown until the
first complete measurement arrives.

For a **classic body analysis scale**, select its clock format before setup
finishes: **2010 for BS 410/444**, **1970 for BS 430/440**. Home Assistant then sets
the scale clock and requests stored readings. These formats can be changed later
under **Configure**. Select a user filter there to keep an individual's history
separate from other people using the scale.
Scales wait **12 seconds** before connecting so the weighing and body analysis
can finish. This delay is configurable from 0 to 30 seconds. Allow roughly
15–20 seconds after weighing before expecting new values, as described in the
[independent BS444 integration](https://github.com/andriensis/ha-medisana-scale).

Both US-variant blood pressure monitors were reported to work with the
integration's existing automatic discovery. The report does not identify which advertisement field
triggered discovery. Their Bluetooth names are **`AOJ-35C`** (wrist) and
**`ARM-30H`** (arm), with reported addresses starting with **`A4:C1:38`**.
If manual setup is needed, enter the device's **full six-byte MAC address** from
a BLE scan and select **Blood pressure monitor**; the prefix alone is not a
complete address. The MAC prefix is only a hint for locating the device in a scan;
automatic discovery uses the signatures listed below.

### Dependency installation errors

If startup reports `Requirements for medisana_ble not found` for
`bleak-retry-connector==4.7.0`, install version 1.0.4 or later and restart Home
Assistant. Medisana BLE uses the connector version supplied by Home Assistant's
Bluetooth integration through its `bluetooth_adapters` dependency instead of
installing a second, fixed version. If the error persists, check the preceding
package installation logs for download failures or dependency conflicts. Other
custom Bluetooth integrations with fixed connector requirements may also need
updating.

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
classic scale discovery.

Once connected, the device must expose a measurement characteristic that supports
indications or notifications:

| Device type | Measurement characteristic |
| --- | --- |
| Blood pressure | `00002a35-0000-1000-8000-00805f9b34fb` |
| Thermometer | `00002a1c-0000-1000-8000-00805f9b34fb` |
| Classic scale | `8A21` weight, optional `8A22` composition and `8A82` user profile; writable `8A81` command endpoint required |
| Pulse oximeter | `2A5E` spot-check and/or `2A5F` continuous |
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
| Oxygen saturation, pulse, pulse amplitude index | Pulse oximeter | Standard PLX values; quality/status information is retained as attributes |
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

With `-1`, all users share one sensor group. This version does not create separate
sensor groups per user. Select a fixed user when using these sensors for an
individual's history. Thermometers have no user filter or other configurable
options. Pulse oximeters and glucose meters also have no user filter.

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

## Development

See [Testing on your installation](docs/TESTING.md) for physical-device checks.

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
[`THIRD_PARTY_NOTICES.txt`](custom_components/medisana_ble/THIRD_PARTY_NOTICES.txt)
inside the distributed integration directory.

The glucose and pulse-oximeter parsers are independent implementations based on
the published [Glucose Service](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/GLS_v1.0.1/out/en/index-en.html),
[Pulse Oximeter Service](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/PLXS_v1.0.1/out/en/index-en.html)
and GATT field definitions. The VitaDock research informed the compatibility
assessment; no decompiled app implementation or native body-composition library
is bundled with this integration.
