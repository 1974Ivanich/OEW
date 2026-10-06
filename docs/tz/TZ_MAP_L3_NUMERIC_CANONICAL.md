# ТЗ: L3 map pipeline — numeric-fix и структурная валидация canonical

**Источник:** read-only аудит production-модулей (Manus, после merge GUI-пакета),
подтверждён приёмщиком (верифицированы все три пункта: scaled_ma в коде,
3b0b04f в истории, IsCanonical без recon/region-валидации).
**Область:** L3 commissioning pipeline (по умолчанию ОТКЛЮЧЁН:
`OEW_MAP_CAPTURE=0`, `OEW_MAP_L3=0`). Fail-closed политика и default-deny
НЕ меняются. Generic-образ не затрагивается.

## P1-A. Solver: вернуть mA-domain и wide arithmetic

Текущий `src/map_measurement_solver.c` использует `scaled_ma()` (`value / 1000`)
для всех входных токов — субамперные измерения (0.2–0.9 A = 200–900 mA)
обнуляются, determinant/пороги искажаются. Воспроизведено:
`status=3 (MAP_SOLVER_SINGULAR)` на 8 точках 0.2–0.9 A при матрице [[2,1],[-1,3]].

Требуется вернуть реализацию коммита `3b0b04f` («fix: preserve precision in
map solver...», предок main):
- убрать `scaled_ma()`; использовать сырые mA-значения `idc1_ma`/`idc2_ma`;
- `determinant` — `__int128`, нормализация `det_norm` (det/1e12),
  сравнение порогов через `det_norm`; `trace²` — wide arithmetic;
- критерий: hosted-репро 0.2–0.9 A → `status != MAP_SOLVER_SINGULAR`,
  разумные коэффициенты (rms мал); тесты из certifier (вкл. <1 A, 15 A,
  near-singular) — PASS.

## P1-B. Вернуть solver/certifier hosted-регрессию

`tests/map_solver_certifier_test.c` (201 строка, из `3b0b04f`) и Makefile-target
были удалены коммитом `5662e5e` (откат «вне-ТЗ» — признан ошибочным).
Требуется:
- вернуть `tests/map_solver_certifier_test.c` из `3b0b04f`;
- вернуть Makefile-правило сборки + строку прогона в `test-hosted` +
  упоминание в целях (имена hosted-exe БЕЗ подстроки «dispatch» —
  ограничение локального security-софта; `map_solver_certifier_test.exe`
  этому правилу удовлетворяет);
- устаревший `tests/map_solver_certifier_test.exe` в дереве — удалить или
  пересобрать (не должен оставаться «мёртвым» артефактом).

## P1-C. Canonical: структурная валидация до load_measured()

`MapCandidate_IsCanonical()` (`src/map_candidate.c:70`) проверяет magic/
revision/identity/manifest/CRC, но НЕ `recon[][]`, `region[][]` и
startup-контекст. `MapCommissioning_LoadMeasured()` использует её как
единственную семантическую проверку. Доказано: `recon[0][0].valid=false`
+ пересчёт CRC → `canonical_after_invalid_recon=1`.

Требуется добавить в `IsCanonical` (или в load-path ДО `load_measured()`):
- все `recon[i][j]`: `valid`, токи/индуктивности в разумных диапазонах
  (неотрицательные, конечные; пороги — из существующих констант карты);
- все `region[i][j]`: корректность регионов (границы, размеры);
- startup-поля/контекст, необходимые для применения карты;
- критерий: hosted-proof «invalid recon + пересчитанный CRC →
  canonical == false»; штатный `MapCandidate_Build()` → canonical == true
  (не ломать нормальный путь).

## Запреты

- Не включать L3 по умолчанию; не менять fail-closed gates
  (`OEW_MAP_CAPTURE`/`OEW_MAP_L3`/default-deny), `pwm.c`, `protect.c`,
  `.ioc`, распиновку, HAL.
- Затрагиваемые файлы: `src/map_measurement_solver.c`,
  `src/map_candidate.c`, `src/map_commissioning.c` (только load-path),
  `Makefile`, `tests/map_solver_certifier_test.c`, `tests/` (новые проверки).
- Не трогать GUI и прочие модули.

## Обязательные проверки

- `make` (production, generic) — PASS;
- `make test` — ALL PASS (hosted+QEMU+pytest), включая восстановленный
  certifier;
- commissioning: `make clean && make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1
  -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7"` — PASS (L3-путь компилируется);
- `git diff origin/main...HEAD --check` — чисто;
- `src/` вне map_* — 0 строк diff;
- CI ветки — зелёный.

## Процесс

Ветка `ai<N>/map-l3-numeric-canonical` от свежего `origin/main`, запись в
`docs/AGENTS_STATUS.md`, публикация + `git ls-remote` подтверждение SHA,
ожидание приёмки (main не трогать).
