# Acceptance record: ACS712 no-HV checkout (ПК-3, 2026-09-04)

## Campaign identity

- **Campaign ID:** `acs712_nohv_20260904T040619Z`
- **Source PC:** ПК-3 (operator: Андрей Изместьев)
- **Receiving PC:** ПК-2
- **Accepted zip:** `C:\campaign_raw\accepted\acs712_nohv_20260904T040619Z.zip`
- **Original zip:** `C:\Users\MyHome\Downloads\acs712_nohv_20260904T040619Z.zip`
- **Zip SHA-256:** `96095265702B07C70F5786ED118D301B6F6D6528BBF73B5BA9CDFA17EB90B705`
- **Files in zip:** 11 (validation summary, calibration JSON, DMM worksheet, identity logs, observations, scope CSV, summary)

## Verification performed on ПК-2

```powershell
python scripts\verify_acs712_zip.py
```

Result:

```text
zip_sha256=96095265702b07c70f5786ed118d301b6f6d6528bbf73b5ba9cdfa17eb90b705
zip_sha256 OK
extracted files: 11
verdict=PASS
```

- Validator schema: `acs712-nohv-check-v1`
- Validator checks: **133/133 PASS**
- Exit code: `0`

## Key evidence

| Item | Value |
|---|---|
| Firmware source | `origin/main` @ `6a91312` |
| Build type | default-deny production image (`make clean && make`) |
| DC-link | physically disconnected (< 1 V) |
| Vcc (sensor supply) | 5020 mV |
| v0_U | 2680 mV |
| v0_V | 2661 mV |
| v0 tolerance | within Vcc/2 ± 200 mV |
| Noise U / V | 100 mV pp / 90 mV pp (within ≲110 mV pp guideline) |
| Sensitivity | 100.0 mV/A (both phases) |
| `mapcap`/`mcarm`/FOC/Vf/autotune commands | absent in default-deny image (expected, reality-check documented) |

## Calibration artifact

`calibration/acs712_calibration.json` inside the zip is accepted and ready for future BOAR grid campaign ingest via:

```powershell
python tools\map_scope_ingest.py --calib <path_to_acs712_calibration.json> ...
```

## Scope of acceptance

- **This package authorizes only:** de-energized/no-HV ACS712 wiring verification and zero-current offset calibration.
- **This package does NOT authorize:** DC-link energization, Test №3 MapCapture execution, Stage A 60 V, FOC/Vf/autotune, or any motor-energizing work. Those require separate G0 approval, written procedure, two-person checklist, and LOTO.

## Acceptance decision

- **Status:** ACCEPTED for downstream offline ingest and as input to future approved grid-campaign evidence.
- **Accepted by:** PC-2 agent (Hermes) on 2026-09-04.
- **Operator signature:** Андрей Изместьев (reported by ПК-3).

## Locations

- Accepted zip archive: `C:\campaign_raw\accepted\acs712_nohv_20260904T040619Z.zip`
- Runbook / validator: `docs/templates/acs712_nohv_checkout/` and `tools/acs712_nohv_validator.py`
