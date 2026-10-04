# Инцидент 2026-10-04: аппаратный break при подаче 60 В + автостоп скана на гейте параметров

Плата: STM32G474RE, COM4 `STMicroelectronics STLink Virtual COM Port` @115200, образ `ab1274d`
(identity подтверждена повторно, `verdict: PASS`, 29/29 — файл `05_step0_result.json`).

## Хронология (факты, каждая строка — из сырого лога с порта)

| фаза | время | сырой лог | факт |
|---|---|---|---|
| шаг 0 (до питания) | до 19:30 | `01_baseline_raw.log` | `@PWM:FULL … CR1=0x000000E0` (CMS=11, CEN=0), `p? CR1=224:CCER=0:BDTR=7360` (MOE=0), `@BRK:valid=0`, `@FOC:VBUS=0:FAULT=0:FAULT_R=0:RUN=0:CCR1=0:CCR2=0:CCR3=0:ADC_STATUS=2` |
| шаг 1 (шкала) | ~19:33 | `calc_expected.py` | `f_pwm=5000.000`, `f_JEOS=5000.000`, `N_expected=10000`, `RESULT: PASS` (TCLK cross-check OK) |
| шаг 2 (pre-cell, FOC выключен) | ~19:33 | `04_precheck_report.txt` | 8/8 OK: `JEOS=0`, `OVR=0`, `@ENC:angle=5144:err=0`, `i=2000,0 -> @I:OK:Id=2000:Iq=0`, `RUN=0`, `FAULT=0 FAULT_R=0` |
| подача питания 60 В | ~19:37 | `03_power_check_raw.log` | **`FAULT=1:FAULT_R=18`**, `VBUS=62353`, `a` -> `@ADC:I1=2039:I2=2066:Ires=2:VBUS=619`, `ADC_STATUS=11`, `CCR1=500:CCR2=500:CCR3=500` |
| диагностика | ~19:38 | `02_fault_check_raw.log` | `@BRK:valid=1:seq=1:src=TIM1:cyc=0:sr=81,81:sd=1,1:bd=1CC0,1CC0:ce=0,0:cnt=0,0`; `p? CR1=224:CCER=0:BDTR=7360` (MOE=0); `dump CR1=0xE0:CR2=0x20:CCER=0`; `a? offset_i1=0` |

## Расшифровка (по исходникам образа)

* `FAULT_R=18` = `PROTECT_FAULT_HARDWARE_BREAK` (`src/protect.h:26` — последний элемент
  перечисления `ProtectFaultReason`, нумерация с 0).
* `@BRK:valid=1:seq=1:src=TIM1:sr=0x81` = первый break, источник TIM1, в `TIM1->SR`
  биты `UIF(0)` и `BIF(7)`. Сработал штатный обработчик `TIM1_BRK_TIM15_IRQHandler`
  (`main.c:140`): снимок диагностики (`break_diagnostics_record`), `TIM1->SR &= ~flags`
  (BIF очищается там же), `PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK)`, `PWM_Disable()`.
* `PWM_Disable()` (`src/pwm.c:417`) ставит `CCR1..3 = (ARR+1)/2 = 500` и снимает `CEN`/`MOE` —
  отсюда `CCR1=500:CCR2=500:CCR3=500` в `@FOC` при `p? CCER=0:BDTR=7360` (выход обесточен).
* `sd=1,1` в снимке и `em_stop1=1:em_stop2=1` в `@FOC` — линии SD сейчас высокие,
  то есть break был импульсным (срыв при подаче питания), а не постоянным.
* `@ADC:I1=2039 … VBUS=619` -> 619·3300/4095·125 ≈ 62.3 В (делитель 1:125, `src/adc.h:20`);
  `@FOC:VBUS=62353` мВ — питание подано и измеримо.
* «Ток» `@FOC:I1=26081:I2=26427` мА — **артефакт некалиброванных нулей**: `a?` даёт
  `offset_i1=0`, поэтому `idc_ma ≈ raw·VREF/(shunt·4095) ≈ 2039·12.8 ≈ 26 А` вместо ~0.
  Это же значение блокирует штатное снятие латча: `PROTECT_RequestClear()` (`src/protect.c:164`)
  отвергает `|idc| > PROTECT_I_MAX_MA/2 = 6000 мА` -> `PROTECT_CLEAR_VALUES_UNSAFE` (4),
  а `cli.c:291` калибрует нули (`adc_calibrate`) **только при rc==0**. Порядок обязан быть
  обратным: сначала `c` (калибровка нулей при выключенном PWM), потом `f` (сброс латча).

## Почему клетка не могла стартовать при латче (правило пакета)

`START_HERE_PC3.md` §5 (жёсткое правило): стоп при `FAULT`/`FAULT_R`. `scan_48cell.py`
реализует это автостопом (`stop_reason`: `FAULT latched`). Плюс при некалиброванных нулях
первый же валидный кадр даёт ложный `PROTECT_FAULT_OVERCURRENT` (26 А > 12 А,
`src/protect.c:6`), то есть клетки упали бы в латч, а не дали метрику.

Дополнительно `START_HERE_PC3.md` §3: «HV и повтор аппаратного break на 60 В — не делать.
Скану 60 В не нужны» — подача 60 В вышла за рекомендованный пакетом режим стенда.

## После снятия латча: скан упёрся в гейт параметров (`rc=-6`) и карту (`rc=-2`)

Латч снят штатным путём по решению владельца (`07_recover_raw.log`):

```
> c
@ADC:CAL:offset_i1=2038:offset_i2=2068:offset_ires=0
> f
@FOC … I1=25:I2=12:Ires=0 … FAULT=0:FAULT_R=0:RUN=0:em_stop1=1:em_stop2=1
> a?
@ADC:STATUS:offset_i1=2038:stream=0
> p?
@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0        (MOE=0 — выходы обесточены)
```

Далее `scan_48cell.py` (живой блок, `--amp 2000,3000,4000,5000 --angles-el 0 --window 2.0
--n-expected 10000`) корректно остановился на первой клетке (`docs/evidence/scan_20261004_run1/`):

```
[1/4] A2000_ANG0  (i=2000,0 mA)
    STOP: cell A2000_ANG0: FOC did not start (reply:
      FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply)
      @FOC:START:FAIL:rc=-6 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm
       -5=pwm_enable -6=params_out_of_range))
cells: 1 of 4 | levels: L0=1 | "f" sent: 0
```

Причина `rc=-6` — не дефект железа, а **fail-closed по умолчанию** (`src/foc.c:184-192, 552-556`):
`FOC_DEFAULT_L_UH = 100` мкГн намеренно выведен за окно `AT_MATH_SANE_LS_MIN_UH = 500`
(статическая проверка `foc_default_l_uh_must_stay_out_of_sane_range`), поэтому до явного
`mp=<Rs>,<Ls>` + `mpapply` старт FOC запрещён. Границы гейта: Rs 10…100000 мОм,
Ls 500…500000 мкГн (`src/autotune_math.h:11-14`).

Следом за params стоит **второй гейт — карта реконструкции** (`src/foc.c:560-563`):
`CurrentRecon_IsReady()` → иначе `rc=-2 map_unverified`. В телеметрии карты нет:
`@FOC:run_id=UNSET:map_id=M0:map_crc32=00000000`.

Проверка доступных путей (`06_cmd_probe_raw.log`):

| команда | ответ | вывод |
|---|---|---|
| `mapcap identity` | `@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:adc_clk=42500000:sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0x13552B12` | сборка **умеет** измерять карту (`mapcap build=<profile_id>`) |
| `mapload` (без аргумента) | `unknown` | **зонд неверной формы** — парсер ждёт `mapload ` + 994 hex-символа; вывод «команды нет в образе» из этого ответа ошибочен (см. «Поправка B18» ниже) |
| `mpapply` | `@MPAPPLY:ERROR:-2` | параметров нет (ожидаемо без `mp=`) |

То есть для запуска клеток плату нужно **подготовить**: задать измеренные `Rs/Ls`
(автотюн или паспорт) и построить/загрузить карту — это отдельная (комиссионинг) работа
вне пакета `foc_scan_48cell_v2`, который прямо запрещает менять прошивку/карту/`foc.c`
и не включает `mp=`/`mapcap` в набор команд стенда.

## Состояние платы после серии (безопасное)

`08_post_scan_raw.log`: `sysinfo` — `OVR=0:JEOS=0:TO=0:JQOVF=0`; `@FOC` —
`RUN=0`, `FAULT=0:FAULT_R=0`, `em_stop1=1:em_stop2=1`, `CCR1..3=500` (середина, как ставит
`PWM_Disable`), `p?` — `CR1=224:CCER=0:BDTR=7360` (MOE=0). VBUS в телеметрии `0`
(кадр `ADC_FRAME_NOT_ARMED`, пока FOC не запущен — VBUS не публикуется; измеренное
ранее значение было `VBUS=62353` мВ при поданном питании).

## Что не сделано и почему

* живой блок амплитуд (`scan_48cell.py --amp 2000,3000,4000,5000 --angles-el 0`) был запущен
  после снятия латча и **корректно остановлен автостопом** на первой клетке (`rc=-6`,
  ноль клеток протокола получено): данных для 48 ячеек нет;
* `--dry-run` перед запуском прошёл: 28 команд, `"f" among them: 0`;
* `f` и `c` в пакете ред. 2 отсутствуют (а `f` прямо запрещена), поэтому латч снимался
  **вне рамок пакета** — по отдельному решению владельца (п. 2-3 ниже), с полной фиксацией
  в сырых логах;
* параметры двигателя (`mp=`) и карта секторов не задавались: значения `Rs/Ls` для стенда
  и построение карты (`mapcap build=<profile_id>`) — это комиссионинг, отдельное ТЗ;
* `HV-эксперименты` и повтор аппаратного break не выполнялись (запрет пакета §3).

## Штатный путь к живым клеткам (требует отдельного решения/ТЗ)

1. подтвердить, что аппаратный break при подаче HV — понятное явление стенда, а не дефект IPM;
2. при выключенном PWM: `c` (калибровка нулей ADC) → `a?` (`offset_i1=2038`), `@FOC:I1=25:I2=12`;
3. `f` (сброс латча) → `FAULT=0:FAULT_R=0`; результат этого шага уже получен (см. `07_recover_raw.log`);
4. задать измеренные параметры двигателя: `mp=<Rs_mOhm>,<Ls_uH>` + `mpapply`
   (окно гейта: Rs 10…100000 мОм, Ls 500…500000 мкГн) — иначе `rc=-6`;
5. получить карту секторов: захват на месте — `mcarm=<profile_id>` → `mapcap run` (burst,
   8 импульсов, энергированный шаг) → `mapcap status` → `mapcap build=<profile_id>` →
   `mapcap drain`
   (`profile_id = 0x424F4152 + sector*2 + window`, десятичные `1112490322…1112490333`; иначе
   `BLOCKED:PROFILE`) — иначе `rc=-2`;

   > **Поправка 05.10.2026 (живой прогон).** В первом выпуске порядок был записан
   > «… → `mapcap drain` → `status` → `build`» — **неверно**. `mapcap build=<id>`
   > **потребляет** записи буфера (`MapCapture_ConsumeRecord`, `main.c:372`), поэтому
   > после `drain` он отвечает `@MAP:BUILD:BLOCKED:CAPTURE_STATE=3:TERM=0:AVAILABLE=0`
   > (подтверждено: `docs/evidence/incident_20261005_hwbreak/07_drain_and_blocked_build_raw.log`).
   > Обязательный порядок: **`build` ДО `drain`**. См. также `tools/pc3_commission.py`
   > (`cmd_capture`) и `tools/acceptance_lessons`-заметку в
   > `docs/BENCH_SESSION_20261005_ENERGIZE_CAPTURE.md` (B21).
6. повторить живой блок амплитуд (`1` → `i=` → окно 2 с → `0`), ожидая `FOC started`.

## Поправка B18: зонд путей к карте был поставлен неверно

Первая редакция этой справки (и раздел B17 пакета) утверждала: «`mapload` отвечает `unknown` ⇒
внешний артефакт карты этим образом не грузится». Основание было негодным: на плату ушло слово
**без аргумента**, а парсер образа распознаёт только `mapload ` + 994 hex-символа
(`strncmp(line,"mapload ",8)`, `main.c`).

Повторный читающий зонд (2026-10-04, тот же образ, `09_capability_probe_raw.log`; разбор —
`capability_probe.md` рядом) при гейте `@FOC frames=8 RUN=0 FAULT=0`:

| зонд | ответ платы | вывод |
|---|---|---|
| `mapcap status` | `@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0:detail=0:…` | путь `mapcap` скомпилирован |
| `mapcap identity` | `@MAP:IDENTITY:board=7:pwm=5000:…:acs=0x26B9B97B:ccs=0x13552B12` | скомпилирована ветка `#if OEW_MAP_L3` |
| `mapload 00` | `@MAP:LOAD:FAIL:DECODE` | команда **есть** (длина отсекается раньше разбора) |
| `mapcap build=` | `err: mapcap build=<profile>` | команда **есть** (пустой аргумент = ошибка формы) |

Значит `rc=-2` — это «карта ещё не загружена», а не «путь закрыт сборкой». Внешние артефакты
`мотор анализ\map_upload_pkg_20260908\*.bin` при этом не подходят: они несут `pwm=294` (дефект D3,
двойной учёт `PSC`), а такие identity отвергаются (`src/map_capture_profiles.c:191-199`) — живая
плата даёт `pwm=5000`, поэтому загрузка этих файлов дала бы `@MAP:LOAD:FAIL:COMMISSION`. Границы
вывода: захват не запускался, поэтому `@MC:ARM`/`@MC:RUN` и качество будущей карты неизвестны.

