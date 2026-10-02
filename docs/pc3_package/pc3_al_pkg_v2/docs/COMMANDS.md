# CLI-справочник образа 4507983 (ред. 2 — после сверки с кодом)

Все строки ответов ниже взяты из фактического дерева `4507983`
(`src/cli.c`, `src/foc.c`, `src/foc.h`, `src/autotune.c`, `src/protect.c`,
`src/telemetry_format.h`), а не из справочного среза. Номера строк — для
`git show 4507983:<файл>`.

## Диагностический набор

a            single-shot: сырые I1/I2/Ires/VBUS (@ADC:I1=..:I2=..:Ires=..:VBUS=..),
             regular polling                                     (cli.c:48)
a=N          stream каждые N мс (50..1000); a=0 — стоп               (cli.c:52)
a?           @ADC:STATUS:offset_i1=..:stream=.. — offset_i1 + статус потока;
             offset_i2/offset_ires НЕ печатает                      (cli.c:58-60)
c            перекалибровка offset по 256 выборкам (инвертор ВЫКЛ!):
             @ADC:CAL:offset_i1=..:offset_i2=..:offset_ires=..
             либо @ADC:CAL:FAIL:rc=.. (Ires unqualified / ADC not converged)
                                                                    (cli.c:62-74)
cv           калибровка offset VBUS (инвертор ВЫКЛ!):
             @ADC:CV:OK:offset_vbus=.. (raw at 0 V);
             @ADC:CV:FAIL:rc=.. (0=OK -1=ADC busy/not converged -2=VBUS not at zero);
             в сборке без операции — `err: cv unsupported`           (cli.c:75-88)
ci 0/1/2     окна шунтовой калибровки (только PWM off):
             @CI:ZERO:samples=..:raw_i1_avg=..:raw_i2_avg=..:raw_i1_min=..:raw_i1_max=..
             :raw_i2_min=..:raw_i2_max=..:dt_ms=..:vbus_mv=..
             / @CI:PT<k>:samples=..:raw_i<k>_avg=.. …
             `ci ?` — справка по окнам                            (cli.c:89-139)
p?           TIM1: CR1/CCER/BDTR/CNT                               (cli.c:191)
p=arr,duty,dt[,mask]   debug-конфиг PWM → @PWM:OK:arr=..:duty=..:dt=..  (cli.c:241-246)
bench=0 / bench=…      bench-профиль (в no-output сборке)          (cli.c:196-240)
breakdiag    @BRK:valid=0 либо @BRK:valid=1:seq=..:src=TIM1|TIM8:cyc=..:sr=..:sd=..:bd=..:ce=..:cnt=..:cap=..   (cli.c:257-274)
breakdiag reset        @BRK:RESET:rc=0|-1                          (cli.c:275-277)
dump         TIM1: PSC/ARR/BDTR/CR1/CR2/CCER                       (cli.c:311)
dump8        TIM8: то же                                           (cli.c:311)
dumpa        ADC2: SQR1/CFGR/SMPR1/JSQR/DIFSEL/CR/ISR/DR/JDR1..4
             (регистровый снимок, конверсий не запускает; JADSTART = CR бит 3) (cli.c:318)
pdump        @PWM:FULL… — hex-дамп обоих таймеров                  (cli.c:326)
sysinfo      CLK/PSC/TCLK/PLLCFGR/OVR                              (cli.c:330)
run=<id>     метка прогона (1..23 симв. [A-Za-z0-9_.-]) → @RUN:ID=<id>  (cli.c:428-446)
m            справка                                                (cli.c:280-281)
s            SWO-тест                                              (cli.c:282-284)

## Телеметрия

@FOC печатается безусловно каждые 100 мс при выключенном stream (telemetry_format.h:26-28):
Id/Iq/Id_ref/Iq_ref, VBUS (мВ), STATE, SPD, TH, FAULT, FAULT_R, FAIL, RUN, em_stop1/2.
До старта FOC это живой источник FAULT/FAULT_R (программная защёлка).

## Управление FOC

1            старт (cli.c:247-254):
             при защёлкнутом fault  → "FAULT! send 'f' to clear"
             успех                  → "FOC started"
             отказ                  → @FOC:START:FAIL:rc=N
                                      (0=OK -1=clock/fault -2=map_unverified
                                       -3=calib -4=arm -5=pwm_enable
                                       -6=params_out_of_range)
0            стоп → "FOC stopped"                                  (cli.c:255-256)
f            очистка fault + перекалибровка offset (только вне FOC);
             отказ при активном управлении: "err: stop FOC/Vf first" (cli.c:285-304)
s=N          уставка скорости, об/мин, диапазон ±50000; ответ "speed=<rpm> rpm";
             отказы: "err: no digits" / "err: trailing chars" / "err: out of range"
                                                                    (cli.c:305-311)
i=<Id_mA>,<Iq_mA>
             уставки токов контура в мА; ОБЕ величины обязательны
             (sscanf "i=%d,%d" == 2), значения клампятся в ±10000 мА
             (FOC_I_MAX_MA, foc.c:260; clamp — foc.c:364-368);
             ответ @I:OK:Id=<Id>:Iq=<Iq> — повторяет ЗАПРОШЕННЫЕ значения,
             а не результат клампа (кламп происходит внутри FOC_SetIdRef
             /FOC_SetIqRef): при i=20000,0 в ответе будет Id=20000,
             фактическая уставка — 10000;
             Iq=0 — режим без прямой q-уставки (контуром q владеет скорость).
             Штатная команда первого FOC: проверено
             cli.c:427 → main.c:519 cli_foc_current → FOC_SetIdRef/FOC_SetIqRef.
             Силовая часть командой НЕ включается: команда только пишет уставку,
             поэтому она безопасна до `1`.
pp=N         пары полюсов (1..24)                                  (cli.c:341)
dt=N         dead-time, нс (до 12700)                              (cli.c:357)
mp=R,L,Rr,Lm,Tr,Ke,p,J   применить параметры машины (cli.c:398-416);
             минимум 2 поля; поля 3..8 применяются только при >0;
             Ls==0/Rs==0 → отказ (foc_set_params: r_mohm<1 || l_uh<1 → -2);
             Vbus берётся в момент команды; p= применяется только при 1..24;
             успех  → @MP:OK:Rs=..:Ls=..:Rr=..:Lm=..:Tr=..:Ke=..:p=..:J=..
                      :Kp=..:Ki=..:Lsig=..:AP=1
             отказ  → @MP:ERROR:<rc>
mpapply      применить уже сохранённые параметры (cli.c:417-421):
             @MPAPPLY:OK:… / @MPAPPLY:ERROR:<rc>
pi=N         расчёт Kp/Ki по полосе N Гц                            (cli.c:396)
piapply      применить последние Kp/Ki (cli.c:422-425):
             @PI:APPLIED:Kp=..:Ki=..:AP=1
             / @PI:ERROR:<rc> / @PI:ERROR:NOT_CALCULATED
stats        статистика autotune                                   (cli.c:426)
abort        запрос аборта autotune                                (cli.c:393)

## Align-check

al           включить дифференциальный вектор u=−6 %, v=w=+3 %
             (CCR TIM1=TIM8 470/515/515 при ARR=999);
             успех  → @AL:OK:code=0:u=..:uu=..:v=..:vv=..:w=..:ww=..:i1_ma=..
                       :i2_ma=..:align=0 (1=v1_rev 2=v2_rev 3=both; report-only)
             отказ старта → @AL:FAIL:start (PWM off, no fault, SD high, ADC idle)
             отказ вектора → @AL:FAIL:vector
             в сборке без апертуры → `err: al unsupported`
             при активных FOC/Vf → `err: stop FOC/Vf first`     (cli.c:140-179)
al off       выключить вектор → @AL:OFF                           (cli.c:180-190)
Авто-таймера нет; повторный al останавливает предыдущий вектор.
Safety: docs/SAFETY.md.

## Маршрут B (только после физического PASS al)

ch, chw, iv, pairs, oew, rr, noload, scope, lspos, idle, curve, params, irot,
inertia, abort — autotune-команды (cli.c:361-395); на время теста отключают
ADC IRQ → PROTECT неактивен; конверт и порядок: docs/SAFETY.md.
Фактические ack-строки:
  @AT:RS_IV:OK:Rs=<mΩ>                        (autotune.c:877)
  @AT:OEW:OK:Ls=<µH>:Isat=<мА>                (autotune.c:1637)
  @PI:APPLIED:Kp=..:Ki=..:AP=1                (cli.c:424)
Предел тока тестов autotune: AUTOTUNE_MAX_CURRENT_MA = 8000 мА
(autotune.h:7); у `scope` — AT_SCOPE_MAX_CURRENT_MA = 8000 мА (autotune.c:144);
порог VBUS маршрута B — 12000 мВ (autotune.c:621, 1446).

## V/f

vf=N         ±5000 об/мин; vf=0 — стоп; V/f blocked: rc=… (sample context unverified)
vf?          @VF:target=..:meas=..:fe=..:fslip=..:vmag=..
vflog=N      0 или 10..1000 (период @VFLOG), vfk=N,N — boost/rated

## Команды commissioning-адаптера (main.c — ТОЛЬКО при OEW_MAP_CAPTURE=1)

Третий маршрут CLI: нераспознанное в `src/cli.c` уходит в `ops->mapcap_command`
(`src/cli.c:278`) → `cli_mapcap_command` (`main.c:541-647`). В production-сборке
(`make` без defines) тело компилируется как `(void)line; return 0;`
(`main.c:644-646`), поэтому в production-артефакте этих команд НЕТ — они
отвечают `unknown`. В commissioning-сборке CI
(`-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7
-DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1 -DOEW_HS1_COMMISSIONING_RELEASE=1`,
`.github/workflows/ci.yml` @4507983) доступны:

```
mcarm=<profile_id>   -> @MC:ARM:cap=…:rc=…:offsets_valid=…:inj_start_rc=…
                        либо @MC:ARM:BLOCKED:PROFILE
mapcap run           -> @MC:RUN:rc=…
mapcap drain         -> @MC:REC:cap=…:seq=…:raw_i1=…:i1=…:… (×N) + @MC:DRAIN:records=…
mapcap abort         -> @MC:ABORT:rc=…
mapcap status        -> @MC:STATUS:state=…:term=…:…:fault_detail=…
mapcap identity      -> только при OEW_MAP_L3=1
mapcap build=<N>     -> только при OEW_MAP_L3=1: сборка и загрузка карты
mapload <994 hex>    -> @MAP:LOAD:OK:crc=…:cid=… / @MAP:LOAD:FAIL:DECODE
```

`mapload` принимает ровно 994 hex-символа (`OEW_CURRENT_MAP_WIRE_SIZE * 2`),
декодирует через `MapArtifact_DecodeBinary` и грузит через
`CurrentMap_LoadMeasured` — это путь снятия гейта `-2 map_unverified`.

Идентификаторы профилей (`src/map_capture_profiles.c`):
- `0x424F4152` = "BOAR" (+ sector·2 + window, 0…11) — измеренный board-профиль;
- `0x53594E54` = "SYNT" — синтетический, активируется ТОЛЬКО если одновременно
  определены `OEW_MAP_SYNTHETIC_PROFILE` и `OEW_HOST_TEST`
  (`src/map_capture_profiles.c:33-38`) — оба флага есть в CI-сборке
  commissioning. Следствие: на этом образе синтетический профиль скомпилирован,
  и карта, полученная через него, физическим доказательством НЕ является.

## Наличие команд: проверено grep'ом по дереву 4507983

Присутствуют (НЕ трактовать как unknown; в прежней редакции часть из них
числилась отсутствующей):
`i=<Id>,<Iq>`, `cv`, `ci 0/1/2`, `ci ?`, `run=<id>`, `bench=…`,
`breakdiag` / `breakdiag reset`, `pdump`, `inertia`, `chw`, `abort`, `stats`,
`s=N`, `m`, `f`, `vf=` / `vflog=` / `vf?` / `vfk=`, `enc`, а также команды
commissioning-адаптера из раздела выше (в commissioning-сборке).

Отсутствуют в образе 4507983 (grep по ВСЕМУ дереву — 0 совпадений):
`vci=`, `jc`.

`eangle` — НЕ команда, а поле телеметрии `@VFLOG`
(`main.c:253`, разбирается `telem_parser.py`).

Правило приёмки: утверждение «команды X нет в образе» проверяется grep'ом по
ФАКТИЧЕСКОМУ дереву (`git grep "<X>" <sha>`) и с учётом `#if`-вариантов сборки:
часть CLI живёт в `main.c` (`cli_mapcap_command`) и в production-сборке
отсутствует, а не «не существует». Обратное тоже верно: наличие `i=`
подтверждено кодом, а не памятью.
