# map_scope_ingest.py — merge scope evidence into the BOAR campaign

Склеивает scope-слой стендовой сессии (осциллограф) с UART-логами захвата в
канонический формат кампании (`manifest.json` + `samples.jsonl`), который
принимает `tools/map_bench_dataset.py` и host-конвейер
(`map_artifact_pipeline_cli`).

## Зачем

REC-записи `mapcap drain` содержат токи шунтов прошивки (idc1/idc2, vbus), но
**не содержат независимого фазного референса** (`ref_u/v/w_ma`) и
подтверждения апертуры (`scope_qualified`). Валидатор кампании fail-closed их
требует — без scope-слоя оценка матрицы M (и карта) невозможна. Этот инструмент
сливает данные осциллографа с записями захвата.

## Формат scope evidence

На каждый регион r (0..11) — `scope_region_<r>.csv`, 16 строк (по строке на
импульс бёрста). Строка k CSV ↔ k-я запись `@MC:REC` того же региона
(порядок drain = хронология бёрста). Шаблоны — в пакете
`C:\campaign_raw\map_scope_pkg_boar_20260902\`.

```csv
pulse,ref_u_ma,ref_v_ma,ref_w_ma,margin_ticks,blanking_ticks,scope_qualified,note
1,123,-45,-78,110,15,1,...
```

- `ref_u/v/w_ma` — фазные токи в точке выборки ADC, измеренные осциллографом
  (обязательны; пусто = REJECT).
- `scope_qualified` — 1 только если апертура подтверждена на приборе
  (0 = честный отказ).
- `margin_ticks` — измеренный запас до переключений (пусто = 110 из
  VfcApertureContract; < 110 = REJECT).
- `blanking_ticks` — информационный (пусто = 15, окно выборки ADC).

## Запуск

```bash
py -3 tools/map_scope_ingest.py --logs DIR --scope DIR --out DIR [--pipeline]
```

- `--logs` — каталог с `region_0.log`..`region_11.log` (вывод UART сессии);
- `--scope` — каталог со `scope_region_0.csv`..`scope_region_11.csv`;
- `--out` — выходной каталог кампании;
- `--pipeline` — дополнительно конвертировать в `dataset.txt` и прогнать
  host-конвейер (`map_artifact_pipeline_cli`, собирается через
  `make -f tools/map_artifact_writer_test.mk map-artifact-cli`).

Exit 0 = кампания записана и прошла официальный валидатор; 1 = REJECT.

## Что заполняется

| Поле | Источник |
|---|---|
| `ref_u/v/w_ma` | scope CSV (реальное измерение) |
| `scope_qualified` | scope CSV (должно быть 1) |
| `adc_settled` | 1 — только при firmware-доказательстве: status=7 (WINDOW_INVALID, штатный service capture), fault=0, raw_i1/raw_i2/raw_vbus в 1..4094 |
| `margin_ticks` | CSV или 110 (контракт) |
| `blanking_ticks` | CSV или 15 (окно выборки ADC, информационно) |
| `timestamp_cycles` | seq*1000 (информационно, в REC нет аппаратного timestamp) |
| `characterization_id` | 0x424F4152 (base BOAR) |
| `dataset_crc32` | CRC32 канонического payload сэмплов |
| `tool_build_id` | 0x20260902 (сборка инструмента) |

## Fail-closed

Инструмент не синтезирует scope-данные. REJECT (до записи вывода) при любом из:

- отсутствует/неполон лог региона (нет `@MC:DRAIN`, REC ≠ 16);
- CCR записи ≠ вектору BOAR сектора (лог не того региона);
- отсутствует scope CSV региона / в нём не 16 строк;
- `ref_u/v/w` не заполнены, `scope_qualified != 1`, `margin < 110`;
- |ref| > 10000 мА (предел профиля);
- KCL |u+v+w| > 100 мА (рассинхрон/насыщение);
- собранная кампания не проходит `map_bench_dataset.validate_campaign`.

## Ограничение (проверено 02.09.2026)

Один вектор на строку (кампания 01.09) не проходит полный пайплайн даже со
scope-слоем: solver `MAP_SOLVER_SINGULAR` (возбуждение (idc1,idc2) ~ rank-1,
гейт det/1e12 ≥ 1) и certifier `MAP_CERT_DEGENERATE` (все ячейки строки в
одной точке). Для полной карты нужен grid-свейп на строку —
`TZ_MAP_GRID_PROFILE.md` (профиль BOAR v2). Инструмент работает и для grid
(таблица `expected_ccr` расширяется на точки).
