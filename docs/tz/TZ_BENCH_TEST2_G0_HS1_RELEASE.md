# ТЗ: G0-контракт — разрешить OEW_HS1_COMMISSIONING_RELEASE=1 в diagnostic-образе

Статус: черновик пакета `ai4/bench-test2-g0-hs1-release`

## 1. Проблема

Физический Test №2 (no-HV MapCapture) блокируется на стадии ARM:
`mcarm` → `rc=-1 = MAP_CAPTURE_HW_INTERLOCK_MISSING`.

Причина (проверено на железе, diagnostic-образ main@33960bd):
- `MapCapture_Arm` требует `hardware_interlock_healthy()` (`src/map_capture.c:157`);
- `PWM_HardwareInterlockHealthy()` (`src/pwm.c:152`) в сборке **без**
  `OEW_HS1_COMMISSIONING_RELEASE` **всегда возвращает false** (намеренный
  fail-closed «release gate»);
- G0-контракт (`tools/bench_test2_g0_check.py`) **запрещал**
  `OEW_HS1_COMMISSIONING_RELEASE` (в `FORBIDDEN_DEFINES`) и не включал его в
  required defines → одобренный diagnostic-образ физически не может выполнить
  `mcarm`.

Ранняя стендовая сессия «Шаг №1: mcarm/run … 7/7 PASS» использовала
**commissioning-образ** (с этим define) — поэтому на симуляции (захардкоженные
ответы) и на G0-валидаторе расхождение не проявлялось.

## 2. Цель

Разрешить `OEW_HS1_COMMISSIONING_RELEASE=1` в diagnostic-образе Test №2:
- исключить define из `FORBIDDEN_DEFINES`;
- добавить в `REQUIRED_DEFINES` (шестой required define).

Безопасность: define **не** открывает DC-link, control admission, FOC, V/f или
autotune. Он только включает **реальную** проверку hardware interlock
(`PWM_HardwareInterlockHealthy()`: SD high + break сконфигурирован + нет BIF)
вместо константного `false`. `PWM_Enable`/`PWM_ServiceCaptureStart` по-прежнему
требуют контекст/admission и не вызываются до явной команды в рамках одобренного
профиля SYNT.

## 3. Контракт (после изменения)

`REQUIRED_DEFINES` (6):
`OEW_MAP_CAPTURE=1, OEW_MAP_L3=1, PWM_OEW_BOARD_REVISION=7,
OEW_MAP_SYNTHETIC_PROFILE=1, OEW_HOST_TEST=1, OEW_HS1_COMMISSIONING_RELEASE=1`

`FORBIDDEN_DEFINES` (без OEW_HS1_COMMISSIONING_RELEASE):
`OEW_ALLOW_DC_LINK, OEW_ALLOW_CONTROL_ADMISSION, OEW_STAGE_A,
OEW_FOC_ENABLE, OEW_VF_ENABLE, OEW_AUTOTUNE_ENABLE`

Всё остальное (scope approval, source/binary SHA-256, build log tokens,
fail-closed семантика) — без изменений.

## 4. Объём изменений

| Файл | Изменение |
|---|---|
| `tools/bench_test2_g0_check.py` | `REQUIRED_DEFINES` += HS1; `FORBIDDEN_DEFINES` −= HS1 |
| `tools/bench_test2_preflight.py` | `REQUIRED_DEFINES` += HS1 (согласованность re-validation G0-evidence) |
| `tests/test_bench_test2_g0_check.py` | фикстуры используют `G0.REQUIRED_DEFINES` (авто); добавить тест: HS1 в required и не в forbidden; negative: отсутствие HS1 в defines → FAIL |
| `tools/bench_test2_g0_check.md` | схема manifest: 6 required defines; примечание про HS1 (реальная interlock-проверка) |
| `TZ_BENCH_TEST2_G0_CHECK.md` | «пяти required defines» → «шести» |
| `docs/AGENTS_STATUS.md` | строка занятости |
| `TZ_BENCH_TEST2_G0_HS1_RELEASE.md` | этот документ |

После пакета (отдельно, по процедуре):
- пересобрать diagnostic-образ с 6 defines;
- обновить G0-evidence кампании и перевалидировать `HARD_GATE_G0=PASS`;
- перепрошить плату, повторить preflight и capture.

## 5. Проверки

- `python -m pytest tests/test_bench_test2_g0_check.py -q` — ALL PASS;
- `python -m py_compile tools/bench_test2_g0_check.py`;
- `make` — production build PASS (C не меняется);
- `git diff origin/main...HEAD --check` — чисто;
- safety-модули (`src/`, `.ioc`) — 0 строк diff (только инструмент/доки).

## 6. Запреты

- НЕ менять прошивку и `.ioc`;
- НЕ добавлять `OEW_ALLOW_DC_LINK`/`OEW_STAGE_A` и прочие energise-define;
- НЕ менять scope approval (forbids_dc_link и т.д. остаются true).
