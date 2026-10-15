# Bench campaign dataset: canonical format (manifest.json + samples.jsonl)

## Purpose

Единый канонический формат стендовой кампании characterization. Это **сырые
данные до обработки**: запись сохраняет ADC raw, CCR, ARR, timestamp —
рассчитанные токи хранятся рядом, но не вместо исходников. Только так можно
позже восстановить ошибку ADC calibration, timing или reconstruction.

```text
campaign/
├── manifest.json     конфигурация и identity кампании (один на кампанию)
└── samples.jsonl     одна полная запись на измерение (JSON per line)
```

Конвертер `tools/map_bench_dataset.py` валидирует кампанию и генерирует
`dataset.txt` для host-конвейера (CLI `map_artifact_pipeline_cli`), который
выполняет Accumulator → Solver → Certifier → Writer и выдаёт
`oew_map_v2.bin` + `oew_map_v2.json`.

## Принципы

1. **Raw-first**: `raw_idc1/raw_idc2/raw_ct/raw_vbus`, `ccr1/2/3`, `arr`,
   `timestamp_cycles` сохраняются в каждом sample. Инженерные токи
   (`idc1_ma` и т.д.) — производные, но тоже пишутся (их выдаёт стенд).
2. **Fail-closed на двух уровнях**:
   - *dataset-уровень* (validator, зеркалит `MapCharacterizationAdapter`):
     `adc_settled==1`, `scope_qualified==1`, `margin_ticks>0`, схема записей,
     полнота 12 строк;
   - *pipeline-уровень* (C-конвейер): duplicate sequence, KCL, solver
     condition, валидность регионов (включая `margin < region.min_margin`).
3. Каждая строка (sector/window) получает ячейки региона **из своих
   сэмплов**: modulation-точка сэмпла (mu/mv/mw) вычисляется из
   `ccr1/2/3` + `arr` формулой `MapMeasurement_CcrToQ15`, margin берётся из
   записи, `status=1` (все сэмплы прошли evidence-гейты). Отдельного
   файла ячеек нет.

## manifest.json

Все числа — JSON integers (десятичные). Ключи соответствуют полям C-структур.

| Ключ | C-поле / смысл |
|---|---|
| `format_version` | формат dataset (сейчас 1) |
| `campaign_id` | строковый идентификатор кампании |
| `board_revision` | `OewMapIdentity.board_revision` |
| `pwm_frequency_hz` | `pwm_frequency_hz` |
| `timer_arr` | `timer_arr` (TIM1 ARR) |
| `adc_trigger_id` | `adc_trigger_id` (trigger revision) |
| `trigger_offset_ticks` | `trigger_offset_ticks` (0 до scope-этапа) |
| `deadtime_ticks` | `deadtime_ticks` |
| `adc_clock_hz` | `adc_clock_hz` (живая ADC-сигнатура) |
| `adc_sample_cycles_x2` | `adc_sample_cycles_x2` |
| `adc_resolution` | `adc_resolution` (0=12-bit) |
| `adc_config_signature` | `adc_config_signature` (CRC живых регистров) |
| `current_calibration_signature` | `current_calibration_signature` |
| `characterization_id` | `OewMapProvenance.characterization_id` |
| `dataset_crc32` | `dataset_crc32` |
| `tool_build_id` | `tool_build_id` |
| `qualification_revision` | `qualification_revision` |
| `solver_revision` | `solver_revision` |
| `certifier_revision` | `certifier_revision` |
| `shunt_resistance_uohm` | опорный тракт квалификации: шунт перед INA240A1, мкОм (30300 = 30,3 мОм); **обязательно, целое > 0** |
| `amplifier_gain` | опорный тракт: усиление INA240A1, В/В (20; rev 1 была заглушкой 1); **обязательно, целое > 0** |
| `calibration_revision` | ревизия описания опорного тракта в манифесте (1 = заглушка, 2 = фактическая цепь INA240A1); **обязательно, целое >= 1** |
| `phase_a`, `phase_b` | wiring шунтов (0=U,1=V,2=W), одинаковы для всех строк |
| `startup.sector/window/hold_cycles/mu/mv/mw` | стартовый контекст (точка внутри startup-региона) |
| `qualifications.accumulator.*` | `MapAccumQualification` |
| `qualifications.solver.*` | `MapSolverQualification`; `min_abs_determinant` — относительный детерминант det/(S00·S11) в ppm (1e-6), 10000 = 1% |
| `qualifications.region.*` | `MapRegionQualification` |

Обязательные поля identity должны быть ненулевыми (board, pwm, arr, trigger,
clk, smp, acs, ccs), provenance — все 6 полей ненулевые.

Поля `shunt_resistance_uohm` / `amplifier_gain` / `calibration_revision` —
документация ОПОРНОГО (внешнего) тракта квалификации кампании; их не читают
ни конвейер, ни прошивка. Привязка артефакта к бортовому ADC-тракту идёт
отдельно, через `current_calibration_signature` (BOAR_CAL_SIG). Несмотря на
документационный характер, поля **обязательны и проверяются валидатором**
(`tools/map_bench_dataset.py`, `REFERENCE_PATH_FIELDS` +
`REFERENCE_PATH_REVISIONS`):

- все три — целые положительные (`> 0`), `bool` не допускается;
- `calibration_revision=1` — устаревшее описание-заглушка: `amplifier_gain`
  обязан быть `1` (не подтверждает физический тракт);
- `calibration_revision=2` — фактическая цепь INA240A1:
  `shunt_resistance_uohm=30300`, `amplifier_gain=20`;
- `calibration_revision>=3` — неизвестная/отдельно описанная цепь: только
  положительность. **Важно**: прохождение валидации для `rev>=3` означает лишь
  корректность формата; физическое соответствие опорному тракту **не
  подтверждается** и требует отдельной документации цепи. Например,
  демо-кампания `campaign_demo` использует rev=3 со своими значениями
  (shunt=5000, gain=50) — они формально валидны, но не квалифицированы как
  физический тракт.

Это не влияет на прошивочный артефакт, но не даёт кампании молча потерять
описание опорной цепи или подать заглушку `gain=1` под ревизией `2`.

## samples.jsonl

Одна JSON-строка на измерение. Поля:

| Ключ | Смысл |
|---|---|
| `seq` | sequence (уникален в строке) |
| `sector`, `window` | строка 0..5 / 0..1 |
| `ccr1`, `ccr2`, `ccr3`, `arr` | PWM-снапшот (модуляционная точка сэмпла) |
| `raw_idc1`, `raw_idc2`, `raw_ct`, `raw_vbus` | **сырые ADC-коды** |
| `idc1_ma`, `idc2_ma`, `ict_ma`, `vbus_mv` | инженерные значения (выдает стенд) |
| `ref_u_ma`, `ref_v_ma`, `ref_w_ma` | scope-референс фазных токов (KCL=0) |
| `margin_ticks`, `blanking_ticks` | timing-свидетельства |
| `adc_settled`, `scope_qualified` | evidence-гейты (1/0) |
| `timestamp_cycles` | метка времени (CYCCNT) |

Требования validator (dataset-уровень): `adc_settled==1`, `scope_qualified==1`,
`margin_ticks>0`, `seq` уникален в строке, sector/window в диапазоне, все 12
строк присутствуют (≥1 сэмпл и ≥1 выводимая ячейка).

## REJECT-матрица (гарантируется end-to-end)

| Порча | Уровень | Стадия/статус |
|---|---|---|
| `adc_settled=0` | validator | REJECT: evidence-гейт |
| `scope_qualified=0` | validator | REJECT: evidence-гейт |
| `margin_ticks=0` | validator | REJECT: evidence-гейт |
| `margin < region.min_margin_ticks` | pipeline | certifier `MAP_CERT_MARGIN_BAD` |
| duplicate `seq` в строке | pipeline | accumulator `MAP_ACCUM_SAMPLE_DUPLICATE` |
| KCL violation (`ref_u+ref_v+ref_w ≠ 0`) | pipeline | accumulator `MAP_ACCUM_KCL_ERROR` |
| сингулярная строка (idc2=idc1) | pipeline | solver `MAP_SOLVER_SINGULAR/CONDITION_BAD` |
| вырожденный регион (все точки в одной ячейке) | pipeline | certifier `MAP_CERT_DEGENERATE` |

Регрессия: `tests/test_map_bench_dataset.py` гоняет каждую строку матрицы
через validator → CLI → проверяет стадию отказа.

## Конвертация

```bash
python tools/map_bench_dataset.py campaign/ dataset.txt      # validate + convert
```

CLI-конвейер затем:

```bash
make -f tools/map_artifact_writer_test.mk map-artifact-cli
tools/map_artifact_pipeline_cli.exe dataset.txt out_dir      # oew_map_v2.bin + .json
```

Демо-кампания: `tools/campaign_demo/` (синтетическая, 12 строк × 8 сэмплов).
