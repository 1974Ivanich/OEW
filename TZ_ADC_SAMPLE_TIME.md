# ТЗ: ADC — включение длинного sample time (adc_set_sample_time)

**Источник:** стендовые испытания (2026-08-22): ADC VBUS/current read mismatch —
`TZ_ADC_VBUS_MISMATCH.md`; корневая причина найдена приёмщиком в `src/adc.c`.

## Объект

`src/adc.c` — инициализация ADC2/ADC1 (regular + injected), каналы:
I1 (PA0/IN1, ADC1), I2 (PA1/IN2, ADC2), Ires (PA6/IN3, ADC2), VBUS (PC4/IN5, ADC2).

## Проблема (подтверждено)

- `adc_set_sample_time()` (adc.c:137–147, «Use long sampling initially») **определена, но нигде не вызывается**;
- `ADC_Init()` (adc.c:204–205) сбрасывает `SMPR1/SMPR2 = 0` → все каналы читаются с **1.5 cycles** (SMP=000);
- Следствие на стенде: VBUS raw≈2 (высокоомный делитель 1:125 не заряжает S/H), I1/I2 raw≈4095 (недозаряд/наводка), Ires raw≈2048 (случайное усреднение); fault `FAULT_R=18` не сбрасывается (`VBUS < 8000 мВ`).

## Требования

1. **Вызвать `adc_set_sample_time()`** для всех четырёх каналов в `ADC_Init()`:
   `ADC1` + `ADC_CH_SHUNT1`; `ADC2` + `ADC_CH_SHUNT2`, `ADC_CH_CT`, `ADC_CH_VBUS`
   (после/вместо сброса SMPR — итог: SMPR=111 (640.5 cycles) для каналов 1,2,3,5).
   Допускается оставить сброс SMPR, если вызовы идут после него.
2. **Hosted-регрессия**: расширить существующий ADC-тест или добавить
   `adc_sample_time_test.c`: после `ADC_Init()` проверить, что
   `ADC1->SMPR1` и `ADC2->SMPR1` содержат `7 << (ch*3)` для каналов 1,2,3,5.
3. **Не менять**: regular-чтение (`adc_regular_read`), injected JSQR/триггер,
   формулы (adc.h), `protect.c`, `pwm.c`, `.ioc`, fail-closed, wire-протокол.

## Стендовая проверка (после прошивки, пользователь)

1. Порядок взвода: логика (aux 3.3V + 15В) → HV 60В → подождать заряд C-банка (2–5 с).
2. `a=200` → ожидание: `VBUS raw≈595` (60 В через делитель), `I1/I2 raw≈2048` (нулевой ток), `Ires≈2048`.
3. `f` → `FAULT` сброшен, `em_stop1=1:em_stop2=1` (VBUS ≥ 8000 мВ).
4. `@FOC` телеметрия: `VBUS≈60000 мВ`.
5. `FAULT_R=18` при включении HV — штатно (UVLO → SD при заряде C-банка); после фикса — сбрасывается `f`.

## Приёмка

`make` PASS; `make test` ALL PASS (включая новый/расширенный ADC-тест);
commissioning (`OEW_MAP_CAPTURE=1 OEW_MAP_L3=1 REV=7`) PASS; `git diff --check`;
CI ветки success; diff — только `src/adc.c` + тесты (0 строк в прочих safety-модулях).

## Ограничения

- Safety-модуль: только точечное изменение (вызовы `adc_set_sample_time`), без рефакторинга.
- Диагностика — существующими командами (`a=N`, `cli_adc_diag`); временные принты — под `#if 0`, удалить.
- Без изменения порядка инициализации main.c (ADC_Init вызывается до PWM_Init — не трогать).
