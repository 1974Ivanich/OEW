# Прошивка и отладка через OpenOCD + ST-Link (схема софта)

## Стек

- **Програматор/отладчик:** ST-Link/V3 на Nucleo-G474RE (SWD, SWO).
- **OpenOCD 0.12** (`../toolchain/openocd/xpack-openocd-0.12.0-7/bin/openocd.exe`
  — каталог рядом с репозиторием, вне git): сборка xPack на стеке DAP — интерфейс `stlink.cfg` использует
  драйвер `st-link` (dapdirect), HLA в этой сборке не собран, поэтому транспорт
  **`swd`** (не `hla_swd`).
- **Конфиг:** `openocd.cfg` (корень): `interface/stlink.cfg` +
  `target/stm32g4x.cfg`, `adapter speed 4000`, `reset_config srst_nogate`
  (наследуется; SRST на Nucleo не разведён — сброс по SYSRESETREQ).
- **GDB:** `arm-none-eabi-gdb` из ARM GNU Toolchain (уже в `toolchain/`);
  сценарий — [.gdbinit](../../.gdbinit): `load`, `reset halt`, `break main`,
  `continue`; `G` — отсоединиться и дать цели работать, `q` — выйти (цель
  продолжает работать).

## Цели Makefile

| Цель | Что делает |
|---|---|
| `make flash` | = `flash-openocd`: `program build/firmware.bin verify 0x08000000 exit` |
| `make flash-openocd-elf` | то же из ELF (addr снимается с секций) |
| `make openocd-server` | сервер GDB RSP `localhost:3333` + telnet `4444` |
| `make debug` | сервер + `arm-none-eabi-gdb -q -ex "target remote …" -x .gdbinit` |
| `make flash-cubeprog` | прежний путь через STM32_Programmer_CLI (если установлен) |

Порты/адрес: `OEW_GDB_PORT` (3333), `OEW_GDB_TARGET` (`localhost:PORT`).
Остановка сервера: Ctrl+C или `telnet localhost 4444` → `shutdown`.
В Windows-проводнике: `flash.bat` (OpenOCD), `debug.bat` (OpenOCD + GDB).

## Связь с HWT и backup

- **HWT (`make hwt-run`) не программирует цель.** Прошивка — только `make flash`;
  HWT сверяет образ на цели с ELF, запускает сценарии и даёт вердикт
  (ERROR ≠ PASS). Транспорт — тот же RSP-сервер OpenOCD (порт из
  `tests/target/stand.toml*`, по умолчанию 3333). Исключительный доступ к
  адаптеру — через lock (`%TEMP%/hwt_stlink.lock`).
- **`make backup`** (`tools/hwt_backup.py`) — дамп Flash через SWD + Intel HEX +
  SHA-256; перед рискованным экспериментом — сначала backup.
- `tools/hwt.py` находит OpenOCD в таком порядке: PATH → портативный
  `toolchain/openocd/xpack-openocd-*/bin/openocd.exe` → системный
  `C:\Program Files\OpenOCD-*`.

## Безопасные практики

- Прошивка и HWT — взаимоисключающие операции: не запускать параллельно
  (адаптер один; lock защищает HWT-прогоны, `make flash` lock не берёт).
- Необратимые настройки цели (опционные байты, RDP) через OpenOCD не менять.
- OpenOCD не трогает protection-код прошивки: SD→BKIN, default-deny
  (OEW_MAP_CAPTURE/OEW_MAP_L3) — состояние цели, а не инструмента.

## Проверка без железа

Проверено на ПК-2 без платы. OpenOCD xpack 0.12.0-7 лежит рядом с
репозиторием: `../toolchain/openocd/xpack-openocd-0.12.0-7/` (вне git);
PATH — через `../toolchain/env.sh` или `env.bat`.

- `openocd --version` — 0.12.0+dev-02228 (сборка со `st-link`/DAP);
- `openocd -f openocd.cfg` — конфиг разобран, порты 6666/4444 подняты, дальше
  `Error: open failed` и **rc=1**: ST-Link на ПК-2 нет, это отсутствие цели,
  а не ошибка конфига;
- `make flash` — доходит до `OpenOCD init failed` (адаптер), а не до
  «openocd: команда не найдена»;
- `tools/hwt.py doctor` — `openocd` найден, `doctor: OK`; `make test-hwt` — 48 passed.

Запись во Flash на цели — только на стенде.

## История

- До 2026-10: прошивка только через STM32CubeProgrammer CLI
  (`C:\ST\STM32CubeCLT_1.22.0\...`); OpenOCD не был установлен.
- 2026-10: OpenOCD добавлен портативно; `make flash` переведён на OpenOCD,
  CubeProgrammer оставлен как `flash-cubeprog`.
