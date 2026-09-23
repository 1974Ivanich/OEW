# TZ: TAO3104A USB capture pipeline (PC-2)

**Ветка:** `ai2/tao3104a-usb-capture`
**База:** `origin/main` @ `149bdaf7a730f935f3a5c303d63c747ad263354c`
**Дата:** 2026-09-23
**Автор:** ai2 (Hermes), ПК-1
**Статус:** **запрос на приёмку**

## 1. Зачем

Кампания B по grid-профилю BOAR (`TZ_MAP_GRID_PROFILE`) требует записывать
осциллограмму TAO3104A параллельно с записями `MapCapture` на STM32.
Существующие инструменты `map_scope_ingest.py` / `boar_campaign_template.py`
ожидают CSV от внешнего scope-инструмента; для TAO3104A такого инструмента
в репо нет.

Внешний ИИ прислал `tao3104a_mapcapture.zip` (см. `pasted_content_*.txt`),
но он использует несуществующие команды `:WAV:*` и `:SING` и опирается на
`pyvisa`, тогда как на этом ПК драйвер — `libusb-win32`, и pyvisa не открывает
прибор вообще (`VI_ERROR_LIBRARY_NFOUND`).

В этой ветке:

* захват через **сырой bulk USB** (без NI-VISA, без USBTMC);
* протокол валидирован на живом `OWON,TAO3104A,2306027,V3.0.0` (см.
  `tools/OWON_TAO3104A_PROTOCOL.md`);
* soak A (HEAD+CH1, 100 итераций) и soak B (HEAD+CH1..4, 50 итераций) **PASS**
  на ПК-1 (см. ниже).

## 2. Цель

Предоставить на ПК-2:

1. `tools/tao3104a_cap.py` — CLI для одиночной и многозонной съёмки
   (`--list`, `--probe`, `--head`, `--capture`, `--campaign`).
2. `tools/tao3104a_soak.py` — soak A/B с жёсткими инвариантами:
   - `IDN_OK ∧ HEAD_OK ∧ HEAD_DATALEN==1520 ∧ CHx_OK ∀x∈{1..4}`
   - hard stop на первой аномалии, **никакого** `PASS_WITH_WARNING`;
   - никаких SET-команд (V3.0.0 их игнорирует по USB).
3. `tools/OWON_TAO3104A_PROTOCOL.md` — полная карта протокола, валидированная
   двумя независимыми источниками (PC JAR `com.owon.uppersoft.hdoscilloscope_1.6.33`
   + Android APK `OSC3000_1.3.8`).
4. `docs/TZ_TAO3104A_USB_CAPTURE.md` — этот документ.
5. `docs/AGENTS_STATUS.md` — строка статуса.

## 3. Архитектура

### 3.1 Транспорт

```
PC                                    TAO3104A
│ libusb-win32 / pyusb                │ VID 0x5345 / PID 0x1234
│  EP 0x03 OUT  ──────► SCPI text + \r\n
│  EP 0x81 IN   ◄────── reply (text + "->\n")  или 4-byte LE length-prefix + JSON/binary
```

Прибор **не является USBTMC-устройством** в строгом смысле (нет
`DEV_DEP_MSG_OUT` заголовка и MsgID/EOM). Это **plain text поверх bulk
endpoints**. Любой код, начинающийся с `*IDN?`/`:ACQuire:Mode?` и т.п.,
работает; USBTMC-wrappers (`pyvisa` USB-TMC class) — **нет**.

### 3.2 Формат данных waveform

Для `:DATA:WAVE:SCREEN:CH<n>?`:

```
+--------+----------------+------------------+-----------------+
| plen   |     sample 1   |      sample 2    | ... | sample N  |
| LE u32 |  b[0]=0, b[1]= |  b[2]=0, b[3]=   |     |           |
+--------+----------------+------------------+-----------------+
4 bytes    2 bytes each                    ...
```

* `plen` = `2 * DATALEN` (для SCREEN-режима с `DATALEN=1520` → `plen=3040`).
* Каждая точка — **8-битный код в 16-битном little-endian контейнере**:
  - чётный байт = 0x00,
  - нечётный байт = код (0..255).
* Извлечение: `np.frombuffer(body, '<u2') >> 8`.
* `HEAD?` отдаёт JSON с полями:
  - `SAMPLE.DATALEN` (=1520 для SCREEN, =7600 для DEPMEM)
  - `SAMPLE.SAMPLERATE` ("(1MSa/s)" — формат скобки + единицы)
  - `CHANNEL[i].SCALE` ("100mV"), `OFFSET` (raw code), `PROBE` ("1X")
  - `TIMEBASE.SCALE` ("500us")
* Вольты на точку:
  ```python
  v = (codes - offset) * (scale/25.0) * probe
  ```
  (8 делений по вертикали × 25 кодов/деление — наблюдаемая конвенция OWON).

### 3.3 Read-loop

V3.0.0 отдаёт binary payload пакетами по 512 байт **с задержками до ~1 с**
между пакетами. Агрессивные короткие read-timeouts обрезают ответ
(симптом: `plen=3040`, реально прочитано `2047` байт).

Решение (`tools/tao3104a_cap.py::Scope.bulk`):

```python
while time.time() - t0 < timeout_s:
    chunk = self._read(500)              # короткий polling-read
    if chunk:
        got += chunk
        if len(got) >= 4:
            ln = int.from_bytes(got[:4], 'little')
            if 4 <= ln < maxbytes and len(got) >= 4 + ln:
                break                     # payload полностью дочитан
    # else: continue waiting up to timeout_s
```

### 3.4 Синхронизация с `mapcap`

V3.0.0 **игнорирует все SET-команды по USB** (включая `:TRIGger:SINGle:SWEEp
SINGle`, `:ACQuire:RUN`, `:ACQuire:STOP`, `:TRIGger:SINGle:EDGE:SOURce EXT`).
Управлять scope'ом можно **только с передней панели**.

Поэтому синхронизация реализуется как **PB6 → CH1** (щуп ACS712 или сам
PB6 заводится на CH1, scope уже настроен на `EDGE` trigger по CH1):

* при `mapcap run` STM32 поднимает PB6 на 0.5–1.7 мс;
* этот же сигнал виден на CH1 осциллографа;
* фронт PB6 ловится trigger'ом, экран замораживается;
* USB-команда `:DATA:WAVE:SCREEN:CH1?` отдаёт этот кадр.

На ПК-1 стенда нет — синхронизация будет подтверждена на ПК-2 отдельным
qualification-тестом (см. §6).

## 4. Установка и запуск (на ПК-2)

```powershell
cd <repo>
git fetch origin
git checkout ai2/tao3104a-usb-capture   # или после merge main
py -3 -m pip install -r tools/requirements-tao3104a.txt
py -3 tools/tao3104a_cap.py --list
py -3 tools/tao3104a_cap.py --probe
py -3 tools/tao3104a_cap.py --capture --out test_capture.csv
py -3 tools/tao3104a_soak.py --mode A --n 100 --out soak_A.csv
py -3 tools/tao3104a_soak.py --mode B --n  50 --out soak_B.csv
```

Ожидаемые результаты (на ПК-1 проверены, см. §5):

```
$ py -3 tools/tao3104a_cap.py --list
USB: found VID 5345 PID 1234
  product : Oscilloscope
  serial  : <SN>
  driver  : libusb-win32 (raw bulk) - pyvisa CANNOT open this device

$ py -3 tools/tao3104a_cap.py --probe
IDN= OWON,TAO3104A,<SN>,V3.0.0
DATATYPE= SCREEN RUNSTATUS= TRIG
DATALEN= 1520 SAMPLERATE= (1MSa/s)
  CH1 display=ON scale=100mV probe=1X offset=-139
  ...
```

## 5. Проверка на ПК-1 (livedump)

| Тест | Условие | Результат |
|---|---|---|
| `--probe` | свежий scope, CH1=мейнд | IDN, DATALEN, RUNSTATUS=TRIG ✅ |
| `--capture --out test_capture.csv` | меандр на CH1 | 1521 строка CSV, форма меинда в CSV, V_max=1.336 В ✅ |
| Soak A (HEAD+CH1, 100 итераций, settle=0.5 с) | свежий scope | **100/100 PASS**, ~1.5 с/iter ✅ |
| Soak B (HEAD+CH1..4, 50 итераций, settle=0.5 с) | свежий scope | **50/50 PASS**, ~3.5 с/iter ✅ |

CSV soak лежат рядом в `tools/soak_A100.csv` / `tools/soak_B50.csv` как
доказательство на момент приёмки.

## 6. Что НЕ входит в пакет и требует ТЗ на ПК-2

1. **Qualification PB6→CH1 синхронизации на стенде.** Один `mapcap run` с
   PB6 на CH1 → `RUNSTATUS` стал `TRIG`/`STOP`, `CH1?` содержит PB6-фронт.
   Без этого `region_N.csv` ≠ `MapCapture` records N.
2. **`--require-trig` production-gate.** После подтверждения п.1 — добавить
   формальный gate, чтобы `region` помечался `PASS` только если `HEAD.RUNSTATUS`
   == `TRIG`/`STOP`. Не делать до получения реального стендового результата.
3. **`mapcap <-> scope` time alignment.** Сейчас синхронизация **через
   физический trigger**, а не через таймштамп. Если окажется, что PB6-фронт
   попадает в разные индексы массива CH1 в зависимости от time-base, нужно
   фиксировать `HOffset` и `HScale` из HEAD и искать PB6-импульс на экране.
4. **DEPMEM (15200 Б/канал).** Не тестировалось end-to-end; формат тот же
   (`plen=2*DATALEN`), но `DATALEN=7600`, и кампания, вероятно, захочет
   больше точек. Если да — добавить `--mode DEPMEM` в `waveform()`.

## 7. Что было сломано в исходном `tao3104a_mapcapture.zip`

Внешний ИИ прислал инструмент, который **не работает на этом экземпляре**:

| Заявлено в пакете | Реальность на TAO3104A V3.0.0 |
|---|---|
| `import pyvisa` | `pyvisa.ResourceManager()` → `VI_ERROR_LIBRARY_NFOUND` (драйвер `libusb-win32`, не NI-VISA) |
| `:WAV:SOUR CH1` | 0 байт — команда не существует |
| `:WAV:FORM BYTE` | 0 байт |
| `:WAV:PRE?` | 0 байт |
| `:WAV:DATA?` | 0 байт |
| `:SING` | 0 байт |
| `:SYST:ERR?` | 0 байт |
| `np.frombuffer(y, dtype=np.uint8)` для waveform | данные **8-бит в 16-бит контейнере**, нужно `'<u2' >> 8` |
| `preamble[4..9]` для масштабирования | масштабирование из JSON HEAD, а не из CSV-preamble |

Также пакет использует `SCPI_SEQUENCE.md` с **сокращённой формой**
`:DATA:WAVE:SCREen:HEAD?` — реальная команда **`:DATA:WAVE:SCREEN:HEAD?`**
(полная форма, иначе V3.0.0 молча игнорирует). Это подтверждено как из
исходников OWON PC-software (`com.owon.uppersoft.hdoscilloscope_1.6.33.jar`),
так и из Android-приложения (`OSC3000_1.3.8.apk`).

## 8. Изменяемые файлы

```
docs/TZ_TAO3104A_USB_CAPTURE.md    (новый)
tools/tao3104a_cap.py              (новый, ~290 строк)
tools/tao3104a_soak.py             (новый, ~200 строк)
tools/OWON_TAO3104A_PROTOCOL.md    (новый, ~250 строк)
tools/requirements-tao3104a.txt    (новый, ~3 строки)
tools/soak_A100.csv                (новый, evidence на момент приёмки)
tools/soak_B50.csv                 (новый, evidence на момент приёмки)
docs/AGENTS_STATUS.md              (обновить строку занятости)
```

**Не трогаются** (требуют отдельного ТЗ, AGENTS.md §правила):
- `src/`, `Makefile`, `.ioc`, `main.c` (safety-mодули)
- `tools/map_scope_ingest.py`, `tools/boar_campaign_template.py` (кампания B)
- `tests/*` (нужны отдельные host-тесты для CLI; см. §9)

## 9. Тесты (что добавить на ПК-2)

`tao3104a_cap.py` и `tao3104a_soak.py` без прибора не тестируются; нужны
smoke-tests на host:

1. `tests/test_tao3104a_unit.py` — `unit()` parser (mV, us, MSa/s, GSa/s),
   `to_volts()` с известным `SCALE/OFFSET`, `time_axis()` с известным
   `SAMPLERATE`.
2. `tests/test_tao3104a_soak_invariants.py` — синтетические raw-ответы
   (PASS/FAIL по `head_ok`, `plen`, `datalen`, `ch_ok`) прогоняются через
   `check_head()` / `check_ch()` без прибора.

На ПК-1 в этой ветке тесты **не добавлены** (нет pytest-окружения, плюс
это блокеры для приёмки — пусть приёмщик решит, нужны ли они как часть
этого пакета или отдельной веткой).

## 10. CI

CI (`build-test`) триггерится `push: branches: '**'` на GitHub Actions
ubuntu-latest. Этот пакет **не собирается** `make` (только Python), поэтому
CI-прогон покажет только `py_compile`/`make` без эффекта, что и видно как
PASS на этой ветке.

Реальный gate — ручной `--probe` + `--capture` + soak A/B на ПК-2. Эти
команды запускаются оператором по инструкции §4.

## 11. План приёмки для ревьюера

1. `git fetch origin && git checkout ai2/tao3104a-usb-capture`
2. `git diff --check` — чисто (нет trailing-whitespace/CFLF в новых файлах).
3. `py -3 -m py_compile tools/tao3104a_cap.py tools/tao3104a_soak.py` — OK.
4. `py -3 tools/tao3104a_cap.py --list` — находит TAO3104A через libusb.
5. `py -3 tools/tao3104a_cap.py --probe` — IDN, DATALEN, RUNSTATUS.
6. `py -3 tools/tao3104a_cap.py --capture --out /tmp/test.csv` —
   1521 строка, форма CH1 в вольтах разумна.
7. `py -3 tools/tao3104a_soak.py --mode A --n 50` — PASS.
8. `py -3 tools/tao3104a_soak.py --mode B --n 25` — PASS.
9. Сверка soak CSV с приложенными `tools/soak_A100.csv` / `tools/soak_B50.csv`:
   формат колонок одинаковый, IDN совпадает с `--probe`.
10. `docs/AGENTS_STATUS.md` — строка `ai2/tao3104a-usb-capture` присутствует.

Если пп. 1–10 PASS — пакет принимается, статус «**влито в main**».
