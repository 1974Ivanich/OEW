# ТЗ: рамко-устойчивое чтение измерений TAO3104A (tools/tao3104a_cap.py)

**Ветка:** `ai3/tao3104a-meas-framing` от `origin/main` (`0bbc3ee`).
**Тип:** фикс инструмента (не firmware; `src/`, `.ioc`, safety-модули не тронуты).
**Предшественник:** `docs/TZ_TAO3104A_VOLTS_V2.md` (влито в `main`, merge
`e35a97d`, приёмка 05.10.2026). Его критерий 4 (живая проверка) **не выполнялся
до 05.10.2026 19:2x** и при первом же живом прогоне выявил дефект — см. §1.

## 1. Проблема (живой прогон на приборе 2026-10-05, 19:09–19:32 UTC)

Стенд: `OWON,TAO3104A,2306027,V3.0.0`, libusb-win32, CH1 `1.00V/дел`, `1X`,
`OFFSET=-126`, меандр ~1 кГц (`HEAD CH1 FREQUENCE=1000.12802`), TIMEBASE `500us`.

Прогон сценария из 16 команд (`C:\campaign_raw\tao3104a_live_pass_20261005\`):

| Критерий 4 ТЗ v2 | Факт | Итог |
|---|---|---|
| `--probe`: `mode=SCREEN samples=1520 pair_packed=True pixels=760 dt=10.000 us span=0.007600 s` | ровно так | PASS |
| `--measure`: 5.12 / 5.08 / −0.04 / 1e-3 / 1e3 | **все `null`** | **FAIL** |
| `--capture --strict`: `PASS` + `measurement_min` | `UNKNOWN` + `legacy_constant`, `rc=1` | **FAIL** |
| `--capture --no-measure`: `UNKNOWN` + `legacy_constant` | ровно так | PASS |
| `--capture --depmem`: `DEPMEM dt=1.000 us samples=9999` | геометрия так, `selfcheck UNKNOWN` | геом. PASS / якорь FAIL |

Корень (wire-level, `diag_framing.py`, repr пакетов): прибор отвечает **без эха
команды и без завершающего `|`**:

```
*IDN?                       -> b'OWON,TAO3104A,2306027,V3.0.0->\n'
:MEASUrement:CH1:PKPK?      -> b'Vpp : 5.120V->\n'
:MEASUrement:CH1:MAX?       -> b'Ma : 5.080V->\n'
:MEASUrement:CH1:MIN?       -> b'Mi : -40.00mV->\n'
:MEASUrement:CH1:PERiod?    -> b'T : 1.000ms->\n'
:MEASUrement:CH1:FREQuency? -> b'F : 1.000KHz->\n'
```

`measure_reply_value()` брал текст **после первого** `->` и потому возвращал
`None` на всех пяти запросах → `Scope.measure()` = `null` →
`calibrate_zero_code()` уходил в `legacy_constant` → `selfcheck` = `UNKNOWN` →
`--strict` отказывал (rc=1). В CSV при этом попадали числа v1-эпохи
`−4.66…+0.62 В` (коды 63…195, `zero_code=179.50`) — то самое расхождение
117 кодов, из-за которого делалось ТЗ v2. Инструмент вёл себя **безопасно**
(не выдал неверные вольты молча), но якорь не работал.

Гипотеза «рамка зависит от состояния панели Measure» **опровергнута**: после
включения показа измерений на приборе (объективно: в JSON `:MEASUrement?` по
CH1 стало `ON=['FREQuency','MAX','MIN','PERiod','VAMP']`) рамка не изменилась —
ни одного пакета с эхом/`|`; значения те же. Рамку не выбирает ни панель, ни
терминатор запроса (`\r\n`/`\n`/`\r`), ни разбиение записи, ни задержка чтения →
парсер обязан **принимать обе формы**.

## 2. Что меняется

| Файл | Правка |
|---|---|
| `tools/tao3104a_cap.py` | `measure_reply_value()` принимает **обе** рамки: значение берётся из первого `->`-сегмента, где есть `'<метка> : <значение>'` (эхо-форма — после первого `->`, простая — до единственного), а голое значение (напр. `b'2.54V->\n'` у `:TRIGger:…:LEVel?`) — как есть; `MEAS_JSON_FIELDS` + `measure_json_values()`; `Scope.measure_json()` (length-prefixed JSON `:MEASUrement:CHx?`, одно bulk-чтение); `Scope.measure()` возвращает `source` = `text`\|`json`\|`none` и при полном молчании пяти текстовых запросов берёт якорь из JSON |
| `tests/test_tao3104a_unit.py` | +5 тестов: простая рамка (живые 5 ответов + `:TRIGger`), согласие обеих рамок, разбор живого JSON-тела (в т.ч. снятие флага `,ON` и «пустое тело» → `None`, не 0), роутинг фолбэка `Scope.measure()` (JSON включается только при молчании, текстовые ответы приоритетны) |
| `tools/OWON_TAO3104A_PROTOCOL.md` | addendum 2026-10-05 (v2): рамка ответов измерения не гарантирована — обе живые формы, JSON-маршрут, проверка и опровержение гипотезы о панели |
| `docs/TZ_TAO3104A_MEAS_FRAMING.md` | этот файл |
| `docs/AGENTS_STATUS.md` | строка о пакете |

CSV-контракт, `src/`, `.ioc`, `Makefile`, safety-модули, `tao3104a_soak.py`,
`shunt_cal_campaign.py`/`sampling_qual.py` — **не тронуты**; `Scope` API
(`identify/head/waveform/close`) сохранён (тест
`test_scope_api_surface_used_by_consumers_is_intact`).

## 3. Критерии приёмки

1. `python -m pytest tests/test_tao3104a_unit.py -q` — все зелёные (38 тестов:
   33 прежних + 5 новых), ни одного skip.
2. `python -m py_compile tools/tao3104a_cap.py tests/test_tao3104a_unit.py` — rc=0.
3. CI `build-test` на ветке — success (`make test` → `pytest tests -q`).
4. **Живой прогон (тот же прибор и стенд, что в §1):**
   - `--measure` даёт `pkpk/max/min/period/frequency` (не `null`) и `source` =
     `text` либо `json` — смотря какую рамку отдаст прибор;
   - `--capture --strict` — `selfcheck PASS`, `zero_code_source=measurement_min`,
     `rc=0`, CSV в вольтах прибора (`vmin ≈ 0.00 В`, `vmax ≈ +5.08 В`);
   - `--capture --depmem --strict` — `mode=DEPMEM dt=1.000 us samples=9999`,
     `selfcheck PASS`;
   - `--capture --no-measure` — по-прежнему `UNKNOWN` + `legacy_constant`
     (фолбэк помечен) — поведение не изменилось.
5. Совместимость: потребители (`map_scope_ingest.parse_scope_csv`,
   `shunt_cal_campaign.py`, `sampling_qual.py`, `tao3104a_soak.py`) не затронуты;
   `Scope` API и CSV-заголовок прежние.

## 4. Границы

- Приборные `MIN/MAX/PKPK` дрейфуют на ±1 код между чтениями (живые: `MIN` =
  `−40.00mV` и `0.000mV`, `PKPK` = `5.120V` и `5.080V`) — это укладывается в
  остаточную точность ±1 код из ТЗ v2; допуск `--tol-codes` и `robust_levels`
  остаются обязательными.
- JSON-маршрут проверен только по каналу (`:MEASUrement:CHx?`); форма «все
  каналы» (`:MEASUrement?`, 3526 Б) содержит мусорные control-байты и целиком
  не парсится — в якоре не используется (зафиксировано в протоколе).
- Рамку ответа менять нельзя (SET-команды V3.0.0 игнорирует) — пакет именно
  **принимает** обе возможные формы, а не навязывает одну.
- Полная метрология канала (нелинейность, 14-битный тракт) — вне пакета.

## 5. Доказательства

- Отчёт живого прогона:
  `C:\campaign_raw\tao3104a_live_pass_20261005\RESULTS_TAO3104A_LIVE_20261005.md`
  (+ `summary.txt`, `MANIFEST_SHA256.txt`, сырые stdout/stderr всех прогонов,
  CSV и сайдкары, диагностики `diag_framing*.py`, `diag_fix_probe.py`,
  `measure_state.py precheck|after`). Прогоны выполнены из worktree на принятом
  `main` (`0bbc3ee`), репозиторий не менялся.
- Кандидатное исправление проверено на приборе **до** правки кода
  (`diag_fix_probe.py`, in-memory monkeypatch): `CH1 verdict=PASS`,
  `zero_code=63.00 via measurement_min`, `scale_mode=fit`, расхождения
  `vmin/vmax/vpp` — `0.00` кодов; совпадение с прежним парсером на всех 12
  векторах действующего теста.

## 6. Приёмка (05.10.2026)

- Ветка `ai3/tao3104a-meas-framing` = `ddfd2000d8237d676864fe80c590d35203214e4d`;
  merge-base с `origin/main` — tip (`0bbc3ee`, `main` впереди на 0) ⇒ слияние
  без конфликтов.
- Приёмочное слияние: `git merge --no-ff` в worktree приёмки
  (`C:\campaign_raw\acc_meas_framing`) ⇒ merge-commit **`384ae2b`**
  (родители `0bbc3ee` + `ddfd200`): 5 файлов, +355/−15, маркеров конфликта 0,
  журнал 216 строк, `src/`/`.ioc`/`Makefile`/linker/startup не затронуты.
- CI `build-test`: ветка — run 37365968168 (`ddfd200`) **success** (все шаги,
  вкл. «Hosted + QEMU + pytest tests»). По дереву слияния CI закрыт прогоном на
  tip `main` — run 37370138220 (`49054bb`, штамп приёмки; дерево = merge
  `384ae2b` + docs-only) — **completed/success, 16 шагов, 0 не-success**.
  Прогон на самом merge-коммите (37368807502, `384ae2b`) — **canceled без
  выполнения шагов**: job `cancelled`, `steps=0`, runner не назначен; в `ci.yml`
  `concurrency` нет, а на 19:11Z–20:32Z пришёлся внешний инцидент GitHub Actions
  («Incident with Actions» — задержка выдачи GitHub-hosted runner'ов), т.е. отмена
  инфраструктурная и к репозиторию/коду отношения не имеет. Компенсирующие
  проверки на слитом дереве: 100 passed (unit 38/38 + потребители), `py_compile`
  rc=0, образ `firmware.bin` байт-в-байт совпал со сборкой до слияния (пункт ниже).
- Образ не менялся: чистая сборка `0bbc3ee` и слитого дерева — `firmware.bin`
  73952 Б, SHA-256 `00024826B37C8B9E77611560B75763D81238976B0BBAD6F9F6F9B37B800E1553`
  (тот же SHA, что фиксировался при приёмке ТЗ v2) ⇒ прошивать/переидентифицировать
  образ не нужно.
- Живой повтор критерия 4 выполнен **на исправленной ветке** на той же сессии
  прибора: `RESULTS_TAO3104A_LIVE_20261005.md`, §«Живой повтор критерия 4 после
  правки» (все три `v*`-CSV и `--probe` — `PASS`).
- Поздняя правка docs-only (внесена штампом приёмки): дописано заключение §1 —
  «⇒ парсер обязан **принимать обе формы**» (в исходной редакции фраза
  обрывалась на «ни задержка чтения»).
