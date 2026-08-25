# `bench_test2_capture.py` — ПК‑3, UART + sigrok для test № 2

Скрипт автоматизирует **сбор доказательств** no-HV test № 2 MapCapture на ПК‑3. Он открывает UART, сохраняет полный неизменённый лог, запускает цифровой capture через `sigrok-cli`, посылает строго ограниченную последовательность диагностических команд и создаёт `summary.json` с PASS/FAIL.

> Скрипт **не прошивает МК, не включает источник DC-link и не выполняет FOC/V/f/autotune**. Прошивка временного diagnostic образа и физический no-HV preflight остаются отдельными обязательными действиями оператора. Реальный запуск заблокирован, пока оператор явно не подтвердит: DC-link отключён, PC4=0 и SD1/SD2 high.

Рабочая стендовая процедура и ручной шаблон протокола находятся в пакете документации test № 2. Если он ещё не принят в `main`, используйте его из ветки `ai4/bench-pc3-test2-docs`.

## 1. Что автоматизируется

| Действие | Поведение скрипта |
|---|---|
| UART evidence | Сохраняет до-командные данные, каждую TX-команду и все полученные RX-строки в `uart.log`; входной буфер намеренно не очищается. |
| UART preflight | Выполняет `sysinfo`, `p?`, `pdump`, `a`, `c`, `enc`, `mapcap status`. Он требует offsets-строку от `c`, `err=0` от `enc` и начальный `state=0`. |
| MapCapture | Выполняет только `mcarm=1398361684`, `mapcap status`, `mapcap run`, `mapcap status`, `mapcap drain`, `p?`, `pdump`. |
| Sigrok | Запускает цифровой capture `fx2lafw` на D0…D11, по умолчанию 8 MHz и 1,5 s; сохраняет CSV и stdout/stderr sigrok. |
| Вердикт | PASS только при `arm rc=0`, `run rc=0`, `state=5`, `term=-11` **или** `-12`, `frames=0`, `records=0`, отсутствии `@MC:REC` и успешном создании CSV. |
| Fault | При FAIL автоматически **не** посылает `f`. При PASS `f` возможна только с явным `--clear-fault-after-evidence`; её результаты логируются. |

Terminal `-11` допустим при raw VBUS 0–1, когда ADC отбрасывает нижнюю границу. Terminal `-12` допустим при raw VBUS ≥2, но VBUS остаётся ниже 1000 mV. Любой `term=0`, `state=COMPLETE`, `records>0` или иной terminal status — FAIL. [1] [2]

## 2. Требования ПК‑3

| Компонент | Требование |
|---|---|
| Python | Python 3.10+ с `py` launcher или аналогичным `python` |
| UART | `pyserial`: `py -3 -m pip install pyserial` |
| Логический анализатор | Saleae Logic / fx2lafw, подключён к USB и доступен sigrok-cli |
| sigrok-cli | `SIGROK_CLI_PATH`, `C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe`, `tools\sigrok-cli\sigrok-cli.exe` или PATH |
| Каналы | По умолчанию D0…D11 — все 12 PWM-линий двух инверторов по штатной карте каналов |
| Физика | DC-link на обеих шинах <1 V и отсоединён; PC4 без имитатора; aux 3,3 V подан; SD1/SD2 high |

До реального запуска оператор вручную собирает и прошивает **временный** diagnostic образ:

```powershell
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1"
make flash
```

Этот образ только для test № 2. `SYNT` и `OEW_HOST_TEST` запрещены в Stage A с DC-link 60 V. [3]

## 3. Быстрая проверка без стенда

Скрипт можно проверить до подключения оборудования. Это не открывает COM-порт и не запускает sigrok:

```powershell
py -3 tools\bench_test2_capture.py --dry-run
```

Проверка COM-портов:

```powershell
py -3 tools\bench_test2_capture.py --list-ports
```

Проверка обнаружения анализатора:

```powershell
py -3 tools\bench_test2_capture.py --scan-sigrok
```

Если `--scan-sigrok` не видит устройство, реальный тест не запускать. Закройте Saleae Logic 2, проверьте USB/драйвер и повторите scan; используйте `--sigrok-cli <путь>` или `SIGROK_CLI_PATH`, если сигrok установлен не в стандартном месте.

## 4. Реальный запуск test № 2

После ручного preflight, запуска диагностического образа, DMM-проверки обеих шин и подтверждения SD high выполните одну команду. Замените `COM4`, если MCU назначен другой порт.

```powershell
py -3 tools\bench_test2_capture.py `
  --port COM4 `
  --confirm-dc-link-disconnected `
  --confirm-pc4-zero `
  --confirm-sd-high
```

По умолчанию папка evidence создаётся как:

```text
campaign_raw\test2_nohv_<UTC timestamp>\
```

Если нужна заданная папка:

```powershell
py -3 tools\bench_test2_capture.py `
  --port COM4 `
  --output-dir campaign_raw\test2_nohv_20260825_1500 `
  --confirm-dc-link-disconnected `
  --confirm-pc4-zero `
  --confirm-sd-high
```

Флаг `--clear-fault-after-evidence` добавляйте только после того, как вы согласовали штатную очистку fault и хотите автоматически записать результат `f`, `p?`, `pdump`. Без флага fault остаётся защёлкнутым для ручной проверки — это безопасный default.

## 5. Артефакты и заполнение протокола

| Файл | Содержимое |
|---|---|
| `metadata.json` | Аргументы запуска, profile ID, подтверждения безопасности, путь sigrok-cli, время старта |
| `uart.log` | Непрерывный UART evidence: boot, TX, RX и фоновые сообщения |
| `sigrok_digital.csv` | Цифровой capture D0…D11 |
| `sigrok_stdout.log` / `sigrok_stderr.log` | Результат sigrok-cli |
| `summary.json` | Машиночитаемый verdict, parsed status, checks, command responses и пути evidence |

После запуска перенесите фактические значения и пути артефактов в шаблон протокола test № 2. CSV должен быть просмотрен человеком: скрипт проверяет наличие файла и завершение sigrok, но итоговая оценка «PWM выключен после terminal state» требует просмотра trace оператором.

## 6. Изменение параметров capture

| Параметр | Default | Когда менять |
|---|---:|---|
| `--sigrok-channels` | `D0,…,D11` | Только если фактическое подключение ЛА отличается от утверждённой карты каналов. |
| `--sigrok-rate-hz` | 8 000 000 | Не повышать для `fx2lafw`; это практический максимум проекта. |
| `--capture-seconds` | 1.5 | Увеличить, если нужно больше фонового времени; не нужно для самого bounded burst. |
| `--capture-warmup-seconds` | 0.30 | Увеличить, если конкретный ПК/анализатор требует больше времени до начала захвата. |
| `--sigrok-cli` | auto-detect | Указать явный путь при нестандартной установке. |

## 7. Результаты и действия

| Результат `summary.json` | Значение | Следующее действие |
|---|---|---|
| `PASS` | Целевой no-HV VBUS gate доказан по UART; CSV создан | Сохранить evidence, вручную проверить trace, заполнить протокол; затем вернуть generic default-deny образ. |
| `FAIL` до `mapcap run` | Preflight/arm не прошёл | Не обходить гейт. Сохранить лог и разбирать причину. |
| `FAIL` после `mapcap run` | Нецелевой terminal/PWM/sigrok результат | Fault остаётся защёлкнутым по умолчанию; остановить test и сохранить все evidence. |
| `FAIL` из-за sigrok | Нет CSV или ненулевой exit status | Не считать UART PASS достаточным; исправить sigrok и повторить test № 2. |

## 8. Offline-проверки для разработчиков

```powershell
py -3 -m py_compile tools\bench_test2_capture.py
py -3 -m pytest tests\test_bench_test2_capture.py -q
```

Тесты не требуют COM-порта, sigrok-cli или платы: они проверяют парсинг firmware-строк и то, что `records>0`/`MAP_CAPTURE_COMPLETE` при PC4=0 не могут дать PASS.

## References

[1]: ../src/map_capture.c
[2]: ../src/adc.c
[3]: ../TZ_MAP_CAPTURE_PROFILE.md

---

**Граница безопасности:** скрипт служит только для no-HV test № 2. Его PASS не разрешает Stage A, 60 V, `mapcap build`, `MAP_READY`, FOC или ручное открытие admission.
