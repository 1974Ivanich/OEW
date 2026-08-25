# `bench_test2_g0_check.py` — offline validator Hard Gate G0

`bench_test2_g0_check.py` validates the **offline evidence package** which must exist before any physical no-HV Test №2 diagnostic image is flashed. It is deliberately fail-closed: a missing field, mismatch, malformed JSON, missing compiler token, artifact hash mismatch, or path outside the campaign returns `HARD_GATE_G0=FAIL` and exit code `2`.

> The validator never opens a COM port, accesses ST-Link, flashes firmware, calls `sigrok-cli`, or controls STEVAL/DC-link. It may be run on a disconnected workstation. A validator PASS proves internal consistency of supplied evidence only; it does not cryptographically authenticate a human approver or prove what image is already on a physical target.

## 1. Gate position and no-HV boundary

G0 is a **pre-flash** control. A diagnostic binary may be built and checked offline. However, when G0 does not pass, the diagnostic image must **not** be flashed to real hardware and `mcarm`/`mapcap run` must not be used. The script records the pre-flash decision; it does not intercept `make flash`. Integrating an enforced Makefile/flash block would change shared tooling and requires a separate approved package.

```text
source SHA + green CI
        │
        ▼
offline diagnostic build ──► G0 evidence validator
                                 │
                       FAIL ────┴──── PASS
                        │                 │
     no physical flash/run       human protocol permits diagnostic flash
                                          │
                                          ▼
                  flash verification → sysinfo → continuous UART log
                                          │
                                          ▼
                              physical no-HV preflight
```

Even `HARD_GATE_G0=PASS` does not permit DC-link, Stage A, `control_admitted`, FOC, V/f, autotune or `mapcap build`.

## 2. Required campaign inputs

Create a dedicated folder outside Git, for example `campaign_raw/test2_nohv_20260825T120000Z/`. G0 requires the following four inputs in its root.

| File | Required content |
|---|---|
| `g0_approval.json` | Safety-owner approval bound to source SHA, firmware SHA-256 and **physical no-HV execution** scope. |
| `diagnostic_build_manifest.json` | Complete diagnostic build manifest: source SHA, full defines map, target/test, binary path and firmware SHA-256. |
| `diagnostic_build.log` | Original build output containing each exact compiler token `-DNAME=VALUE`. |
| Binary referenced by the manifest | Relative path below campaign root; its SHA-256 must equal both manifest and approval. |

The tool writes `g0_check_summary.json` to the same folder. It will not overwrite any required input name. Artifact paths in the manifest must be relative to the campaign root; absolute paths and `..` traversal are rejected.

## 3. Approval schema

The approval is a human-process record expressed in JSON so it can be checked deterministically. Its approval ID, approver and time are required but are **not** cryptographic signatures; the physical protocol retains the original approval record and human sign-off.

```json
{
  "schema": "h1-g0-approval-v1",
  "gate": "HIL_TEST2_G0",
  "decision": "APPROVED",
  "role": "safety-owner",
  "approval_id": "HIL-G0-YYYYMMDD-001",
  "approver": "<safety-owner name>",
  "approved_at": "YYYY-MM-DDTHH:MM:SSZ",
  "source_sha": "<40 lowercase hex git SHA>",
  "firmware_sha256": "<64 lowercase hex SHA-256>",
  "approved_extra_defines": [],
  "scope": {
    "target": "physical-nohv-diagnostic",
    "test": "MAPCAP_TEST2",
    "allows_oew_host_test": true,
    "allows_physical_nohv_execution": true,
    "diagnostic_only": true,
    "forbids_stage_a": true,
    "forbids_production": true,
    "forbids_dc_link": true,
    "forbids_foc": true,
    "forbids_vf": true,
    "forbids_autotune": true
  }
}
```

A build-only, host-only, rejected, expired, incomplete, or scope-widened approval fails. In particular, `allows_physical_nohv_execution=true` is mandatory: permission to compile a host-test guarded image is not automatically permission to flash or execute it on real hardware.

## 4. Diagnostic build manifest schema

`defines_complete=true` means the listed `defines` object records all active preprocessor defines pertinent to the controlled build. The validator requires exact values for the five required diagnostic defines and rejects direct Stage-A/admission/energise defines. Any other define must appear by name in `approved_extra_defines` of the approval.

```json
{
  "schema": "h1-g0-diagnostic-manifest-v1",
  "gate": "HIL_TEST2_G0",
  "target": "physical-nohv-diagnostic",
  "test": "MAPCAP_TEST2",
  "source_sha": "<same 40 lowercase hex SHA>",
  "defines_complete": true,
  "defines": {
    "OEW_MAP_CAPTURE": "1",
    "OEW_MAP_L3": "1",
    "PWM_OEW_BOARD_REVISION": "7",
    "OEW_MAP_SYNTHETIC_PROFILE": "1",
    "OEW_HOST_TEST": "1",
    "OEW_HS1_COMMISSIONING_RELEASE": "1"
  },
  "firmware": {
    "path": "firmware-diagnostic.bin",
    "sha256": "<same 64 lowercase hex SHA-256>"
  }
}
```

The required defines are purposeful: `OEW_HOST_TEST=1` together with `OEW_MAP_SYNTHETIC_PROFILE=1` crosses the compile-time guard for `SYNT`. `OEW_HS1_COMMISSIONING_RELEASE=1` enables the *real* hardware-interlock check in `PWM_HardwareInterlockHealthy()` (SD lines high + break configured + no pending BIF), which `MapCapture_Arm` requires; it does **not** open DC-link, control admission, FOC, V/f or autotune. This is why both human approval and source-to-binary binding are required.

## 5. Run the validator

Run the tool only after the approval, manifest, log and binary have been copied into the campaign folder.

```powershell
py -3 tools\bench_test2_g0_check.py `
  --campaign campaign_raw\test2_nohv_20260825T120000Z
```

A nonzero exit is a hard fail:

| CLI result | Meaning | Required action |
|---|---|---|
| `HARD_GATE_G0=PASS`, exit `0` | Offline evidence is internally consistent. | Attach `g0_check_summary.json` to the physical protocol; obtain/confirm human G0 sign-off before flash. |
| `HARD_GATE_G0=FAIL`, exit `2` | At least one contract check failed. | Do not flash diagnostic firmware or run physical Test №2. Preserve and correct offline evidence in a new/revised campaign. |

`g0_check_summary.json` includes every check as `id`, `result`, `expected`, `actual` and `detail`. The summary does not hide partial success: one failed check makes the whole gate FAIL.

## 6. Required identity chain after G0

G0 validates `source SHA → manifest → binary SHA-256`. The physical report must subsequently preserve a separate chain:

| Link | Required evidence | Checked by this tool |
|---|---|---|
| Source → build manifest | Matching 40-hex source SHA | Yes |
| Build manifest → firmware bytes | Matching 64-hex SHA-256 | Yes |
| Firmware bytes → flashing event | Programmer verification log and retained binary hash | No; physical protocol |
| Flashed target → runtime identity | `sysinfo` and continuous UART log tied to campaign | No; physical protocol |

This split is intentional. The offline validator cannot read a target MCU and must not create the false claim that it can.

## 7. Example build-log requirement

The build log must retain the exact compiler-style tokens below. It can contain additional ordinary build output.

```text
-DOEW_MAP_CAPTURE=1
-DOEW_MAP_L3=1
-DPWM_OEW_BOARD_REVISION=7
-DOEW_MAP_SYNTHETIC_PROFILE=1
-DOEW_HOST_TEST=1
```

A log that merely says “diagnostic build PASS” without these tokens is insufficient evidence.

## References

[1]: `TZ_BENCH_TEST2_G0_CHECK.md` — formal validator requirements.

[2]: `src/map_capture_profiles.c` — compile-time guard for the synthetic profile.

[3]: `tools/bench_test2_capture.md` — strict physical Test №2 acceptance and no-HV boundary.
