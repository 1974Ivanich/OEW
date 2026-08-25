# BENCH: MapCapture fail-closed проверка (шаг №1, без силовой части)

**Дата:** 2026-08-24 · **main:** `f8cd2e2` (mapcap-uart-export принят) · **ПК-2 (подготовка)**
**Цель:** подтвердить, что без board-qualified профиля канал MapCapture не может
начать захват: `mcarm` / `mapcap run` / `drain` / `status` / `build` ведут себя fail-closed.
**Ограничения:** НЕ подавать силовое напряжение (DC-link отключён). Плата — только от
логического питания. Реального двигателя нет. Никакой energise-кампании.

## Прошивка (ВАЖНО)

Команды `mcarm`/`mapcap` компилируются **только** в commissioning-сборке
(`#if OEW_MAP_CAPTURE` в `main.c`, `cli_mapcap_command`). На production-образе этих
команд нет — строка уйдёт в fallthrough. Поэтому для сессии нужна commissioning-сборка:

```bash
make clean && make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7"
```

Проверено на ПК-2 из `f8cd2e2`: `Build complete` (text 74556, firmware.bin 75080 байт).
Прошивка: `make flash` (ST-Link). UART: **115200 8N1** (USART2, `BRR = PCLK1/115200`).

## Сессия (ожидаемые ответы — из кода `main.c:444-455`)

| Команда | Ожидаемый ответ | Критерий |
|---|---|---|
| `mcarm=0` | `@MC:ARM:BLOCKED:PROFILE` | PASS: `BuildRequest`→false для любого id |
| `mcarm=1` | `@MC:ARM:BLOCKED:PROFILE` | то же |
| `mcarm=1398361684` (0x53594E54 «SYNT») | `@MC:ARM:BLOCKED:PROFILE` | PASS: synthetic в FW недоступен (host-only) |
| `mcarm` (без числа) | `err: mcarm=<profile_id>` | формат |
| `mapcap run` | `@MC:RUN:rc=-14` | PASS: `MAP_CAPTURE_NOT_ACTIVE` (-14), не armed |
| `mapcap drain` | `@MC:DRAIN:records=0` | PASS: буфер пуст |
| `mapcap status` | `@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0` | PASS: IDLE, не armed |
| `mapcap build=0` | `@MAP:BUILD:BLOCKED:CAPTURE_STATE=0:TERM=0:AVAILABLE=0` **или** `BLOCKED:FAULT` (если на обесточенном стенде залип fault Vbus/ток) | PASS: любой `BLOCKED` |

## PASS-критерии (главные)

1. **Ни одного** ответа вида: `@MC:ARM:cap=…`, `@MC:RUN:rc=0`, `@MC:REC:…`,
   `@MC:DRAIN:records>0`, `@MAP:BUILD:…` без `BLOCKED`.
2. На выходе инвертора **нет импульсов PWM** (осциллограф/логический анализатор) —
   service PWM не запускался, мост не открывался.
3. Все команды вернули ожидаемый отказ (таблица выше).

## Фиксация

- Сохранить полный лог сессии в `campaign_raw/uart.log` (позже он понадобится как
  исходник) или `docs/bench/`.
- По желанию: `dump` / `pdump` / `sysinfo` — зафиксировать PSC/ARR/BDTR/такты
  (пригодится для шага №2 — сверка `ccr1/ccr8/arr` в `@MC:REC` с фактическим PWM).

## Следующий шаг (после PASS)

Шаг №2 — synthetic transport test: локальная стендовая сборка с
`-DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1` (обеспечивает одобренный
synthetic profile ТОЛЬКО в этой сборке; `OEW_HOST_TEST` нигде кроме
`map_capture_profiles.c` не используется — побочных эффектов нет). Только после
этого — Stage A (60 В DC-link, токоограничение, approved profile, осциллограф).

## Результат сессии (2026-08-24, ПК-3, main@8773492, commissioning-образ, no-HV)

| Команда | Ответ | Вердикт |
|---|---|---|
| `mcarm=0` | `@MC:ARM:BLOCKED:PROFILE` | PASS |
| `mcarm=1` | `@MC:ARM:BLOCKED:PROFILE` | PASS |
| `mcarm=1398361684` | `@MC:ARM:BLOCKED:PROFILE` | PASS |
| `mapcap run` | `@MC:RUN:rc=-14` | PASS |
| `mapcap drain` | `@MC:DRAIN:records=0` | PASS |
| `mapcap status` | `@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0` | PASS |
| `mapcap build=1` | `@MAP:BUILD:BLOCKED:CAPTURE_STATE=0:TERM=0:AVAILABLE=0` | PASS |

**7/7 PASS.** Главные критерии подтверждены: ни одного `@MC:ARM:cap=`, `@MC:RUN:rc=0`,
`@MC:REC:`, `@MC:DRAIN:records>0`. Плата возвращена на generic-сборку
(`make clean && make` + flash), commissioning-образ на плате не оставлен.
