# Medisana BLE

<p align="center">
  <img src="https://raw.githubusercontent.com/michelnet/homeassistant-medisana-ble/main/custom_components/medisana_ble/brand/icon%402x.png" alt="Medisana BLE" width="180" height="180">
</p>

Local Home Assistant integration for **Medisana BLE blood pressure monitors,
thermometers, body analysis scales, pulse oximeters and glucose meters**.
Works through Home Assistant Bluetooth adapters and proxies, without a Medisana
account or cloud. Only direct BLE measurement transfer is supported; WLAN-only
setup interfaces are excluded.

## Device compatibility

**Hardware tested** and **user-confirmed** refer to this integration.
**Simulated** means synthetic protocol/GATT tests; external reports do not replace
hardware validation here. **Candidate** means compatibility is unconfirmed.
Optional measurements depend on the device's transmitted fields.

### Blood pressure monitors

| Model | BLE service | Driver measurements | Validation here | External evidence | Model notes |
| --- | --- | --- | --- | --- | --- |
| BU 550 connect | `1810` | Pressure, pulse, user ID | Simulated profile; model untested | [2019 research implementation](https://eprints.uklo.edu.mk/id/eprint/2338/1/AIIT2019_ProceedingsFinal_Optimizing%20Bluetooth%20Communication.pdf) | Older BLE model; published `1810`/`2A35` endpoint matches the existing driver |
| BU 570 connect | `1810` | Pressure, pulse, user ID | Hardware tested | [Project-tested](https://github.com/Rudertier/medisana_blood_pressure#readme) | Discovery, setup and transfer tested here |
| BU 584 connect | `1810` | Pressure, pulse, user ID | [User-confirmed](https://github.com/michelnet/homeassistant-medisana-ble/issues/1) | — | HA 2026.9.3; ESPHome proxy; transfer and retry without pairing |
| Medisana cordless upper-arm monitor / ARM-30H | `1810` | Pressure, pulse, user ID | [User-confirmed](https://community.home-assistant.io/t/medisana-ble-blood-pressure-and-temperature-in-home-assistant-local-and-cloud-free/1027847/2) | — | Walmart-purchased unit; BLE name `ARM-30H`; European sales-model mapping unverified |
| BU 572 connect | `1810` | Pressure, pulse, user ID | Simulated; model untested | [Hardware-tested](https://github.com/jagula/home-assistant-medisana-bu572#getestet) | Optional pairing and clock-before-notifications settings; raw user IDs **0/1** |
| BW 360 connect / AOJ-35C | `1810` | Pressure, pulse, user ID | [User-confirmed (AOJ-35C)](https://community.home-assistant.io/t/medisana-ble-blood-pressure-and-temperature-in-home-assistant-local-and-cloud-free/1027847/2) | [BW 360 hardware report](https://github.com/bkbilly/medisanabp_ble/pull/12) | EU manual names type `AOJ-35C`; reported unit bought at Walmart; identical regional firmware not established |
| BU 546 connect | `1810` | Pressure, pulse, user ID | Untested | [Project-tested](https://github.com/Rudertier/medisana_blood_pressure#readme) | Standard GATT endpoint matches; exact firmware not tested here |
| BU 575 connect | `1810` | Pressure, pulse, user ID | Untested | [Working user report](https://github.com/Rudertier/medisana_blood_pressure/issues/9) | External report uses a nearby ESP32 proxy |
| BU 580 connect | `1810` | Pressure, pulse, user ID | Untested | [Success report](https://github.com/bkbilly/medisanabp_ble/issues/4#issuecomment-2582846944); [sync failures](https://github.com/bkbilly/medisanabp_ble/issues/6) | Mixed evidence; repeat synchronization is not established here |
| BUA 52 connect | To verify | Unconfirmed | Candidate | — | No concrete independent working report found in this review |

### Thermometers

| Model | BLE service | Driver measurements | Validation here | External evidence | Model notes |
| --- | --- | --- | --- | --- | --- |
| TM 750 connect — multifunction thermometer | `1809` | Temperature, temperature type | Hardware tested | — | BLE name `TS42B`; discovery, setup and transfer tested here |

### Body analysis scales

| Model | BLE service | Driver measurements | Validation here | External evidence | Model notes |
| --- | --- | --- | --- | --- | --- |
| BS 410 | `78B2` | Weight, composition, BMI, user ID | Simulated; model untested | [Working user report](https://github.com/keptenkurk/BS440/issues/56#issuecomment-311601018) | Clock origin **2010**; external report used public address type |
| BS 430 connect | `78B2` | Weight, composition, BMI, user ID | Simulated; model untested | [Confirmed in support list](https://github.com/bwynants/weegschaal#support) | Clock origin **1970**; verify device timestamps |
| BS 440 connect | `78B2` | Weight, composition, BMI, user ID | Simulated; model untested | [Original implementation](https://github.com/keptenkurk/BS440#readme); [openScale](https://github.com/oliexdev/openScale/wiki/Supported-scales-in-openScale) | **1970** in the original reference; verify clock variant |
| BS 444 connect | `78B2` | Weight, composition, BMI, user ID | Simulated; model untested | [Working user report](https://github.com/keptenkurk/BS440/issues/40#issuecomment-280841093); [HA integration](https://github.com/andriensis/ha-medisana-scale#readme) | Clock origin **2010**; allow body analysis to finish |
| BS 550 | `78B2` | Weight; composition for assigned users | Simulated profile; model untested | [Conditional working report](https://github.com/bwynants/weegschaal/issues/22) | Guest ID **255** now keeps unassigned weight; guest composition is ignored |
| BSA 45 | `78B2` family reported | Classic scale profile | Conditional; model untested | [Conditional working report](https://github.com/keptenkurk/BS440/issues/103) | Try **2010** clock, **3 s** timeout and **10** attempts; defaults remain unchanged |
| BSA 41 connect | To verify | Unconfirmed | Candidate | [Working openScale report](https://github.com/oliexdev/openScale/issues/851) | Classic BS 44x profile available; no packet capture or hardware validation here |

### Pulse oximeters

| Model | BLE service | Driver measurements | Validation here | External evidence | Model notes |
| --- | --- | --- | --- | --- | --- |
| PM 100 connect — standard-profile variant | `1822` | Oxygen saturation, pulse, pulse amplitude index | Simulated; model untested | [Hardware-tested](https://github.com/frankhommers/ha-pulse-oximeter#tested-devices) | Tested external model: **ChoiceMMed MD300C208S**; continuous `2A5F` |
| PM 150 connect — legacy BLE | `ba11f08c-5f14-0b0d-…` | Oxygen saturation, pulse | Experimental; simulated | [2019 research implementation](https://eprints.uklo.edu.mk/id/eprint/2338/1/AIIT2019_ProceedingsFinal_Optimizing%20Bluetooth%20Communication.pdf) | Separate CDxx protocol; use nearby-device search or manual setup; device timestamp/status not decoded |

### Glucose meters

| Model | BLE service | Driver measurements | Validation here | External evidence | Model notes |
| --- | --- | --- | --- | --- | --- |
| MediTouch 2 connect | `1808` | Glucose, timestamp, sequence, meal context | Experimental; simulated | [Protocol reference only](https://github.com/KathrinKollegger/Gluko-Smart-App/blob/master/app/src/main/java/com/example/gluko_smart/GlobalVariable.java) | Source names MediTouch 2; exact connect variant and working measurements unverified |
| MediTouch 2 connect dual | `1808` | Glucose, timestamp, sequence, meal context | Experimental; simulated | — | No separate independent working report found for the dual variant |

Other BU/BW monitors need the same standard BLE measurement format. Lefu scales
and other unconfirmed families require further protocol work. See the
[research report](docs/PROTOCOL_RESEARCH.md) and
[external evidence](docs/TESTING.md#external-evidence-and-remaining-checks).

## Features

- Automatic discovery and manual Bluetooth-address setup; English and German UI.
- Measurements, optional battery/device information, and separate device/reception times.
- Last readings remain available while devices sleep and restore after restart.
- User filters and optional separate sensor groups for blood-pressure monitors and scales.
- Configurable retries, timeout, pairing and blood-pressure clock synchronization.
- Private local history, administrator CSV export, measurement events and diagnostics.

## Requirements

Home Assistant **2026.9.0+** with a managed Bluetooth adapter or proxy. Wake the
device for transfers and close VitaDock+ or other clients connected to it.

<!-- ha-marketplace:exclude-start -->

## Installation

| Method | Steps |
| --- | --- |
| **Marketplace — HA 2026.11+** | Open **Settings → Marketplace → Add from link**, add `https://github.com/michelnet/homeassistant-medisana-ble`, connect GitHub if prompted, and install **Medisana BLE**. |
| **HACS — HA 2026.9–2026.10** | Open **HACS**, search for **Medisana BLE**, and download it. No custom repository is required. |
| **Manual** | Copy [`custom_components/medisana_ble`](custom_components/medisana_ble) to `/config/custom_components/medisana_ble`. |

Restart Home Assistant if prompted, or after HACS/manual installation.
Medisana BLE is included in the [default HACS catalog](https://github.com/hacs/default/pull/14163).
Existing HACS installations migrate with Home Assistant 2026.11; see the
[official migration guide](https://www.home-assistant.io/integrations/marketplace/#coming-from-hacs).

<!-- ha-marketplace:exclude-end -->

## Setup

1. Wake the device and start a measurement or Bluetooth transfer.
2. Open **Settings → Devices & services** and confirm its discovery card. If
   missing, use **Add integration → Medisana BLE → Find a nearby device**, or
   **Enter a Bluetooth address** with its full MAC address and device type.
3. For classic scales, choose the clock origin: **2010 for BS 410/444**, **1970
   for BS 430/440**; verify your firmware's timestamps. Scales wait **12 seconds**
   before connecting so body analysis can finish.

For the legacy **PM 150**, use nearby-device search or manual setup and select
**Pulse oximeter**. It has no generic automatic discovery matcher.

## Options and data

Open the integration's **Configure** menu to adjust connection settings, history
retention and, where supported, user filtering and clock synchronization.

| Option | Default / behavior |
| --- | --- |
| User filter | `-1` = all; raw Bluetooth IDs may differ from the device display. Separate user groups are optional. |
| Scale guests | Raw ID `255` keeps unassigned weight only; no personal group or composition. |
| Connection | 20-second timeout, 2 attempts; pairing off. BSA 45 users can try 3 seconds / 10 attempts; BU 572 may need pairing and clock-before-notifications. |
| Local history | 200 records per device; configurable 0–1000. Zero disables and removes stored history. |
| CSV export | Administrator action **Medisana BLE: Export measurement history**; save the decoded `csv` response as a UTF-8 `.csv` file. |
| Events / diagnostics | Event `medisana_ble_measurement`; **Download diagnostics** on the integration entry. |

**The user filter applies to the main sensors. History, events and separate user
groups include all transmitted users.** Device timestamps depend on the device
clock; Recorder statistics use reception times and are not backfilled.

See the [device reference](docs/REFERENCE.md) for sensor fields, clock setup,
connection timing, export/event details and troubleshooting.

## Confirming a device

Help improve the compatibility table by testing your physical device with this
integration:

1. Set it up with VitaDock+ and other connected BLE clients closed. Compare
   received values and units with the device display; check its timestamp if supplied.
2. Let the device sleep, then repeat a transfer with a new reading. Confirm that
   the sensors and **Last received** update again.
3. Restart Home Assistant. Check that previous values restore and the next
   transfer still works. If applicable, also check user separation and composition.
4. Open a [device confirmation issue](https://github.com/michelnet/homeassistant-medisana-ble/issues/new?title=Device%20confirmation%3A%20MODEL)
   with the exact model/article number, firmware if available, Home Assistant and
   integration versions, Bluetooth adapter/proxy, settings and results.

Report which features worked, failed or were not tested; discovery alone does
not confirm measurement transfer. Use the [report template](docs/TESTING.md#reporting-a-result).
Personal health values, MAC addresses and serial numbers are not needed; review
any diagnostics or logs before attaching them.

## Documentation and development

- [Device reference](docs/REFERENCE.md) — setup details, options and protocol behavior.
- [Testing](docs/TESTING.md) — simulated checks and physical-device validation.
- [Protocol research](docs/PROTOCOL_RESEARCH.md) — sources, Android analysis and remaining gaps.
- [Release 1.2.0](docs/releases/v1.2.0.md) — features, fixes and validation.

Development requires **Python 3.14.2+**; tests use Home Assistant 2026.11.1.

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_test.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

## Attribution

The classic scale driver adapts the MIT-licensed [BS440 project](https://github.com/keptenkurk/BS440);
its full notice ships in [THIRD_PARTY_NOTICES.txt](custom_components/medisana_ble/THIRD_PARTY_NOTICES.txt).
No decompiled app implementation or proprietary body-composition library is bundled.
The icon is from the [official VitaDock+ listing](https://play.google.com/store/apps/details?id=de.medisana.vitadockplus);
VitaDock+ and Medisana are trademarks of Medisana GmbH.
