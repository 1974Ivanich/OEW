# `bench_test2_rerun_verdict.py` — offline verdict повторного no-HV прогона

## Назначение

Скрипт проверяет сохранённый **physical evidence bundle** controlled no-HV прогона. Он не открывает COM, ST-Link, sigrok, USB или DC-link, не прошивает MCU и не посылает UART-команд.

```text
<campaign>/
├── summary.json
├── uart.log
├── metadata.json
├── sigrok_digital.csv
└── physical_run_attestation.json
```

> `TERMINAL_VERDICT=PASS` означает только, что attested campaign evidence согласован с expected no-HV terminal contract. Он **не** является Test №2/Test №3 PASS, manual sigrok scope acceptance или разрешением на Stage A/DC-link 60 V.

## Порядок evidence и attestation

Сначала existing physical automation создаёт `summary.json`, `uart.log`, `metadata.json` и sigrok CSV. Затем назначенный bench-operator заполняет `physical_run_attestation.json` **после** завершения run, указывая своё имя, UTC observation time, statement и SHA-256 exact evidence files. Нельзя редактировать input files после attestation: их hash mismatch приведёт к FAIL.

Минимальная schema:

```json
{
  "schema": "oew-physical-nohv-attestation-v1",
  "role": "bench-operator",
  "operator": "<name>",
  "observed_at": "<UTC>",
  "statement": "I observed the physical no-HV run and preserved the listed original evidence files.",
  "evidence": {
    "summary_sha256": "<sha256>",
    "uart_log_sha256": "<sha256>",
    "metadata_sha256": "<sha256>",
    "sigrok_csv": {
      "path": "<absolute path within campaign>",
      "sha256": "<sha256>"
    }
  }
}
```

Это chain-of-custody statement, а не криптографическое доказательство личности оператора или подлинности hardware. Он запрещает текстовой synthetic pair самовольно объявить себя `PHYSICAL` лишь отсутствием `@SIM:` markers.

## Запуск

```powershell
py -3 tools\bench_test2_rerun_verdict.py `
  --campaign D:\campaign_raw\test3_nohv_<UTC>
```

| Exit code | Console result | Meaning |
|---:|---|---|
| `0` | `TERMINAL_VERDICT=PASS stage_a_60v=BLOCKED` | Все machine-checkable evidence gates согласованы. |
| `2` | `TERMINAL_VERDICT=FAIL stage_a_60v=BLOCKED` | Missing/changed evidence, missing attestation, synthetic marker или хотя бы один contract gate failed. |

## Независимые checks

| Layer | Required condition |
|---|---|
| Provenance | `summary.execution.mode` и `metadata.execution.mode` равны `PHYSICAL`; profile is approved; attestation binds exact hashes. |
| Original evaluator | Все existing required no-HV checks in summary are present/true, но это не единственное основание PASS. |
| Raw preflight recomputation | Каждая UART `@ADC` sample parsed again; count, VBUS median/max и I1/I2 saturation соответствуют metadata contract. Ordered samples должны совпасть с summary. |
| Terminal history | В log ровно один terminal STATUS; earlier bad fault then later good STATUS is FAIL. |
| Terminal identity | Summary и UART совпадают по **всем** parsed STATUS fields, включая `cap`, `periods`, `sector`, `window`. |
| Sigrok evidence | Summary declaration, actual non-empty campaign-local CSV и exact size binding присутствуют. Manual waveform review всё ещё обязателен. |
| UART sequence/drain | Accepted ARM предшествует RUN; last drain is zero and no record line exists. |
| Simulation | Simulation markers are absent only as supplemental negative evidence; they never establish physical provenance by themselves. |

Expected no-HV terminal is `state=5`, `term=-12`, `detail=7 (VBUS_LOW)`, `adc_status=7 (WINDOW_INVALID)`, low VBUS, zero records and bounded currents. `term=-11`/`ADC_SATURATED`, timeout, records, incomplete evidence, bad→good terminal sequence or summary/UART mismatch are FAIL.

## Result and safety boundary

`rerun_terminal_verdict.json` stores every check plus SHA-256 values of all inputs. Preserve it alongside the original artifacts and conduct manual sigrok review plus the approved archive workflow.

```json
{
  "terminal_verdict": "PASS",
  "stage_a_60v": "BLOCKED"
}
```

No parser result permits Stage A. Requirements for a separate 60 V approval remain in `docs/TEST3_NOHV_TO_STAGE_A_60V_CRITERIA.md`.

## References

[1]: `TZ_BENCH_TEST2_RERUN_VERDICT.md` — formal parser contract.

[2]: `tools/bench_test2_capture.md` — existing strict no-HV UART/evidence contract.

[3]: `docs/TEST2_PC3_FRESH_MAIN_CHECKLIST.md` — ПК-3 procedure and path separation.
