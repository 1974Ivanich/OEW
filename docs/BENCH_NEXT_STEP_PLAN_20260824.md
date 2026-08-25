# План следующего шага стенда (ПК-3, 2026-08-24)

> **Актуальная операционная инструкция для ПК-3:** перед любым Stage A выполнить [test №2 MapCapture no-HV](BENCH_PC3_TEST2_NOHV.md) при отключённом DC-link и PC4=0. Заполняемый [шаблон протокола](templates/TEST2_NOHV_PROTOCOL_PC3.md) и критерии GO/NO-GO находятся в этом пакете. Настоящий документ сохраняет план кампании на 2026-08-24; он не отменяет fail-closed test №2 и не даёт самостоятельного допуска к 60 В.



**Текущее состояние (подтверждено на железе ПК-3):**
- main `b291554` (фиксы CT влиты) собран и прошит: `c` → SUCCESS
  (`offset_i1=2039:offset_i2=2068:offset_ires=0`), FOC `rc=-2 map_unverified`,
  PWM off, ENC `err=0` (897 мкс), VBUS raw≈2 (0.48 В).
- Fail-closed для I1/I2/VBUS и admission не ослаблены — energise заблокирован
  до квалификации карты (как и задумано).

**Цель следующего шага:** открыть `control_admitted` легально (board-qualified
current map) по L3-конвейеру → затем energise по BENCH_FIRST_SESSION §5.

---

## Этап 1. Диагностический снимок калибровки (уже PASS, ПК-3)
```text
c    → offset_i1=2039:offset_i2=2068:offset_ires=0   (SUCCESS, Путь B)
1    → @FOC:START:FAIL:rc=-2 (map_unverified)         (ожидаемо)
p?   → @PWM:CR1=224:CCER=0 (PWM off)
enc  → err=0, period=897 мкс
```

## Этап 2. Host-side L3-инструменты (CI/ubuntu; на ПК-3 нет gcc)
В main уже влиты: `map_artifact_pipeline`, `map_characterization_e2e`,
`map_bench_dataset`, `map_artifact_writer`. Оркестрация — в CI:
```bash
# на ubuntu (CI workflow build-test)
make test                 # вкл. map_characterization_e2e_test, map_artifact_pipeline_test
```
Данные кампании — `tools/campaign_demo/` (manifest.json + samples.jsonl).

## Этап 3. На стенде: commissioning-сборка + capture (требует HV/внимание)
```bash
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7"
make flash
```
Затем CLI (только с DC-link 60 В с токоограничением и разрешения приёмщика):
```text
mapcap build=...   # квалификация карты (board-qualified profile)
mapcap run         # диагностический захват
chu / chv / chw    # autotune probe каналов (после energise-допуска)
```
(Сейчас commissioning-сборка fail-closed без профиля — подтверждено ранее.)

## Этап 4. Admission + energise (после приёмки L3-артефактов)
- `ADC_SetControlAdmission(true)` — только после: калибровка валидна, окна
  `SetExpectedWindow` валидны, карта квалифицирована, решение приёмщика.
- Energise-тесты: FOC/V/f/autotune по BENCH_FIRST_SESSION §5 (DC-link 60 В,
  токоограничение, SD-линии high).

## Критерии завершения следующего шага
- [ ] CI зелёный (map_characterization_e2e, artifact_pipeline, bench_dataset).
- [ ] Ветка `ai-bench/mapcap-uart-export` принята (или package merge).
- [ ] На стенде: commissioning-образ прошит, `mapcap`/probe не ловят fault.
- [ ] `control_admitted=1` (по решению приёмщика), FOC start не `rc=-2`.
- [ ] Energise на 60 В: токи/VBUS в окне, без latch, `em_stop=1:1`.

## Запреты
- Не открывать `control_admitted` вручную/обходом.
- Не менять `protect.c`/`.ioc` без отдельного ТЗ.
- Energise только по BENCH_FIRST_SESSION §5 и с разрешения приёмщика.