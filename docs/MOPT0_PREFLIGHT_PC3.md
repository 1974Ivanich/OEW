# Pre-flight ПК-3 перед первым физическим `M0-R1` (M-OPT-0)

Область: **read-only** проверка стенда и образа. Pre-flight **не** армит и **не**
запускает MapCapture, **не** подаёт DC-link, **не** выполняет FOC/V/f/autotune и
**не** заменяет ручной осмотр оператора. Он только читает UART и доказывает, что
все гейты перед первым прогоном выполняются.

Реализация: `tools/mopt0_capture.py preflight` (ветка `ai5/m-opt-0-preflight`).
Физические `M0-R1…R5` **не считаются начатыми**, пока нет pre-flight PASS и пяти
raw UART-логов.

## 1. Образ

| Параметр | Значение |
|---|---|
| source (merge M-OPT-0 в main) | `80fe8b263e3178e9b0193bf3aaa3809cd0a9765a` (merge `579b9806`, интеграция `4028ba2`) |
| production `firmware.bin` sha256 | `c23591818be408840da33d49eb7fdc0f0eef46646c0dfc7cbfec17a833cedcc9` |
| размер | 70932 text / 516 data / 11524 bss / 82972 dec |
| CI | main `579b9806` success (ран 35337783031) |

Образ обязан быть собран из принятого `main`, а `firmware.bin` — сверен по SHA-256
**до** прошивки:

```powershell
Get-FileHash build\firmware.bin -Algorithm SHA256
# ожидаем: c23591818be408840da33d49eb7fdc0f0eef46646c0dfc7cbfec17a833cedcc9
```

## 2. Что проверяет pre-flight (автоматически)

| № | Требование | Проверка в отчёте |
|---|---|---|
| 1 | firmware SHA зафиксирован | `firmware_sha256` (считается из `--firmware-bin`) |
| 2 | identity берётся **из firmware**, не из скрипта | `identity.run_id`, `identity.map_id`, `identity.map_crc32` |
| 3 | `map_id = M0` | `preflight_map_id_expected` |
| 4 | `map_crc32` присутствует (фиксируется) | `preflight_map_crc32_present` |
| 5 | `@RUN:ID ACK` точный | `run_id_ack` (`@RUN:ID=<id>` в **прямом** ответе на `run=<id>`) |
| 6 | телеметрия через checked sender | `preflight_telemetry_counters_reported` (`uart_drp`/`uart_trunc` видны) |
| 7 | `uart_trunc = 0` | `preflight_uart_trunc_zero` |
| 8 | `MOE = 0` до допуска | `moe_low` |
| 9 | default-deny удержан | `default_deny_hold` |
| 10 | `mapcap = IDLE` и пусто | `mapcap_idle_empty_before` (`state=term=frames=dropped=avail=0`) |
| 11 | MapCapture не запускался вообще | `preflight_capture_never_started` (нет `@MC:REC:`) |
| 12 | калибровка валидна | `calibration_ok` (нет `@ADC:CAL:FAIL`) |
| 13 | энкодер валиден | `encoder_err_zero` |
| 14 | no-HV VBUS gate | `no_hv_gate` (`median(raw_vbus) ≤ 9` **и** `max ≤ 200`, I1/I2 не на рельсах) |
| 15 | целостность лога | `log_integrity` (нет `line overflow`/`@UART:TRUNC`/чужого `run_id`) |
| 16 | DC-link / PC4 / SD | `--confirm-dc-link-disconnected`, `--confirm-pc4-zero`, `--confirm-sd-high` (оператор) |

## 3. Команды (PowerShell, ПК-3)

```powershell
# 1. Найти порт UART MCU (не угадывать)
py -3 tools\mopt0_capture.py run --list-ports

# 2. Pre-flight (папка должна быть новой)
py -3 tools\mopt0_capture.py preflight `
  --port COM15 `
  --campaign D:\campaign_raw\mopt0_preflight_20260918Z `
  --firmware-bin build\firmware.bin `
  --run-id M0-R1 `
  --confirm-dc-link-disconnected --confirm-pc4-zero --confirm-sd-high
```

Перед этим физически: **оба плеча DC-link отключены и < 1 В по DMM**, PC4 без
внешнего VBUS, SD1/SD2 высокие, полный STEVAL+датчики, нулевой шум тракта
измерен по принятой методике.

## 4. Ожидаемый результат

Успех (exit 0):

```json
{"mode": "PHYSICAL", "status": "PASS", "run_id": "M0-R1", "failed": [],
 "uart_health": {"uart_trunc": 0, "uart_drp": 0, "reported": true}}
```

Отчёт целиком — `<campaign>\preflight.json` (все checks, identity, no-HV гейт с
числами, `command_sequence`, `operator_confirmations`, артефакты, `uart.log`).

Статусы:

| Статус | Значение | Что делать |
|---|---|---|
| `PASS` | все гейты, identity и целостность доказаны | можно начинать `M0-R1` |
| `BLOCKED` | нет firmware-identity или нет счётчиков checked sender | **не начинать**; проверить, что прошит образ из принятого `main` |
| `FAIL` | нарушен no-HV/калибровка/энкодер/целостность/`uart_trunc > 0` | **не начинать**; устранить причину, сохранить отчёт как есть |
| `SIMULATED` | офлайн-прогон оркестрации | это **не** физический pre-flight |

## 5. Стоп-условия (немедленно остановиться)

- `default_deny ≠ 1` или `MOE ≠ 0`;
- `median(raw_vbus) > 9` или `max > 200` (шина не в no-HV);
- I1/I2 на рельсах (0 или 4095), `@ADC:CAL:FAIL`, `enc err ≠ 0`;
- `uart_trunc > 0` (пакет `@FOC` отбрасывался — поток evidence с дырой);
- любой `@MC:REC:`/arm/run, неожиданный fault или reset;
- неверная board identity, потеря связи, запах/нагрев/шум, любое сомнение
  оператора. Fault не очищается автоматически.

При любом `BLOCKED`/`FAIL` результат сохраняется **как есть**: без подмены,
повторов «до зелёного» и без автоматической замены запуска.

## 6. Только после pre-flight PASS

1. `M0-R1` … `M0-R5` через `tools/mopt0_capture.py run` (каждый прогон — своя
   папка и свой raw UART-лог, `run=<id>` выставляется инструментом);
2. гейты кампании: `map_id_identical`, `map_crc32_identical`, `uart_truncation_zero`,
   `run_id_ack`, `no_hv_gate`, `log_integrity`;
3. офлайн-верификация кампании: `py -3 tools\mopt0_capture.py verify --campaign <папка>`;
4. архивация evidence — существующим механизмом кампаний.

## 7. Тесты режима

`tests/test_mopt0_capture.py` (43 pytest): PASS на готовом образе, BLOCKED без
счётчиков checked sender, FAIL при `uart_trunc > 0`, BLOCKED на чужой `map_id`,
BLOCKED при отсутствии `@FOC`-identity и при неотвеченном `run=<id>`,
обязательность `--firmware-bin`, обязательность `--port` и `--confirm-*`,
запрет перезаписи папки, simulated-прогон никогда не «физический» PASS.
