# Инцидент 2026-10-05: аппаратный break (TIM8/SD2) при подаче DC-link 24–36 В, при выключенном PWM

Плата: STM32G474RE, COM4 `STMicroelectronics STLink Virtual COM Port` @115200.
Образ: кампанийный BOARD этой ветки, sha256 `6bb2a31459050d9e9442ab5205dd27988bde99fda916d6c6b30840a5b85a76ce`
(дамп после прошивки `backup/flash_20261005_000056.bin` совпал побайтово, класс BOARD, rc=0).
Роль: первая попытка комиссионинга карты (`capture --allow-energize`) на звене 24–36 В.

## Хронология (каждая строка — из сырого лога)

| фаза | время (t, мс) | сырой лог | факт |
|---|---|---|---|
| после прошивки, звено снято | 0 … ~1 330 000 | `backup/bench_20261005/{01_status,02_params,03_calib,04_arm_boar,05_step0}.log` | **чисто**: ни `FAULT=1`, ни `@BRK:valid=1` нет ни в одном логе (проверено грепом по всем файлам прогона) |
| — | — | `03_calib.log`, `04_arm_boar.log`, `05_step0.log` | `@ADC:CAL:offset_i1=2039:offset_i2=2068`; `mcarm=1112490322` → `@MC:ARM:cap=1:rc=0`; шаг 0 пакета `29/29 OK` → PASS |
| **подача DC-link 24–36 В владельцем** | между 05_step0 и 06a | — | по подтверждению владельца (мотор подключён, вал свободен) |
| первый статус под напряжением | 1 377 900 (~22 мин 58 с после ресета) | `01_first_fault_under_power_raw.log` | **`@FOC:…FAULT=1:FAULT_R=18…VBUS=0`**; `@BRK:valid=1:seq=1:src=TIM8:cyc=0:sr=1,81:sd=1,1:bd=1CC0,1CC0:ce=0,0:cnt=0,0:cap=1,1,0`; `mapcap status` → `state=5:term=-16`; `p?` → `CR1=224:CCER=0:BDTR=7360:CNT=0` (MOE=0) |
| повторная проверка + сырой кадр | 1 425 800 | `02_fault_recheck_raw.log`, `03_break_event_raw.log` | `a` → `@ADC:I1=2040:I2=2070:Ires=1:VBUS=323`; `a?` → `offset_i1=2039:stream=0`; `enc` → `@ENC:angle=4623:speed=-1:period_us=896:pulse_us=254:err=0`; `sysinfo` → `OVR=0:JEOS=0:TO=0:JQOVF=0:uart_drp=0:uart_trunc=0` |

## Расшифровка (по исходникам этой же линии)

* `FAULT_R=18` = `PROTECT_FAULT_HARDWARE_BREAK` (последний элемент `ProtectFaultReason`, `src/protect.h:26`).
  `FAULT=1` — латч; снимается только явной командой `f` (`src/cli.c:285-304`), авто-реарма нет.
* `@BRK:valid=1:seq=1` — это **первый** break в сессии; снимок хранится до `breakdiag reset`
  (`src/cli.c:257-277`), поэтому до разбора события он и не сбрасывался.
* Поля снимка (`src/cli.c:264-273`): `sr=<TIM1_SR>,<TIM8_SR>` → `TIM1_SR=0x01` (только `UIF`,
  **BIF нет**), `TIM8_SR=0x81` (`BIF|UIF`) ⇒ источник — **TIM8**, то есть вход **SD2/PD2**
  (обработчик `TIM8_BRK_IRQHandler`, `main.c:154-160`).
* `sd=1,1` — линии SD1/SD2 **сейчас высокие**; `@FOC:em_stop1=1:em_stop2=1` согласовано.
  Значит break был **импульсным** (срыв при подаче питания), а не постоянным уровнем.
* `bd=1CC0,1CC0` — `BDTR` обоих таймеров (`MOE=0`): MOE снят аппаратно самим break-входом
  (не по программе), `BKE=1`, `BKP=0` (активный уровень SD — низкий). `ce=0,0` (`CCER=0`),
  `cnt=0,0`, `p?`/`pdump` → `CEN=0` — силовая часть обесточена.
* `term=-16` = `MAP_CAPTURE_PROTECTION_FAULT` (`src/map_capture.h:52`) — MC-автомат терминально
  остановлен защитным латчем; `frames=0:dropped=0:avail=0` ⇒ **burst не начинался**, `mapcap run`
  не выполнялся, PWM под током не был.
* **Питание подано и измеримо**: `@ADC:…:VBUS=323` (сырой код). Делитель 1:125 (`src/adc.h:20`):
  `323 · 3300/4095 · 125 ≈ 32.5 В` — согласуется с заявленными владельцем 24–36 В.
* **Роста тока не было**: `I1=2040:I2=2070` (raw) при живых нулях `2039/2068` ⇒ `idc ≈ 0`.
  По критерию проекта (`docs/pc3_bench_kit/.../FINDINGS_SESSION1_SIGN_BREAK_20260926.md`,
  `DESIGN_FAULTN_CHARACTERIZATION_20260927.md`): импульс на SD **без** предшествующего роста тока —
  это не перегрузка, а UVLO драйвера либо помеха.

## Класс события

Импульсный `FAULT_N` по **SD2 (PD2, TIM8)** при подаче питания на обесточенную логику — в проекте
уже разбирался как «ложный FAULT_N на STEVAL-2 (SD2/PD2)» (`FINDINGS_SESSION1_SIGN_BREAK_20260926.md`
§3, `DESIGN_FAULTN_CHARACTERIZATION_20260927.md`) и как аппаратный break при подаче HV в инциденте
04.10.2026 (тогда источник был TIM1/SD1 при 60 В, `docs/evidence/incident_20261004_hwbreak/`).
Отличие от 04.10: здесь (а) звено 32.5 В — в разрешённом окне пакета, (б) источник SD2, (в) нет
предшествующего роста тока, (г) PWM был выключен (`CEN=0`, `MOE=0`) на протяжении всего события.

## Ложный STOP гейта VBUS при разборе инцидента (предыстория находки B20)

`capture` (как и `calib`/`arm`) берёт VBUS из `@FOC:VBUS`, а в idle этот кадр **всегда** нулевой
(`ADC_FRAME_NOT_ARMED`, документированная особенность F12; см. также
`docs/evidence/incident_20261004_hwbreak/incident.md:101-103`). Поэтому
`capture --allow-energize --vbus-min 20000` **не может пройти гейт ни при каком питании** —
что и подтвердил живой прогон: на звене 32.5 В гейт увидел `VBUS=0` и отказал
(`STOP: VBUS < 20000 mV -> no capture`). Корректный источник — сырой кадр `a`
(`@ADC:…:VBUS=<raw>`), `мВ = raw · 3300 · 125 / 4095` (`VBUS_MV_PER_COUNT` —
как в `tools/bench_inject_check.py:99,106-108`); компенсация `offset_vbus` в этом образе в
power-состоянии недоступна (`cv` требует нуля, `src/cli.c:75-86`), поэтому погрешность ≈2 В
(для окна 24–36 В и порога 20 В — допустимо, но должно быть записано в отчёте).

## Штатный путь после разбора (порядок обязателен)

1. убедиться, что FOC/Vf не запущены (`RUN=0`, `p?` → `CCER=0`, `MOE=0`) и нули ADC живы
   (`a?` → `offset_i1=2039`) — тогда `|idc| ≈ 0 < PROTECT_I_MAX_MA/2 = 6000 мА`;
2. `f` → `PROTECT_RequestClear()` (при `rc==0` CLI сам калибрует нули, `src/cli.c:291`) → `FAULT=0:FAULT_R=0`;
3. `breakdiag reset` → `@BRK:RESET:rc=0`, снимок первого break очищается (baseline для шага 0);
4. повторный **read-only** шаг 0 (`TOOLS/check_step0.py`) — ожидание `PASS`, `@BRK:valid=0`;
5. гейт VBUS по сырому кадру `a` (после правки инструмента), затем возобновление energize-шага.

## Результат после восстановления (E0–E3, тот же сеанс)

| шаг | лог в этом каталоге | факт |
|---|---|---|
| снятие латча | `05_recovery_after_incident_raw.log` | `f` → `fault cleared`; `breakdiag reset` → `@BRK:RESET:rc=0`; `@BRK:valid=0`; `p?` → `CCER=0:BDTR=7360` (MOE=0) |
| повторный шаг 0 | `10_step0_after_recovery_report.txt` | `checks 29/29 OK`, `verdict PASS (rc=0)` |
| capture #1 (BOARD `1112490322`) | `06_capture_burst1_raw.log` | `$ a` raw=320; `@MC:ARM:cap=2:rc=0:offsets_valid=1:inj_start_rc=0`; `@MC:RUN:rc=0`; `@MC:STATUS:state=3:term=0:cap=2:frames=8:dropped=0:avail=8` (**COMPLETE/OK**); `@BRK:valid=0`; `p?` MOE=0 |
| 8 записей измерения | `07_drain_and_blocked_build_raw.log` | `@MC:DRAIN:records=8`: `raw_vbus` 320–325 (≈32.2–32.7 В), `i1` 38–294 мА, `i2` 140–767 мА, `frame.status=7`, `fault=0` |
| capture #2 (порядок build→drain) | `08_capture_burst2_build_raw.log` | `cap=3` → `state=3:frames=8`; `mapcap build=1112490322` → `@MAP:BUILD:ERROR:QUALIFICATION`; затем drain 8 записей |
| финальный статус | `09_final_status_after_capture.txt` | `FAULT=0:FAULT_R=0:RUN=0:VBUS=32536:em_stop=1/1`; `MOE=0`; `@BRK:valid=0`; сырой VBUS=323 |

Суммарно: **два энергированных service-burst'а под 32.5 В, 16 кадров измерений, ни одного
fault/break, нулевой рост тока**; `frames=8` и `term=0` в обоих.

## Находки инструмента (исправлены здесь же)

* **B20 — гейт VBUS читал не тот источник.** `@FOC:VBUS` в покое печатает 0
  (`ADC_FRAME_NOT_ARMED`, F12), поэтому `capture --vbus-min` и проверка
  «калибровка только при снятом звене» в `calib` работали неверно (первая — всегда
  отказ, вторая — всегда пропуск). Исправлено: `read_vbus_mv()` берёт сырой кадр `a`
  (`@ADC:…:VBUS=<raw>`, шкала 1:125, VREF 3300 мВ, 12 бит) — как в
  `tools/bench_inject_check.py`. `status` теперь печатает и сырой кадр, и предупреждение
  о нуле `@FOC`. Тесты: `tests/test_pc3_commission.py`.
* **B21 — порядок команд вокруг `build`.** `mapcap build=<id>` **потребляет** записи
  буфера (`MapCapture_ConsumeRecord`, `main.c:372`), поэтому вызов после `drain`
  отвечает `@MAP:BUILD:BLOCKED:CAPTURE_STATE=3:TERM=0:AVAILABLE=0` (живое
  подтверждение: `07_…`). Правильный порядок: `mcarm → run → status → build → drain`.
  Инструмент исправлен (build до drain, drain после build — catch-all, обычно 0 записей);
  добавлен тест на порядок.
* **B22 — недокументированный recon-барьер.** BOARD-профиль намеренно fail-closed:
  `src/map_capture_profiles.c:429-433` ставит `recon->valid = false`
  («coefficients are unknown until the offline characterization … deliberately rejected»),
  из-за чего `qualification_sane` ложно → `MapBuilder_Begin` → `@MAP:BUILD:ERROR:QUALIFICATION`
  (`main.c:356`). Ни в одном документе это не описано. Значит `@MAP:READY`
  (а с ним карта и `1`/скан 48 ячеек) **на этом стенде недостижим без offline-recon**;
  energize-захват при этом остаётся валидным измерением. Инструмент теперь печатает
  явное `FAIL-CLOSED: offline recon absent`, а не «FAIL порядка».
* **B23 — fail-open гейта на обрезанном кадре.** `first_line` брал незавершённый хвост
  буфера (непрерывная телеметрия), `gate_foc` получал `FAULT=None` и через
  `to_int(None, 0)` считал его здоровым — проверка латча могла не сработать. Исправлено:
  кадры распознаются только по завершённым строкам (`\r?\n`), отсутствие
  `FAULT/FAULT_R/RUN` = отказ (fail-closed). Живое подтверждение корректности: `09_…`
  (`FAULT=0 FAULT_R=0 RUN=0`, а не `None`).

## Состояние безопасности на момент фиксации инцидента (pre-recovery)


`p?` → `CR1=224:CCER=0:BDTR=7360` (CEN=0, MOE=0); `pdump` → T1/T8 `CR1=0xE0:CCER=0:BDTR=0x1CC0`;
`RUN=0`, `FAIL=0`, `CCR1..3=500` (середина, как ставит `PWM_Disable`), `em_stop1=1:em_stop2=1`;
`sysinfo` без `OVR/JEOS/TO/JQOVF` — плата в безопасном состоянии, звено подано владельцем.
Латч **сохранён на момент фиксации** (улика); сброс — по решению владельца.

## Осталось (за владельцем)
* **Скан 48 ячеек запускать нельзя.** Без верифицированной карты `@FOC:START=…`
  вернёт `rc=-2 map_unverified` (пакет, `TOOLS/COMMANDS.md`: «не начинать, пока
  `@FOC:MAP` не READY»). Пути: (a) offline-characterization → recon-коэффициенты
  (тогда `mapcap build` даёт `@MAP:READY`); (b) `mapload <994 hex>` внешним
  артефактом под identity `board=7:acs=0x26B9B97B:ccs=0x13552B12` — артефакта для
  этой линии/identity в пакете нет, демонстрационные (`pwm=294`, recon=единичная
  матрица) к `mapload` не допускаются (§25.13 AGENTS_STATUS).
* Энергированные burst'ы (#1/#2) считать **валидной уликой измерения**, но не
  картой: `@MAP:BUILD:ERROR:QUALIFICATION` — документированный fail-closed BOARD-профиля.
* Мотор/звено: 32.5 В держится (владелец), PWM обесточен (`MOE=0`, `CCER=0`, `CEN=0`),
  `RUN=0`, `em_stop1/2=1`, `CCR1..3=500`, `@BRK:valid=0`. При снятии звена — новый
  read-only шаг 0 перед следующим energize.
## Публикация и CI

Ветка `ai3/focscan-48cell-v2` опубликована (`git ls-remote` → `0ba78cb69b5cc2d6f7f5966a4f26e4a19695d695`);
CI `build-test` зелёный (run 37234326497). Сводка сессии и дефекты B20–B23:
`docs/BENCH_SESSION_20261005_ENERGIZE_CAPTURE.md`; правила: `docs/ACCEPTANCE_LESSONS.md`
§25.10 (поправка), §25.14–§25.17.

