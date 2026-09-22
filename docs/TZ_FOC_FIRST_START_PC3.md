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

## Required current-map artifact

FOC намеренно блокируется без полной reconstruction map.

Использовать существующий `oew_map_v2.bin` только как commissioning artifact, если его identity соответствует текущему firmware/board. Его физическая qualification ранее не завершена, поэтому успешная загрузка карты **не означает**, что карта физически сертифицирована.

Wire size: 497 bytes = 994 hex characters.

На PC-3 преобразовать binary в одну hex-строку, например PowerShell:

```powershell
$hex = [Convert]::ToHexString([IO.File]::ReadAllBytes(".\oew_map_v2.bin"))
$hex.Length
```

Ожидается:

```
994
```

Отправить:

```
mapload <994 hex chars>
```

Ожидаемый ответ:

```
@MAP:LOAD:OK:crc=0x........:cid=0x........
```

Если `@MAP:LOAD:FAIL` — **не обходить admission**. Снять полный ответ; это означает mismatch/невалидность artifact.

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

```
mapload <994 hex chars>
```

После успешного load:

```
@MAP:LOAD:OK:...
```

Не выполнять `mapload` при FOC/Vf/PWM running.

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

