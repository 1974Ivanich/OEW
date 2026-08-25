# `bench_test2_capture.py` — UART + sigrok для no-HV test № 2

Скрипт собирает воспроизводимый набор evidence для no-HV test № 2 MapCapture. Он сохраняет непрерывный UART-лог, запускает цифровой захват sigrok, выполняет ограниченную последовательность диагностических команд, опрашивает terminal state и создаёт `summary.json`.

> Скрипт **не прошивает MCU, не включает DC-link, не выполняет FOC/V/f/autotune и не вызывает `mapcap build`**. Он не заменяет физический preflight, проверку PWM-формы на CSV или приёмку результатов оператором.

## 1. Строгий UART-контракт

Скрипт принимает только **расширенную** строку `@MC:STATUS`. Старый формат без `detail`, `raw_vbus`, `vbus_mv`, `i1_ma`, `i2_ma`, `adc_status`, `sector`, `window` считается ошибкой автоматизации, а не совместимым PASS.

| Слой | Что доказывает | Значения |
|---|---|---|
| `automation` | Команды UART, причина terminal stop, raw/engineering VBUS, токи, drain и факт получения CSV | `PASS` или `FAIL` |
| `scope` | Ручная проверка CSV: bounded burst, отсутствие overlap HIN/LIN, отсутствие PWM после terminal state | `PENDING`, затем `PASS` или `FAIL` |
| `final` | Совместный итог | `PASS` только при `automation=PASS` и `scope=PASS`; до того `PENDING` |

`automation=PASS` возможен только при всех условиях ниже:

```text
@MC:ARM rc=0
@MC:RUN rc=0
state=FAULTED (5)
term=-12 (MAP_CAPTURE_LIMIT_EXCEEDED)
detail=7 (VBUS_LOW)
adc_status=7 (WINDOW_INVALID)
raw_vbus <= 9
0 <= vbus_mv < 1000
|i1_ma| <= 10000 и |i2_ma| <= 10000
frames=dropped=avail=0
@MC:DRAIN:records=0
нет строки @MC:REC
sigrok завершился с rc=0 и создал CSV
```

Все другие ситуации дают `automation=FAIL`. В частности, `term=-11`, `ADC_FRAME_NULL`, `ADC_STATUS_INVALID`, `I1_LIMIT`, `I2_LIMIT`, `VBUS_HIGH`, timeout, отсутствие новых полей или `raw_vbus=0` без `detail=VBUS_LOW` не являются доказательством no-HV VBUS gate. [1] [2]

## 2. Требования полного стенда

| Компонент | Нужен для реального запуска |
|---|---|
| Nucleo + ST-Link/SWD | Да; необходим для прошивки и UART Virtual COM Port |
| STEVAL и все штатные датчики тока/VBUS | Да; требуются для корректных ADC-калибровки, frame и VBUS evidence |
| Aux 3,3 V и проверенные SD1/SD2 high | Да |
| DC-link | Обе шины физически отключены и измерены `<1 V` |
| PC4 | Нет имитатора и нет внешнего VBUS |
| Логический анализатор `fx2lafw` / Saleae и sigrok-cli | Да; подключён к D0…D11 и обнаружен `--scan-sigrok` |

**Текущий ПК-1 с одной Nucleo, без STEVAL/датчиков/логического анализатора — NO-GO для реального test № 2.** На такой конфигурации разрешены только offline-проверки, сборка, прошивка default-deny образа и диагностика ST-Link/UART. Команда `mapcap run` не выполняется.

## 3. Подготовка временного образа на полном стенде

Перед тестом оператор вручную собирает и прошивает временный diagnostic образ:

```powershell
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1"
make flash
```

Этот образ существует только для test № 2. `SYNT`/`OEW_HOST_TEST` запрещены для Stage A с DC-link 60 V. [3]

## 4. Проверки без стенда

```powershell
py -3 -m pip install pyserial
py -3 tools\bench_test2_capture.py --dry-run
py -3 tools\bench_test2_capture.py --list-ports
py -3 tools\bench_test2_capture.py --scan-sigrok
py -3 -m py_compile tools\bench_test2_capture.py
py -3 -m pytest tests\test_bench_test2_capture.py -q
```

Если `--scan-sigrok` не обнаружил анализатор, реальный запуск запрещён. Сначала устраните USB/driver/path проблему; не пытайтесь использовать UART evidence вместо цифрового захвата.

## 5. Реальный запуск на полном стенде

Укажите **UART Virtual COM Port MCU**, а не просто любой COM-порт Windows. Если `COM15` принадлежит ST-Link VCP и по нему виден prompt прошивки, используйте `COM15`; иначе выберите фактический UART MCU из `--list-ports`.

```powershell
py -3 tools\bench_test2_capture.py `
  --port COM15 `
  --confirm-dc-link-disconnected `
  --confirm-pc4-zero `
  --confirm-sd-high `
  --confirm-sigrok-connected
```

Скрипт запускает sigrok **до** `mapcap run`, затем вместо фиксированного `sleep(1.0)` выполняет `mapcap status` каждые 50 мс до terminal state. Абсолютный timeout 1,0 с является FAIL. Не меняйте эти параметры без причины, зафиксированной в протоколе.

## 6. Артефакты

Каталог `campaign_raw\test2_nohv_<UTC>\` содержит:

| Файл | Назначение |
|---|---|
| `metadata.json` | Аргументы, profile ID и путь sigrok-cli |
| `uart.log` | Boot, TX, RX и фоновые UART-данные без очистки буфера |
| `sigrok_digital.csv` | Захват D0…D11 для ручной scope-приёмки |
| `sigrok_stdout.log`, `sigrok_stderr.log` | Диагностика sigrok-cli |
| `summary.json` | `automation/scope/final`, подробные checks, terminal-poll и ссылки на evidence |

По умолчанию `f` не отправляется. Даже при `automation=PASS` latch остаётся для ручного расследования. Флаг `--clear-fault-after-evidence` допустим только после согласованной процедуры очистки и фиксирует также `p?`/`pdump` после `f`.

## 7. Действия по вердикту

| `automation` | `scope` | Действие |
|---|---|---|
| `FAIL` | `NOT_APPLICABLE` | Не очищать fault автоматически; сохранить evidence и расследовать конкретный failed check. |
| `PASS` | `PENDING` | Просмотреть CSV, заполнить ручной протокол и проверить безопасное выключение PWM. |
| `PASS` | `PASS` | `final=PASS`; сохранить evidence, вернуть generic default-deny образ. Это всё ещё **не** допуск к Stage A. |
| `PASS` | `FAIL` | `final=FAIL`; остановить кампанию. |

## References

[1]: ../src/map_capture.h
[2]: ../src/map_capture.c
[3]: ../src/map_capture_profiles.c

---

**Граница безопасности:** даже `final=PASS` в test № 2 не разрешает Stage A, 60 V, `MAP_READY`, FOC или ручное открытие control admission.
