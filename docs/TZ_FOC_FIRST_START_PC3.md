# FOC first commissioning — PC-3

## Purpose

Первый физический запуск FOC на существующем OEW hardware path.

Это **commissioning run**, а не доказательство физической квалификации current map. Цель — получить первый управляемый запуск/вращение и собрать telemetry для последующего параметрического поиска.

## Branch

`ai4/foc-first-start-pc3`

Base: `main` @ `1fa2109a1339f82737b5b1aa846b57bfdabf56f3`

## Changes in this package

- добавлена CLI-команда `rpm=N`, диапазон `-5000..5000` rpm;
- команда только задаёт `FOC_SetSpeed()`, PWM не включает;
- ответ: `@RPM:OK:rpm=N`;
- добавлен hosted CLI test;
- safety gates, `both_enable()`, current reconstruction и map admission не ослаблены.

## Motor baseline

Использовать первый baseline:

```
mp=15120,19410
mpapply
```

Это:

- Rs = 15.120 Ω
- Ls = 19.410 mH
- pole pairs = 3

This is an **external LCR average baseline, not phase-qualified**. The measured phase values were U=17.90 mH / 14.65 Ω, V=20.28 mH / 15.35 Ω, W=20.05 mH / 15.36 Ω. Therefore the first run must remain short, unloaded, and observational; no parameter optimization or prolonged acceleration is permitted from this baseline alone.

Rs/Ls находятся в sane window и ранее уже дали ожидаемый `FOC_Start() -> -2` при отсутствии current map.

Rr/Lm/Tr пока **не считать измеренными**. Их не подбирать до получения первого рабочего запуска.

## Required commissioning firmware artifact

Для физического прогона использовать **commissioning artifact из CI run 35713510120**, собранный с этого exact source SHA:

```
source SHA:    81b41aa9ccb108410167fb775e2401777cb736f1
image:         firmware_commissioning_81b41aa.bin
SHA256:        36f0e9705001bf00f3d8730fecaf091eb6b9ec440e2d69c62831d0335c34267c
size:          87864 bytes
CI workflow:   build-test / run 35713510120
```

**Не прошивать production `firmware` artifact для этого прогона.** Production build не содержит commissioning mapload/mapcap path и не включает `OEW_HS1_COMMISSIONING_RELEASE=1`; с ним этот first-start package не выполняется.

Провенанс commissioning image должен быть проверен перед прошивкой; ожидается PASS для source SHA и образа. Локальный GCC build hash не использовать как критерий byte identity CI image.

## Required current-map artifact

FOC намеренно блокируется без полной reconstruction map.

Использовать существующий `oew_map_v2.bin` только как commissioning artifact, если его identity соответствует текущему firmware/board. Его физическая qualification ранее не завершена, поэтому успешная загрузка карты **не означает**, что карта физически сертифицирована.

Wire size: 497 bytes = 994 hex characters — перевод в hex и передачу по UART выполняет штатный инструмент; ручная конвертация и вставка hex-строки в терминал не используются.

Загрузка выполняется штатным инструментом (`tools/map_upload.py`, `TZ_MAP_UPLOAD_AND_ADMISSION` §2.3) при неактивном FOC/Vf/PWM:

```powershell
py -3 tools\map_upload.py --port COM<MCU_VCP> --bin oew_map_v2.bin
```

`COM<MCU_VCP>` — фактический UART VCP платы (порт, на котором виден prompt firmware), не COM из старого лога.

Ожидаемый ответ:

```
@MAP:LOAD:OK:crc=0x........:cid=0x........
```

`@MAP:LOAD:FAIL:*` (в том числе `FAIL:COMMISSION`) → **STOP**. Обход identity/admission запрещён: снять полный ответ UART и артефакт `oew_map_v2.json`, разбираться до повторного запуска. Несовпадение identity карты и платы — не повод обходить гейт.

Флаг `--verify` у инструмента выполняет FOC start/stop smoke test (шлёт `1`, затем `0`): при загрузке карты на шаге 3 он НЕ используется — старт FOC остаётся шагом 5, после `mpapply` и `rpm=`/`i=`. Поэтому в процедуре выше команда без `--verify`.

## PC-3 first-start procedure

### 1. Flash

Прошить firmware, собранный именно из этой ветки.

До подачи DC-link:

```
a
a?
p?
breakdiag
```

Зафиксировать UART.

Ожидаемое исходное состояние:

- FAULT = 0
- FAULT_R = 0
- RUN = 0
- PWM disabled / MOE=0
- BRK valid=0

### 2. Motor parameters

При PWM disabled:

```
mp=15120,19410
mpapply
```

Проверить:

```
@MPAPPLY:OK:Rs=15120:Ls=19410:...:p=3:...:AP=1
```

### 3. Load map

```powershell
py -3 tools\map_upload.py --port COM<MCU_VCP> --bin oew_map_v2.bin
```

После успешного load:

```
@MAP:LOAD:OK:...
```

`@MAP:LOAD:FAIL:COMMISSION` → **STOP**, обход admission запрещён (снять ответ UART и артефакт, разбираться).

Не выполнять загрузку при FOC/Vf/PWM running. Ручная hex-строка и прямая команда `mapload <994 hex chars>` в процедуре не используются — их заменяет инструмент.

### 4. Set the first conservative command

До запуска:

```
rpm=100
i=2000,100
```

То есть:

- speed reference = +100 rpm;
- Id reference = 2.0 A;
- Iq reference = 0.1 A.

Это стартовая commissioning point, а не утверждение оптимального режима.

### 5. Start

Команда:

```
1
```

Ожидаемый результат:

```
FOC started
```

Старт выполняет оператор вручную командой `1`; `map_upload.py --verify` делает то же самое (шлёт `1`, затем `0`), но по ТЗ старт идёт после проверки состояния, поэтому здесь используется ручная команда.

После этого немедленно смотреть:

- токи I1/I2;
- Id/Iq;
- VBUS;
- speed;
- fault/break;
- RUN;
- отсутствие runaway/current explosion.

Если появляется fault, необычный ток, резкое механическое движение или другой небезопасный режим — сразу:

```
0
```

и снять UART.

### 6. First observation window

Первый запуск — короткий, только чтобы установить факт:

1. FOC действительно вошёл в RUN;
2. токи имеют физически разумный порядок;
3. скорость имеет правильный знак/порядок величины;
4. VBUS остаётся в установленном G0 диапазоне;
5. нет повторного fault/SD/ADC invalid;
6. PWM не остаётся включённым после `0`.

Не проводить пока длительный разгон и не делать parameter sweep.

## Stop conditions

Немедленно остановить эксперимент при:

- `FOC:START:FAIL:rc=-6`;
- `rc=-2` после supposedly successful map load;
- `rc=-3/-4/-5`;
- `FOC started` при отсутствии ожидаемой карты;
- current/Id/Iq runaway;
- неожидаемом направлении вращения;
- fault/break;
- ADC invalid/drop/overflow;
- VBUS выходит за действующий G0;
- механически ненормальном поведении.

## Important interpretation

Первый PASS здесь означает только:

```
software admission
    +
PWM/ADC control path
    +
current reconstruction
    +
FOC runtime
    +
first physical response
```

Он **не означает**:

- physical qualification of `oew_map_v2.bin`;
- correctness of phase naming;
- correctness of Rr/Lm/Tr;
- optimal PI gains;
- optimal flux;
- rated-load operation.

## Phase asymmetry qualification

External MS5308 measurements at 100 Hz:

| Phase | Ls | Rs | tau = Ls/Rs |
|---|---:|---:|---:|
| U | 17.90 mH | 14.65 ohm | 1.222 ms |
| V | 20.28 mH | 15.35 ohm | 1.321 ms |
| W | 20.05 mH | 15.36 ohm | 1.305 ms |

The scalar commissioning baseline remains:

```
mp=15120,19410
```

This is an **external LCR average baseline, not phase-qualified**. It does not compensate the lower U-phase inductance and must not be replaced by `Ls=17.90 mH` merely to match phase U.

The first FOC run is therefore limited to a short, unloaded commissioning observation. It is not a phase-symmetry qualification, rated-load run, or parameter-optimization run.

The existing I1/I2 channels are DC-link shunt measurements. They must not be interpreted as three independent phase RMS measurements unless the current-reconstruction/phase convention has been independently established.

Stop immediately if current tracking becomes unstable, current magnitude runs away, unexpected torque ripple/acoustic/mechanical oscillation appears, fault/break/ADC-invalid occurs, or another abnormal physical condition is observed.

Record the LCR baseline in the operator log before `1`:

```
# external LCR baseline
# U: Ls=17.90mH Rs=14.65ohm tau=1.222ms
# V: Ls=20.28mH Rs=15.35ohm tau=1.321ms
# W: Ls=20.05mH Rs=15.36ohm tau=1.305ms
# scalar FOC baseline: Rs=15.120ohm Ls=19.410mH
# phase symmetry: NOT QUALIFIED
```

## Next phase after first successful run

После первого запуска не менять всё сразу.

Порядок поиска:

1. Rs/Ls;
2. Rr/Lm/Tr;
3. Id reference;
4. PI Kp/Ki;
5. speed reference / speed-loop gains;
6. flux/limits.

Для каждого набора сохранять одинаковые:

```
parameter set
→ same VBUS
→ same speed/current command
→ same observation time
→ UART telemetry
→ RMS/current ripple/settling/speed/error/faults
```

Сначала ищем рабочее окно, затем уточняем параметры внутри него.

## Do not

- не отключать `CurrentRecon_IsReady()`;
- не обходить `CurrentMap_LoadMeasured()`;
- не менять `both_enable()`;
- не поднимать VBUS ради прохождения теста;
- не считать старые synthetic map samples физическим доказательством;
- не запускать LsStep повторно;
- не смешивать этот commissioning с INA240 branch.
