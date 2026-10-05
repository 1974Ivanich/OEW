# ТЗ v2: TAO3104A — привязка «код → вольты» и оси времени к прибору + самопроверка кадра

**Ветка:** `ai2/tao3104a-volts-v2` от `origin/main` (ae794bc).
**Тип:** фикс инструмента (не firmware; `src/`, `.ioc`, safety-модули не тронуты).
**Заменяет:** `docs/TZ_TAO3104A_VOLTS_MODEL.md` (v1) — его константа нуля и
формула оси времени на живом приборе не подтвердились (см. §1). v1-файл остаётся
как история, помечен «отменён».
**Живой прибор:** OWON TAO3104A, SN 2306027, FW V3.0.0, драйвер libusb-win32.

## 1. Проблема (замерено на приборе 2026-10-05, вход CH1 = меандр 0…+5 В, 1 кГц)

Прибор сам даёт эталон: `:MEASUrement:CH1:` → `PKPK : 5.120V`, `MAX : 5.080V`,
`MIN : -40.00mV`, `PERiod : 1.000ms`, `FREQuency : 1.000KHz`. Всё, что считал
`tao3104a_cap.py`, противоречило этим числам.

1. **Константа нуля `CODE_0V = 305.5` — не глобальная.** Она калибровалась
   маркером прибора в ОДНОЙ конфигурации (500 мВ/дел, 1X, OFFSET=−100; там
   код 222 = +0.330 В) и даже не влезает в 8-битный диапазон кодов 0..255.
   На живом кадре (1 В/дел, 1X, OFFSET=−126) она ставит 0 В в код
   `OFFSET+305.5 = 179.5`, тогда как нижняя полка кадра — код **64**:
   кадр декодируется в **−4.66…+0.62 В** вместо **−0.04…+5.08 В**.
   Ошибка **+4.66 В (117 кодов)** — только от константы.
   Приборные `MIN/MAX` этой же сессии (`-40.00mV`, `+5.080V`) это подтверждают.
2. **Ось времени `dt = 10·TIMEBASE.SCALE/n = 3.289 мкс` — опровергнута.**
   - геометрия: SCREEN-кадр это **760 пикселей × [max, min]** = 1520 сэмплов,
     а не 1520 равномерных отсчётов; на живом кадре у 745 из 760 пикселей
     `min == max` (отличаются только 15 переходных пикселей);
   - сигнал повторяется каждые 200 SCREEN-сэмплов = 100 пикселей; при
     `PERiod = 1.000 ms` это ровно **10 мкс на пиксель** (50 px/дел при
     500 мкс/дел) ⇒ кадр покрывает **7.6 мс**, а не 5 мс;
   - DEPMEM-HEAD той же сессии: `SAMPLE.FULLSCREEN = 7600` сэмплов при
     1 MSa/s = 7.6 мс — независимое подтверждение окна; полупериоды в DEPMEM
     499–501 мкс ⇒ 1.000 кГц;
   - прежняя модель давала 1.52× (1.5–1.6 кГц вместо 1.000 кГц, окно 658 мкс
     вместо 1.000 мс на период) — т.е. частоты из SCREEN считались неверно.
3. **`:MEASUrement:*` не использовалась**, хотя это единственный достоверный
   источник амплитуды/частоты на этом FW. Живые ответы: `PKPK`/`MAX`/`MIN`/
   `PERiod`/`FREQuency` отвечают (`VPP?`, `MEAN?`, `RMS?` — молчат); значения
   **знаковые** (`-40.00mV`) и с суффиксом `KHz`, который `unit()` не умела
   (1.000KHz читался бы как 1.000 Гц).
4. **Не было самопроверки.** Ничто не сравнивало декодированные вольты с
   прибором, поэтому неверный кадр проходил молча (что и произошло в v1).

## 2. Что меняется

| Файл | Правка |
|---|---|
| `tools/tao3104a_cap.py` | `CODES_PER_DIV = 25`, `SCREEN_PX_PER_DIV = 50`, `MEAS_QUERIES` (PKPK/MAX/MIN/PERiod/FREQuency); `Client.measure()` — чтение приборных измерений; `unit()` — знак, `KHz`, ветка `Sa/s`; `volts_per_code()`, `legacy_zero_code()`, `to_volts(codes, meta, zero_code=None)`, `robust_levels()`, `measure_reply_value()`, `calibrate_zero_code()` (нулевой код кадра от `MIN`, режимы `auto/nominal/fit`), `selfcheck()` (сравнение tool-vs-прибор по MIN/MAX/PKPK, допуск в кодах), `frame_layout()` (SCREEN: 760 px × [max,min], `dt = TB/50`; DEPMEM: `dt = 1/SAMPLERATE`, `DATATYPE` принимается и как `WAVEDEPMEM`); `time_axis()` возвращает времена по реальной геометрии |
| `tools/tao3104a_cap.py` (CLI) | `--measure` (JSON измерений), `--depmem` (равномерный кадр 1 мкс), `--no-measure`, `--scale-mode {auto,nominal,fit}`, `--zero-code`, `--tol-codes`, `--strict`; `capture_region()` возвращает `(head, layout, sidecar)` и пишет рядом с CSV сайдкары `<out>.head.json` и `<out>.measure.json` (геометрия кадра, `zero_code` и его источник, масштаб, вердикт самопроверки) |
| `tests/test_tao3104a_unit.py` | host-тесты (31 шт., не skips): парсер единиц (знак, KHz, `(1MSa/s)`), модель вольт (`k = SCALE/25·PROBE`, нулевой код, отказ от `CODE_0V` как глобальной), калибровка по прибору, `selfcheck` (PASS/WARN/UNKNOWN и предупреждение при расхождении), `measure_reply_value`, геометрия кадра (760 px × [max,min], 1 кГц = 100 px, `10*TB/n` отвергнут, DEPMEM = 1 мкс, `WAVEDEPMEM`, `FULLSCREEN = 7.6 мс`, неизвестный `DATATYPE` → ошибка) — на живом HEAD/живых кодах сессии 2026-10-05 |
| `tools/OWON_TAO3104A_PROTOCOL.md` | вертикаль и ось времени переписаны (прибор — эталон), старые разделы помечены отменёнными; новые разделы: `:MEASUrement:*?` (живая таблица ответов), DEPMEM (работает, `WAVEDEPMEM`, 9999 сэмплов, 1 мкс), addendum 2026-10-05 к верификации; парсер единиц — знак/KHz/`Sa/s` |
| `docs/AGENTS_STATUS.md` | строка о пакете |

`docs/TZ_TAO3104A_VOLTS_MODEL.md` (v1) остаётся только как история и помечен
отменённым: его `CODE_0V` сохранён в коде исключительно как **помеченный
фолбэк** (`zero_code_source='legacy_constant'`) для кадров, где приборные
измерения не читаются; такие кадры самопроверку не проходят, а `--strict`
превращает это в ненулевой код возврата.

## 3. Критерии приёмки

1. `python -m pytest tests/test_tao3104a_unit.py -q` — все тесты зелёные, ни
   одного skip (модуль импортирует `usb`/`numpy`; в CI они в apt-наборе).
2. `python -m py_compile tools/tao3104a_cap.py tests/test_tao3104a_unit.py` —
   без ошибок (проверено, код 0). Стиль: в этой среде `flake8`/`pycodestyle`
   не установлены ни в одном интерпретаторе (`py -3`/`py -3.11` → «No module
   named flake8/pycodestyle»), конфига `.flake8`/`setup.cfg`/`pyproject.toml` в
   репозитории нет и CI линт не запускает (`.github/workflows/ci.yml` — только
   build/test/pytest). Поэтому вместо flake8 выполнена механическая проверка
   изменённых файлов: табов нет, trailing whitespace нет, BOM нет, файл
   заканчивается одним переводом строки, EOL = CRLF (как у остальных файлов
   репозитория); длина строк — как в базовой версии (в `tao3104a_cap.py`
   строк >79 символов было 12 из 376, стало 42 из 744; max 91 → 98 — длинные
   строки это `RuntimeError(...)` и докстринги, стиль файла не меняется).
3. CI `build-test` на ветке зелёный.
4. На приборе (ручная проверка, только READ-команды):
   - `--measure` печатает `pkpk/max/min/period/frequency` (у CH1 = 5.12/5.08
     /−0.04/1e-3/1e3);
   - `--probe` печатает `mode=SCREEN samples=1520 pair_packed=True pixels=760
     dt=10.000 us span=0.007600 s`;
   - `--capture --strict` на меандре даёт `selfcheck PASS`, `vmin ≈ −0.04 В`,
     `vmax ≈ +5.08 В`, `zero_code_source` = приборный (`measurement_min`);
   - `--capture --no-measure` даёт `verdict UNKNOWN` и
     `zero_code_source='legacy_constant'` (фолбэк помечен, не молчит);
   - `--capture --depmem` даёт `mode=DEPMEM dt=1.000 us samples=9999`.
5. Контракт CSV не изменился — `sample_index, time_s, chN_v` (потребители
   `tools/map_scope_ingest.py::parse_scope_csv`, `tools/tao3104a_soak.py`,
   `shunt_cal_campaign.py`, `sampling_qual.py` читают колонки по имени);
   меняется только смысл `time_s` (реальная геометрия кадра). Закреплено тестами
   `test_csv_header_contract_is_unchanged` (константа `CSV_HDR`) и
   `test_scope_api_surface_used_by_consumers_is_intact` (единственная точка
   стыковки со `shunt_cal_campaign.py` — `Scope()` + `identify/head/waveform/
   close`; `tao3104a_soak.py` вообще не импортирует `tao3104a_cap` — у него своя
   копия транспорта).

## 4. Границы (что сознательно НЕ входит)

- `src/`, `.ioc`, safety-модули (`foc/pwm/protect/vf/adc/adc_dispatch`) не
  тронуты; SET-команды прибору не отправляются (V3.0.0 их игнорирует) — арм
  по-прежнему вручную с передней панели.
- Остаточная погрешность вольт ±1 код от каждого якоря (нулевой код из `MIN`,
  масштаб из `PKPK`): ±40 мВ при 1 В/дел, ±20 мВ при 500 мВ/дел. Точность выше
  требует второго эталона, которого в сессии нет.
- `CODE_0V` как глобальная константа не удалён из кода, но помечен как
  `legacy`-фолбэк (см. §2); новые вычисления его не используют.
- DEPMEM на каналах с `DISPLAY=OFF` не проверялся (инструмент отдаёт по ним
  не-подтверждённые данные) — `--depmem --include-off` только предупреждает.
- `tools/shunt_cal_campaign.py` и `tools/sampling_qual.py` содержат свою копию
  старой модели (включая `10·TB/n`) и живую точку 500 мВ/дел, закреплённую
  `tests/test_shunt_cal_campaign.py`; они этим ТЗ НЕ правятся — им нужен
  отдельный пакет (иначе будет сломан их действующий тест). Из-за этого
  комментарий `tools/shunt_cal_campaign.py:56` («держится синхронно с
  tools/tao3104a_cap.py CODE_0V») уже неверен — глобальной `CODE_0V` в
  `tao3104a_cap.py` больше нет, и это надо поправить в том же отдельном пакете
  вместе с переносом модели (здесь файл сознательно не тронут: одна ветка —
  одно ТЗ). API-стык (`Scope`) при этом сохранён и закреплён тестом.
- `tools/tao3104a_soak.py` не правится и не ломается: у него собственная копия
  `Scope` (свой bulk-транспорт), `tao3104a_cap` он не импортирует.
- Файловый протокол/CSV-интерфейс `map_scope_ingest` не расширяется: новый
  сайдкар `.measure.json` — дополнительный артефакт, не обязательный вход.

## 5. Доказательства (живой прибор, воспроизводимо)

- `build/scope_snapshot_20261005_181320/` — SCREEN-снимок: `scope_capture.head.json`
  (`DATATYPE=SCREEN`, `FULLSCREEN=1520`, CH1 100 мВ/дел 10X OFFSET=−136),
  `scope_measure_scope.txt` (живые ответы `:MEASUrement:*`),
  `CORRECTION_amplitude_frequency.md` (разбор двух дефектов), `_check_*.txt`.
- `build/scope_depmem_20261005_200514/` — чистый DEPMEM: `truth.txt`
  (`DATALEN=10000`, `(1MSa/s)`, полупериоды 499–501 мкс, полки 64/194,
  приборные `PKPK=5.120V`, `MAX=5.080V`, `MIN=-40.00mV`, `F=1.000KHz`,
  `T=1.000ms`), `scope_depmem.head.json` (`DATATYPE=WAVEDEPMEM`,
  `FULLSCREEN=7600`, `SCREENOFFSET=1200`), 9999-сэмпловый CSV.
- Ключевой кросс-чек окна: DEPMEM `FULLSCREEN=7600 @ 1 MSa/s = 7.6 мс` ==
  SCREEN `760 px × 10 мкс` — две независимые ветви прибора дают одно окно;
  он же закреплён тестом (`frame_layout`).
- Проверка на хосте (ПК-2, Windows, Python 3.13): `pytest
  tests/test_tao3104a_unit.py -q` → **33 passed**; `py -3 -m py_compile
  tools/tao3104a_cap.py tests/test_tao3104a_unit.py` → код 0; полный
  `pytest tests -q` → **602 passed / 24 skipped / 4 failed** (65 с). Падения —
  только `tests/test_hwt_backup.py` (файл не тронут этим ТЗ: `git diff
  origin/main -- tests/test_hwt_backup.py tools/hwt_backup.py` пуст): 3 из 4 —
  `UnicodeEncodeError: 'charmap' ... '\u2192'` при печати стрелки в
  cp1251-консоль (с `PYTHONIOENCODING=utf-8` проходят), 4-й
  (`test_verify_ok_and_mismatch`) — искажение кодировки (mojibake) stdout
  подпроцесса (`'РАСХОЖДЕНИЕ'` не находится). На Linux/UTF-8 (CI `build-test`,
  шаг `make test` → `pytest tests -q`) набор зелёный.
