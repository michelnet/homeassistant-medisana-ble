# Medisana BLE protocol research

Reviewed on **2026-11-15**. This report distinguishes protocol evidence from a
physical test of this integration. Only direct BLE measurement transfer is in
scope. It does not establish compatibility for every product carrying the same
model name.

## Results used in the implementation

| Device / family | Evidence | Result in this integration | Remaining validation |
| --- | --- | --- | --- |
| BU 550 connect | [Tanaskoska et al., AIIT 2019, pp. 55–57](https://eprints.uklo.edu.mk/id/eprint/2338/1/AIIT2019_ProceedingsFinal_Optimizing%20Bluetooth%20Communication.pdf) reports direct measurements through `1810`/`2A35` | Covered by the existing standard blood-pressure driver; compatibility table extended | Actual device/firmware test here |
| PM 150 connect, legacy BLE | The same paper describes the proprietary service, multiple notifications, start command and saturation/pulse; VitaDock+ independently confirms offsets and initialization | New experimental CDxx path, separate from SIG PLX | Real GATT capture and repeated transfers here; undocumented vendor fields/status remain undecoded |
| BU 572 connect | [jagula's tested integration](https://github.com/jagula/home-assistant-medisana-bu572#getestet) uses pairing and a time write before measurement subscription | Optional pairing and clock-before-notifications settings | Actual adapter/backend pairing and device behavior |
| BS 550 | [weegschaal issue 22](https://github.com/bwynants/weegschaal/issues/22) contains a working report and guest ID 255 | Guest weight retained with no assigned user; composition placeholders ignored | Device/firmware test here |
| BSA 45 | [BS440 issue 103](https://github.com/keptenkurk/BS440/issues/103) reports success with BS 444 settings, three-second timeout and more attempts | Configurable timeout and attempts; setting 3 seconds / 10 attempts reproduces those choices | Conditional report; clock and repeated transfers here |
| BSA 41 | [openScale issue 851](https://github.com/oliexdev/openScale/issues/851) reports operation with its BS 44x handler | Existing classic profile remains available | No independently captured packets were found; model remains a candidate |
| MediTouch 2 | AIIT 2019 reports a BLE device using the standard glucose endpoints; [Gluko-Smart](https://github.com/KathrinKollegger/Gluko-Smart-App/blob/master/app/src/main/java/com/example/gluko_smart/GlobalVariable.java) names that family | Existing standard glucose implementation | Exact connect/dual variants and successful transfers here remain unverified |

The blood-pressure and classic scale changes also retain older downloaded records
and records for other users in a bounded local history. Separate user entities do
not depend on the main sensor's global newest timestamp.

## Legacy PM 150 facts

The legacy service starts with `ba11f08c-5f14-0b0d-`; its remaining UUID contains a
device-specific identifier and must not be published in diagnostics. The five
characteristics use the Bluetooth base UUID:

| Endpoint | Role implemented |
| --- | --- |
| `CD01`, `CD02`, `CD03` | Sequentially enable control notifications; contents are not interpreted as measurements |
| `CD04` | Measurement notification; zero-based byte 3 is saturation and byte 4 is pulse |
| `CD20` | Write `AA 55 04 B1 00 00 B5` after notification subscriptions complete |

The paper has ambiguous endpoint wording for the write. VitaDock+'s Java bridge
explicitly writes to `CD20`; its native command selection confirms the seven-byte
command for device type `OXYMETER`. The native measurement constructor reads
unsigned bytes at offsets 3 and 4. No app implementation was copied into this
project. The independent decoder accepts 5–20-byte GATT values, leaves unverified
vendor fields uninterpreted and publishes no invented device timestamp or quality
flags. A full service/characteristic check gates the start command.

The service prefix cannot be represented by Home Assistant's exact service-UUID
manifest matcher. Nearby-device selection can classify the variable UUID, and
manual address setup is supported. This path does not introduce a broad Bluetooth
matcher that would discover arbitrary nearby devices. Advertised names containing
`Sugar` are excluded from legacy-oximeter classification, matching the app's
explicit distinction for a different device family sharing this UUID prefix.

## Android analysis provenance

Static analysis used **VitaDock+ 2.9.42**, Android package
`de.medisana.vitadockplus`, version code **188**, retrieved on **2026-11-15**.
The installation bundle came from the
[APKPure download page](https://apkpure.net/vitadock-for-connect-devices/de.medisana.vitadockplus/download),
a third-party mirror. It was not downloaded directly from Google Play and was
not installed or run. The
[official Play listing](https://play.google.com/store/apps/details?id=de.medisana.vitadockplus)
identifies the application.

- XAPK SHA-256: `a606f8472ab9d040a5eb890ccbc9b164c790fa6575d39fc029bbaf37924de404`
- Base APK SHA-256: `368dfa744e4deafd7f81d4bfa8f63a8a4c76f140e13c5c1265dc37c6bff3f22f`
- ARM64 APK SHA-256: `06649adc2ef546eb1f6de4c0dc98542222f39dd662a97e0b423d302ef2cad46c`
- Base/ARM64 APK signatures: verified v2/v3 with Android's `apksigner`.
- Signing certificate SHA-256: `5822511878b0d3fef8340bedbf9de5d4b24750aa31c960b7f2fb04d61217fde7`
- Certificate subject: `O=Medisana, CN=Marc Ernst`.

[JADX 1.5.6](https://github.com/skylot/jadx/releases/tag/v1.5.6) was used for
selected BLE Java classes. [Cpp2IL pre-release 21](https://github.com/SamboyCoding/Cpp2IL/releases/tag/2022.1.0-pre-release.21)
parsed native IL2CPP metadata version 39 / Unity 6000.4.0b11. Targeted ARM64
inspection checked command selection and measurement offsets. Native algorithms,
SDKs, credentials and unrelated application behavior are not incorporated.
The installation files, decompiled classes and disassembly stay outside this
repository in the temporary research directory.

## Additional devices investigated

| Family | Findings | Why no new driver is claimed |
| --- | --- | --- |
| ViFit connect / MX3, ViFit Touch | App paths use `FFF0` with `FFF6`/`FFF7`, multi-step commands and per-day data. [Medisana's setup guide](https://medisana.zendesk.com/hc/en-us/articles/204084792-First-linking-via-Bluetooth-with-VitaDock-App) confirms BLE pairing for MX3 | Complete read-only handshake, packet/time semantics and real captures still need validation; the app also contains history-deletion commands, which were not adopted |
| Transtek tracker family | App path uses `7801`, `8A11`, `8A81`/`8A82` | Exact retail-model mapping and complete framing are not established |
| ViFit Run | [Official manual](https://cloud.vitadock.com/doc/download/79492_ViFit_Run_24lgs_WEB_V1_3_2021_10_13.pdf) confirms BLE; the app uses a separate multi-command flow | Device-specific command/response sequencing still needs validation; it cannot use the standard blood-pressure or scale parser |
| BP Watch | Separate app driver and native watch-data conversion | Retail-model mapping, framing and clock behavior remain unverified |
| BS 850 / Lefu 8-point family | Native app exposes `FFF0`, write `FFF1`, additional `FFF2` and notify `FFF4`; live `CF`, plaintext `BE` and encrypted `DF` paths. Body composition calls a native SDK; its failure codes include an invalid license key | No verified device capture or complete independent implementation here. Characteristic UUIDs alone do not establish final weight, user and unit semantics. The SDK/body-composition algorithm is not copied |
| Yolanda WLAN setup | App path shares advertisement `78B2`, name starting `000000`, and configuration endpoints `FFF0`/`FFF1`/`FFF2` | This path configures WLAN and does not demonstrate direct BLE measurement transfer; excluded from project scope |
| Other current BS models and BUA 52 | Product naming or an app's device list alone does not establish packet compatibility | Remain unconfirmed until model-specific direct BLE measurements are established |

The BSA 41 report is useful family evidence but contains no raw capture. Likewise,
[openScale issue 1555](https://github.com/oliexdev/openScale/issues/1555) contains
suggested impedance decoding and an eventual working report, but it does not
establish a verified packet definition suitable for adopting those field offsets.
No estimated body-composition formula is substituted for a manufacturer's result.

## Source handling

The integration remains MIT licensed. Its existing classic scale adaptation
preserves the original MIT notice in
[THIRD_PARTY_NOTICES.txt](../custom_components/medisana_ble/THIRD_PARTY_NOTICES.txt).
The new PM 150 decoder and connection/history features are independently written.
GPL, noncommercial, unlicensed and proprietary implementations were consulted for
protocol evidence only; none of their implementation code or native libraries is
bundled. This report records provenance and implementation choices, not a blanket
legal assurance about every possible use of a device or application.
