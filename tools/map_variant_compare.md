# Offline comparator вариантов карты (TZ-02, шаг b)

Сравнивает `M0-rebased` с вариантами (`M1…M4`) по **каждой метрике отдельно** и выдаёт
`BETTER / SAME / WORSE`. Агрегированного рейтинга карты нет **по построению**.

## Зафиксированные правила (нарушение = дефект инструмента)

1. **Единственный baseline — `M0-rebased`.** `--baseline-crc32` обязан совпасть с `map_crc32`
   baseline-прогона; иначе — REJECT (так старый `oew_map_v2.bin` не может стать точкой отсчёта).
2. **Никакого агрегированного рейтинга.** Ни «overall», ни «score», ни суммы мест.
   В JSON стоит флаг `verdict_absent_by_design: true`, тесты это проверяют.
3. **`idc1/idc2` — только repeatability.** Из них не выводится «физическая истинность M»:
   эти каналы входят в реконструкцию, и метрика из той же `M` дала бы круг.
4. **`Ires` — диагностический zero-sequence канал** (`i_z = (ia+ib+ic)/3`, `|Δi_dq| = 2|i_z|`),
   а не ещё один восстановленный фазный ток. Отдельно помечается ситуация
   **«Id/Iq улучшились, а ZSV вырос»** (`zsv_alerts`).
5. **Метрики без независимого источника → `N/A`** и не участвуют в вердикте
   (`ires_vs_iz` без `--iz`, `kcl_residual` без независимых `iu/iv/iw`).

## Метрики и направление «лучше»

| Метрика | Что считается | BETTER | SAME | WORSE |
|---|---|---|---|---|
| `id_error` / `iq_error` | median \|Id−Id_ref\|, \|Iq−Iq_ref\| | меньше | в tolerance | больше |
| `ripple_id` / `ripple_iq` / `ripple_idq` | p95−p5 по Id/Iq | меньше | в tolerance | больше |
| `idc_repeatability` | p95−p5 raw ADC (idc1/idc2) — **только repeatability** | меньше | в tolerance | больше |
| `zsv_ires` | median \|Ires\| (ZSV-канал) | меньше | в tolerance | больше |
| `ires_vs_iz` | median \|Ires − scale·iz\| (нужен `--iz`, шкала — `--iz-scale`) | меньше | в tolerance | больше |
| `kcl_residual` | median \|iu+iv+iw\| по **независимому** референсу (`--ref`) | меньше | в tolerance | больше |
| `protection_fault_rows`, `break_events`, `mapload_fail` | число событий | меньше | одинаково | новое/большее |
| `uart_trunc`, `uart_drp`, `t_monotonic` | целостность телеметрии | — | без потерь | потеря/немонотонность |
| `map_identity` | `map_id`/`map_crc32` прогона | — | валидна | mismatch/reject |
| `sector/window ↔ CCR` | согласованность внутри прогона (для вердикта — при появлении независимого референса) | — | — | — |

Tolerance задаётся `--tol` (по умолчанию 5 % относительного отклонения от baseline).

## Использование

```bash
python tools/map_variant_compare.py \
    --baseline M0 --baseline-crc32 0x95425CEB \
    --run M0=logs/M0_rebased.log --run M1=logs/M1.log --run M2=logs/M2.log \
    [--iz M1=iz_M1.csv] [--ref M1=phases_M1.csv] [--iz-scale 1.0] \
    [--tol 0.05] [--json report.json] [--markdown report.md]
```

Формат входов: raw-логи сессий (маркеры `[utc] TX/RX`, строки `@FOC:`, `@ADC:`, `@SYS:`,
`@BRK:`, `@MAP:LOAD:`); `--iz` — CSV `t_ms,iz`; `--ref` — CSV `t,iu,iv,iw` (независимый референс).

Выход: таблица в stdout + JSON (`metrics`, `matrix`, `zsv_alerts`, `checks`) + опциональный markdown.

## Тесты

```bash
py -3 -m pytest tests/test_map_variant_compare.py -q      # 12 проверок
```

Покрыто: CRC-гейт baseline (старый артефакт → REJECT), направления и tolerance,
отсутствие агрегата, `idc` только для repeatability, ZSV-предупреждение,
protection/integrity-правила, `N/A` без независимых источников, отсутствие «итоговой» строки.

## Что этот компаратор НЕ делает

Не квалифицирует карту физически и не ищет «истинный ток»: он показывает, как меняется набор
наблюдаемых метрик при смене `M`, чтобы решение о следующем варианте принималось по фактам.
Физическая квалификация — отдельный этап (§7/§7.1 протокола P0/P1).
