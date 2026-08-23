# ТЗ (черновик): L3-квалификация токовой карты для energise

**Статус:** черновик для обсуждения/приёмки задач.
**Зависимость:** ветка `ai-bench/ct-fix` принята в main (калибровка успешна: offset_i1≈2040, offset_ires=0; FOC пока `rc=-2 map_unverified`).
**Цель:** открыть `control_admitted` (board-qualified current map) безопасно, по L3-конвейеру, чтобы разрешить energise-тесты (`BENCH_FIRST_SESSION.md` §5).

---

## 1. Текущий статус (факт, 2026-08-23)

- Калибровка `c` → SUCCESS (`offset_i1=2040:offset_i2=2068:offset_ires=0`).
- FOC start → `rc=-2` (`map_unverified`): `ADC_FRAME_MAPPING_UNVERIFIED`.
- `control_admitted` (adc.h) — false по design до квалификации карты.
- Fail-closed для I1/I2/VBUS работает; PWM off.

## 2. Что блокирует energise

1. `ADC_SetControlAdmission(false)` — контрольный гейт карты не открыт.
2. Карта токов (2-датчиковая: I1/I2, `iw = −iu−iv` — см. pinout.md) не имеет
   board-qualified профиля: `MapCapture_*`/L3 не выполнены на реальной плате.
3. Политика: «шунт НЕ модулирующего инвертора» — energise достоверен только
   после измеренной карты (доки по autotune/commissioning).

## 3. Предлагаемый объём (этапы)

### Этап 1 — offline-профиль (без HV)
- Собрать commissioning-образ: `make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7"`.
- Зафиксировать board-qualified профиль в `src/map_capture_profiles.c`
  (сейчас fail-closed: без профиля `build/` и `mcarm` заблокированы — подтверждено ранее).
- Прозвонка/проверка физики токовых шунтов I1/I2 (PA0/PA1, ОУ 1.65 В — работает).

### Этап 2 — capture на стенде (HV ограниченно, по отдельному допуску)
- `mapcap build=` / `mapcap run` (диагностический захват) — только с DC-link
  с токоограничением и по процедуре BENCH_FIRST_SESSION §5.
- `chu/chv/chw` (autotune probe каналов) — измерение каналов, сверка с картой.

### Этап 3 — решение/маппинг
- `ADC_SetControlAdmission(true)` после:
  - калибровка валидна,
  - окна `SetExpectedWindow` валидны,
  - карта квалифицирована на реальном железе,
  - решение приёмщика (safety-модуль, только по ТЗ).

## 4. Критерии выхода

- [ ] `control_admitted=1` и FOC start не `rc=-2` (а следующий гейт, если есть).
- [ ] energise на 60 В с токоограничением: токи/напряжения в окне, без latch.
- [ ] `make test` + commissioning PASS в CI.

## 5. Запреты

- Не открывать `control_admitted` вручную/обходом.
- Не менять `protect.c`/`.ioc` без отдельного ТЗ.
- Energise только по полной процедуре BENCH_FIRST_SESSION §5 и с разрешения приёмщика.