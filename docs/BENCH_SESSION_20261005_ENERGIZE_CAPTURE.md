# Сессия ПК-3, 05.10.2026 — разбор H/W-break и возврат к energize-шагу

Продолжение [`docs/BENCH_SESSION_20261004_MAPCAP_PROFILE_BLOCKED.md`](BENCH_SESSION_20261004_MAPCAP_PROFILE_BLOCKED.md)
после аварии 05.10 (незаказанное срабатывание break на SD2/TIM8 при подаче звена).

**Итог одной строкой:** плата восстановлена (латч снят, шаг 0 снова `PASS 29/29`),
выполнены **два успешных energize-захвата** (16 кадров при 32.5 В, ни одного fault),
инструмент комиссионинга исправлен (4 дефекта, из них один fail-open);
**скан 48 ячеек по-прежнему заблокирован** — BOARD-профиль намеренно отклоняет
захват без offline-recon (`@MAP:BUILD:ERROR:QUALIFICATION`), поэтому `@MAP:READY` недостижим.

## 1. Разбор инцидента (улики — в evidence-пакете)

Пакет: [`docs/evidence/incident_20261005_hwbreak/`](evidence/incident_20261005_hwbreak/incident.md)
(+ 10 сырых логов 01–10).

* Латч был `FAULT=1 FAULT_R=18` = `PROTECT_FAULT_HARDWARE_BREAK`, источник TIM8/SD2,
  `@BRK:valid=1:seq=1`. PWM уже обесточен (`CEN=0`, `CCER=0`, `MOE=0`).
* Тока не было: `I1/I2` на уровне калибровочных нулей. Значит это **импульс на входе
  SD2 в момент подачи звена**, а не перегрузка по току. Все логи до подачи звена
  (01–05) чистые.
* Латч снят по решению владельца: `f` → `fault cleared`, `breakdiag reset` → `rc=0`,
  `@BRK:valid=0`; повторный read-only шаг 0 → `PASS 29/29`, `rc=0`.

## 2. Дефекты инструмента, найденные при возврате к energize

| код | суть | исправление |
|---|---|---|
| **B20** | гейт VBUS читал `@FOC:VBUS`, который в покое печатает **0** (`ADC_FRAME_NOT_ARMED`, F12). Ложный «STOP: VBUS=0» при живом звене 32.5 В; и обратное — проверка «калибровку делать только при снятом звене» в `calib` всегда проходила | `read_vbus_mv()` читает сырой кадр `a` (`@ADC:…:VBUS=<raw>`, шкала 1:125, VREF 3300, 12 бит), как `tools/bench_inject_check.py`; `capture`/`calib`/`status` переведены на него |
| **B21** | `mapcap build` **потребляет** записи буфера (`main.c:372`), поэтому после `drain` даёт `AVAILABLE=0`. Порядок в старом `incident.md` (2026-10-04) был неверным | порядок `build → drain` (build потребляет, drain — страховочный); исправлено в инструменте и в документе |
| **B22** | BOARD-профиль fail-closed: `src/map_capture_profiles.c:429-433` → `recon->valid=false` ⇒ `@MAP:BUILD:ERROR:QUALIFICATION`. Нигде не задокументировано | инструмент явно печатает `FAIL-CLOSED: offline recon absent (documented)`, а не «ошибка порядка»; описано здесь и в evidence |
| **B23** | **fail-open**: `first_line()` брал незавершённый хвост буфера, `gate_foc` видел `FAULT=None` → `to_int(None,0)=0` → считал состояние здоровым. Проверка латча могла не сработать | кадры распознаются только по завершённым строкам (`\r?\n`); отсутствие `FAULT/FAULT_R/RUN` = **отказ** (fail-closed). Живое подтверждение: status печатает `FAULT=0 FAULT_R=0 RUN=0`, а не `None` |

Тесты: `tests/test_pc3_commission.py` — **14 passed** (в т.ч. порядок `build` до `drain`,
недоверие обрезанному кадру, отказ `capture` на неполном `@FOC`).

## 3. Энергированные захваты (живые)

| прогон | команда | результат |
|---|---|---|
| #1 | BOARD id `1112490322` (арм) → `mcrun` → `build` → `drain` | `@MC:ARM:cap=2:rc=0:offsets_valid=1`; `state=3:term=0:frames=8:dropped=0` (**COMPLETE**); drain 8 записей: VBUS 320–325 (32.2–32.7 В), `i1` ≤294 мА, `i2` ≤767 мА, `fault=0`; `@BRK:valid=0` |
| #2 | `cap=3` → `build` → `drain` | то же `state=3:frames=8`; `mapcap build` → `@MAP:BUILD:ERROR:QUALIFICATION` (fail-closed); drain 8 записей |

Сырые логи: evidence `06_…`, `07_…`, `08_…`, `09_…`. Финальное состояние платы:
`FAULT=0 FAULT_R=0 RUN=0 VBUS=32536 mV em_stop=1/1`, `MOE=0`, `CCER=0`, `CEN=0`,
`CCR1..3=500`, `@BRK:valid=0`.

## 5. Публикация и CI

* Ветка `ai3/focscan-48cell-v2` опубликована: `git push` + подтверждение через `git ls-remote`
  → remote SHA `0ba78cb69b5cc2d6f7f5966a4f26e4a19695d695` (совпал с локальным).
* CI `build-test` — **зелёный** (run [37234326497](https://github.com/1974Ivanich/OEW/actions/runs/37234326497)).
* Локальная проверка перед коммитом: `tests/test_pc3_commission.py` → 14 passed;
  полный `py -3 -m pytest tests -q` при `PYTHONUTF8=1` → **677 passed, 2 skipped**
  (warning — известное cp1251-декодирование в HWT-наборе на Windows-хосте, не связано с пакетом).
* **Образ прошивки не менялся** (правки — только Python-инструмент, тесты и документы),
  поэтому `make`/`make flash` не требовались; состояние платы сверено живым read-only
  `status` (`FAULT=0 FAULT_R=0 RUN=0 VBUS=32536 mV`, `MOE=0`, `@BRK:valid=0`, `state=3`).

## 6. Блокер и что дальше

`@MAP:READY` (карта) этим путём недостижим: BOARD-профиль требует recon-коэффициенты,
которых у стенда нет (offline characterization не проводилась). Следствия:

* `@FOC:START` (и скан 48 ячеек) без карты вернёт `rc=-2 map_unverified` — запускать нельзя.
* Варианты: (a) offline-characterization → recon → `mapcap build`; (b) `mapload <994 hex>`
  внешним артефактом под identity `board=7:acs=0x26B9B97B:ccs=0x13552B12`.
* Владельцу решать: идти в offline-recon или принять захваты как улику и не запускать скан.
