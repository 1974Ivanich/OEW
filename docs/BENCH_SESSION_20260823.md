# Стендовая сессия 2026-08-23 — итоговый отчёт (no-HV)

**ПК-1 / COM4 / NUCLEO-G474RE + STEVAL-IPM20B×2 / ветка `ai-bench/ct-fix`**
**All tests PASS. Energise НЕ производился.**

## 1. Применённое решение ТЗ (TZ_CT_CHANNEL_CALIBRATION_FIX.md)
- Cherry-pick `e73b8f0` (retain shunt offsets when Ires unqualified) → `22a3ee8`.
- Честный FAIL в CLI `c` (rc≠0 → `@ADC:CAL:FAIL`) → `2c0cc09`.
- Результата на стенде:
  - `c` → `@ADC:CAL:FAIL:rc=-1 (Ires unqualified)` (было: маскированный `offset_i1=0`).
  - `a?` → `offset_i1=2039` (сохранён).
  - Фантомные ~26 А устранены: `@FOC:I1=12..38 мА, I2=0 мА` (было 26081/26439).

## 2. Результаты тестов (no-HV, все PASS)

| Блок | Результат |
|---|---|
| Сборка `make` | Build complete (67876 B text) |
| Прошивка `make flash` | Download verified + MCU Reset |
| Stale-flash probe (`sysinfo`, `p?`) | CLK=170 МГц, PWM off |
| ADC raw | I1≈2039-2041, I2≈2063-2074 (шум ±8), **VBUS raw≈595 → 59935 мВ (60 В)** |
| ADC регистры (`dumpa`) | JSQR=0x00A18482 (каналы 2/3/5), CR ADEN=1, DR=598 — **АЦП работает** |
| ADC stability (8 сэмплов) | I1 spread 4, I2 spread 8 — PASS |
| Калибровка `c` | Честный FAIL `rc=-1` (Ires raw=0) — PASS (сознаёт статус) |
| Энкодер | err=0, period 896-898 мкс, полный оборот (angle 25..16383), оба знака speed |
| Fault/interlock | FAULT=0, `f`→STATUS=1 (not latched), FOC start → `rc=-2` (map unverified), PWM off |
| Soak 60 c | Счётчики ADC стабильны (OVR=0/JEOS=0/TO=0/JQOVF=0), PWM off, ENC err=0 |
| CLI robustness (11 кейсов) | unknown/line overflow, CLI жив |
| UART stress (50 команд) | 0 потерь, конфиг-команды применяются без energise |
| Конфиг-команды | dt/pp/vfk/s/i/vdc — применяются, PWM остаётся off |
| Финальная проверка (8/8) | Все assertions PASS (дефолты dt=1500/pp=6) |

## 3. Диагноз CT-канала (PA6 / ADC2_IN3)
- `raw_ct=0` стабильно (нижнее насыщение, `ADC_RAW_SAT_LOW`).
- `JSQR` содержит канал 3 — последовательность правильная; `GPIOA pin6` — analog-режим (`gpio_set_analog(GPIOA,6)` в `PWM_BoardPins_Init`); ADC2 включён (ADEN=1); VBUS-канал (PC4) читается ⇒ **прошивка/АЦП работоспособны**.
- **Корневая причина физическая**: сигнал не доходит до PA6 (обрыв/перемычка/разъём). По отчёту ai-koda (TZ_ADC_VBUS_MISMATCH_REPORT.md): проверить SB33 (PC4/CN10-34, default OFF — для VBUS уже работает), SB21 (PA0), прозвонку ARD_D12/CN7 → PA6.
- **Fail-closed работает**: Ires не квалифицирован → `offsets_valid=0` → FOC `rc=-2 map_unverified`; energise недоступен (и не разрешён).

## 4. Что дальше (energise)
1. Физическая диагностика: прозвонка PA6 (CN7/ARD_D12 → вывод MCU), проверка SB33/SB21 на обратной стороне Nucleo.
2. После: калибровка `c` → Ires≈2040, `f`→STATUS=0, energise по BENCH_FIRST_SESSION §5 (только с DC-link 60 В и токоограничением).
3. Приёмка ветки `ai-bench/ct-fix` в main (CI на ubuntu; локальный `make test` недоступен — нет gcc).