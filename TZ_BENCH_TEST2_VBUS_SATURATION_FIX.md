# ТЗ: VBUS-канал не должен считаться «насыщенным» при нижнем рельсе (no-HV)

Статус: черновик пакета `ai4/bench-test2-vbus-saturation-fix`

## 1. Проблема

Физический Test №2 (25.08.2026): терминальный fault **интермиттентный** —
run2/7 → `term=-12, detail=7 (VBUS_LOW)` PASS; run4/5/6 → `term=-11, detail=2
(ADC_STATUS_INVALID), adc_status=8 (ADC_SATURATED)` FAIL (~50%).

Корень (найден в коде + подтверждён данными): `adc_frame_status()`
(`src/adc.c:354-367`) применяет **биполярную** проверку
`adc_bipolar_sample_is_usable(raw)` (raw ∈ (1, 4094)) к каналу **VBUS**.

Канал VBUS — **униполярный делитель** (PC4, 1:125): при no-HV (шина 0 В)
легитимное значение raw = 0..4 (DMM-доказано 0 В; распределение: ~30% сэмплов 0).
`adc_bipolar_sample_is_usable(0) = false` → кадр помечается `ADC_FRAME_ADC_SATURATED`
→ map_capture: `frame_capture_fault_detail` → `ADC_STATUS_INVALID` → term=-11 → FAIL.

Токовые каналы I1/I2 — биполярные ОУ (смещение ~2048), для них нижний рельс = реальное
насыщение. CT (PA6, zero-level) уже обрабатывается через `adc_ct_sample_is_usable`
(только верхний рельс). VBUS по природе ближе к CT: 0 В — штатное no-HV состояние.

## 2. Цель

VBUS в `adc_frame_status` проверять **униполярно** (как CT): насыщение = только
**верхний рельс** (raw >= 4094). Нижний рельс (raw 0..1) — валидное no-HV состояние,
не SATURATED. Для energised-режима защита от просадки VBUS сохраняется отдельным
fail-closed путём: `PROTECT_CheckFrame` (vbus_mv < PROTECT_VBUS_MIN_MV → undervoltage).

## 3. Безопасность

- Верхний рельс VBUS (raw >= 4094 ≈ шина > ~330 В) по-прежнему = SATURATED.
- Нижний рельс (0 В) в energised-контексте ловится PROTECT (undervoltage), а не
  статусом кадра — ослабления защиты нет.
- I1/I2/CT проверки не меняются.
- MapCapture service-путь: кадр с raw_vbus=0 доходит до `ADC_FRAME_WINDOW_INVALID`
  (expected_window_valid=false), терминальный verdict — VBUS_LOW, как задумано.

## 4. Объём изменений

| Файл | Изменение |
|---|---|
| `src/adc.c` | добавить `adc_vbus_sample_is_usable(raw)` = `raw < ADC_RAW_SAT_HIGH` с комментарием; в `adc_frame_status` заменить `!adc_bipolar_sample_is_usable(rawvbus)` на `!adc_vbus_sample_is_usable(rawvbus)` |
| `tests/adc_frame_host_test.c` | добавить: raw_vbus=0 (no-HV) → НЕ SATURATED; raw_vbus=4095 → SATURATED; обновить комментарий |
| `docs/AGENTS_STATUS.md` | строка занятости |
| `TZ_BENCH_TEST2_VBUS_SATURATION_FIX.md` | этот документ |

## 5. Проверки

- `make test` — hosted тесты (adc_frame_host_test, adc_sample_time_test и др.) ALL PASS;
- `make` — production build PASS;
- `git diff origin/main...HEAD --check` — чисто;
- физический повтор Test №2 (после приёмки) — терминальный fault должен стабильно быть
  VBUS_LOW (без интермиттентных ADC_SATURATED).

## 6. Запреты

- Не менять проверки I1/I2/CT и `PROTECT_CheckFrame`;
- Не ослаблять верхнерельсовую защиту VBUS;
- Не «попутный рефакторинг».
