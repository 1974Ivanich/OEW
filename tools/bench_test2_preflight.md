# `bench_test2_preflight.py` — CLI pre-flight validator Test №2

`bench_test2_preflight.py` converts the machine-checkable portions of the physical no-HV Test №2 pre-flight checklist into a fail-closed CLI report. It validates the G0 evidence package, explicit operator attestations, safe UART observations and optional sigrok discovery. It writes `preflight_summary.json` to the campaign folder.

> `PREFLIGHT=PASS` permits only the separate approved no-HV Test №2 automation command. It is **not** physical Test №2 PASS and **not** an approval for Stage A, DC-link, FOC, V/f, autotune or `mapcap build`.

## 1. Safety boundary

The tool has a closed UART allow-list:

```text
sysinfo → p? → pdump → a → c → enc → mapcap status
```

It never sends `mcarm`, `mapcap run`, `mapcap drain`, `mapcap build`, `f`, FOC, V/f, autotune, programming or flash commands. The source and automated regression test assert this constraint.

The default/offline mode opens no COM port, USB device, ST-Link or sigrok. Real UART access is blocked unless all G0 and operator hard gates have already passed. Sigrok `--scan` is blocked in offline mode and until those same gates pass; it is connectivity only, not actual Test №2 capture evidence.

## 2. Required campaign inputs

Place the campaign outside a Git working tree. Before pre-flight, populate it using the G0 template and validator.

| File | Requirement |
|---|---|
| `g0_approval.json` | `decision=APPROVED`, physical no-HV execution scope and every prohibited scope retained. |
| `diagnostic_build_manifest.json` | `physical-nohv-diagnostic`, `MAPCAP_TEST2`, complete approved define map. |
| `diagnostic_build.log` | Original diagnostic build output. |
| `g0_check_summary.json` or `g0/g0_check_summary.json` | Exactly one location; `gate=HIL_TEST2_G0`, `verdict=PASS`. |

The tool adds only its own evidence files to a valid campaign: `preflight_summary.json`, and in real modes `preflight_uart.log` / `preflight_sigrok_scan.log`. It does not modify G0 inputs. A malformed campaign or one inside Git receives `PREFLIGHT=FAIL` and no summary is written into it.

## 3. Offline validation

Use this first on ПК‑3 or a disconnected workstation. It validates the G0 package and records missing operator attestations as failures; an offline run is deliberately **not** a physical GO.

```powershell
py -3 tools\bench_test2_preflight.py `
  --campaign D:\campaign_raw\test2_nohv_<UTC> `
  --offline `
  --confirm-dc-link-disconnected `
  --confirm-pc4-zero `
  --confirm-sd-high `
  --confirm-sigrok-connected
```

Expected result in this mode:

```text
PREFLIGHT=FAIL
```

The failure identifies that real UART identity and actual sigrok connectivity have not been observed. This is intentional: offline evidence never creates physical GO.

An offline JSON transcript may exercise the strict parsers but still cannot create physical GO:

```powershell
py -3 tools\bench_test2_preflight.py `
  --campaign D:\campaign_raw\test2_nohv_<UTC> `
  --offline --uart-transcript D:\evidence\preflight_responses.json `
  --confirm-dc-link-disconnected --confirm-pc4-zero `
  --confirm-sd-high --confirm-sigrok-connected
```

The transcript has the following shape and must contain only the allow-list commands in this exact order:

```json
{
  "responses": {
    "sysinfo": "<response>",
    "p?": "<MOE=0 response>",
    "pdump": "<default_deny=1 response>",
    "a": "<strict ADC response>",
    "c": "<calibration response>",
    "enc": "<err=0 response>",
    "mapcap status": "<strict IDLE status response>"
  }
}
```

## 4. Controlled physical pre-flight

Run this only after the human checklist has confirmed both DC-link rails physically disconnected and `<1 V`, PC4 without external VBUS, SD1/SD2 high, full STEVAL/sensor harness, and an actual sigrok device connected. The port must be the actual MCU UART VCP—not a guessed Windows COM port.

```powershell
py -3 tools\bench_test2_preflight.py `
  --campaign D:\campaign_raw\test2_nohv_<UTC> `
  --port COM<actual_MCU_VCP> `
  --scan-sigrok `
  --confirm-dc-link-disconnected `
  --confirm-pc4-zero `
  --confirm-sd-high `
  --confirm-sigrok-connected
```

The UART conditions are strict:

| Observation | PASS condition |
|---|---|
| `sysinfo` | Non-empty valid response retained in the summary. |
| `p?`, `pdump` | Each contains `MOE=0` or `default_deny=1`. |
| `a` | Strict ADC response; `raw_vbus ≤ 9`, I1/I2 present and not saturated. |
| `c` | Offset calibration present; no `@ADC:CAL:FAIL`. |
| `enc` | `err=0`. Encoder remains infrastructure readiness, not MapCapture acceptance. |
| `mapcap status` | Extended status says `IDLE`, `term=0`, `frames=dropped=avail=0`. |
| `--scan-sigrok` | `sigrok-cli --scan` exits 0 and returns nonempty output. |

## 5. Verdict and evidence

| Result | Exit code | Required action |
|---|---:|---|
| `PREFLIGHT=PASS` | `0` | Attach `preflight_summary.json` to the physical report. A human may now use only the separate approved no-HV automation command. |
| `PREFLIGHT=FAIL` | `2` | Do not flash diagnostic firmware if G0 was not PASS; do not issue `mcarm`/`mapcap run`; preserve the summary and correct the failed gate. |

`preflight_summary.json` includes timestamp, mode, operator confirmations, G0 input SHA-256 values, UART commands/responses, all individual checks and the fixed safety-boundary statement. It records a reproducible readiness decision but does not replace DMM values, manual wiring inspection, scope review, final Test №2 verdict or post-campaign evidence archive.

## References

[1]: `TZ_BENCH_TEST2_PREFLIGHT_CLI.md` — formal contract.

[2]: `tools/bench_test2_g0_check.md` — G0 pre-flash approval and source-to-binary identity.

[3]: `tools/bench_test2_capture.md` — strict physical UART/sigrok execution contract.
