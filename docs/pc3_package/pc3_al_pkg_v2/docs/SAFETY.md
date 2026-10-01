# Защитный envelope пакета (ред. 2 — числа сверены с кодом 4507983)

## Структурное свойство прошивки

`PROTECT_CheckFrame()` вызывается ТОЛЬКО при `foc_running() && timer_enabled()`
(`src/adc_dispatch.c:24-28`; маппинг `main.c:175`). Во всех состояниях вне FOC
(`al`, autotune `ch/iv/oew/rr/noload/scope/idle`) программная защита структурно
слепа.

Пороги самой защиты (`src/protect.c:6-8`):
- `PROTECT_I_MAX_MA = 12000` — по `|idc1_ma|`, `|idc2_ma|`;
- `PROTECT_VBUS_MIN_MV = 8000`, `PROTECT_VBUS_MAX_MV = 350000`.

Защита работает по полям кадра `idc1_ma/idc2_ma/vbus_mv` (`src/protect.c:96-112`),
то есть по DC-link шунтам с их масштабом, а не по реконструированным фазным
токам. Следствие (не изменилось): магнитудные рейлы активны и при инверсии
знака конвенции; слепнут при неверном масштабе усиления.

Следствие по ограничителям: единственные гарантированные ограничители тока во
время `al` и маршрута B:
1. внешний лимит источника питания — выставляется ДО команды `al`;
2. аппаратный break TIM1/TIM8 (BKE в BDTR), ЕСЛИ физически разведён
   и не выключен вариантом сборки OEW_SD_MONITOR_ONLY.

## Preflight (инвертор выключен)

```
c
dump
dump8
sysinfo
a?
a
dumpa
```

Зафиксировать:
- MOE = 0 на обоих таймерах (BDTR, бит 15, маска 0x8000);
- JADSTART baseline (dumpa, поле CR, бит 3, маска 0x8) — ожидаемо 0;
- offset'ы: `c` печатает `@ADC:CAL:offset_i1=…:offset_i2=…:offset_ires=…`
  напрямую (`src/cli.c:71`), либо `@ADC:CAL:FAIL:rc=…`. `a?` печатает только
  `offset_i1` + статус потока (`src/cli.c:58-60`); если `c` отказал, нулевой raw
  префлайтового `a` остаётся допустимой оценкой offset'а. `cv` даёт
  `@ADC:CV:OK:offset_vbus=…` (`src/cli.c:75-88`);
- FAULT/FAULT_R из @FOC (телеметрия печатается безусловно каждые 100 мс при
  выключенном stream, поэтому до старта FOC это живой источник состояния
  защёлкнутого fault); второй источник допуска — admission-ответ самой `al`;
- OVR baseline (sysinfo);
- фактический Vbus (`a`, поле VBUS). **1 LSB = 125 · 3300 / 4095 = 100.733 мВ**
  (`ADC_VBUS_DIVIDER/ADC_VREF_MV/ADC_MAX_CODE` — `src/adc.h:8,11,20`;
  формула — `src/adc.c:89-97`).

## Vbus

Для `al` порог прошивкой НЕ навязывается: команда `al` не вызывает
`AT_SafetyCheck` (`src/cli.c:140-190`), а её admission-гейт в
`PWM_AlignApertureStart` (`src/pwm.c`) состоит из `arr != 0`, PWM выключен,
ADC injected не взведён, `!PROTECT_IsFault()`, `!g_clock_fail`,
`PWM_HardwareInterlockHealthy()` — проверки Vbus там нет. Значит
`@AT:ERROR:VBUS_LOW` на `al` не появится ни при каком Vbus: нижнюю границу
задаёт оператор.

Для маршрута B порог навязан прошивкой: `AT_SafetyCheck`
(`src/autotune.c:596-660`) при `vbus < 12000` мВ отвечает
`@AT:ERROR:VBUS_LOW` (rc −2), при `vbus > 350000` — `@AT:ERROR:VBUS_HIGH`
(rc −4); `oew` дополнительно проверяет сам (`src/autotune.c:1446-1448`,
`@AT:OEW:ERROR:VBUS_LOW`, rc −7).

## Оценка тока (sanity, НЕ критерий PASS)

I_ss ≈ |permille| · Vbus / (1000 · Rs_обмотки)
Для 48 В и Rs ≈ 13 Ом: U ≈ 222 мА, V/W ≈ 111 мА.
Это первичная DC-оценка: реальный установившийся ток может отличаться из-за
индуктивности, dead-time, сопротивлений силового пути и фактического
дифференциального напряжения. Критерием PASS не является.

## Удержание al

Авто-таймера у `al` нет: вектор живёт до `al off`. Длительность удержания
ограничивает оператор (порядка нескольких секунд). Повторный `al` по коду
сначала останавливает предыдущий вектор — накопления нет.

Во время удержания ADC injected ВЗВЕДЁН: `PWM_AlignApertureStart` вызывает
`ADC_InjectedStart()` (`src/pwm.c`), поэтому JADSTART = 1 — это штатное
состояние удержания, а не аномалия; режим наблюдения выбирается по
docs/AL_PROCEDURE.md §3.

## Маршрут B (ch → iv → oew → pi=N → piapply)

- внешний лимит источника (порядка 1–2 А) ДО первой команды. Два разных
  программных порога, не смешивать: `PROTECT_I_MAX_MA = 12000` мА
  (`src/protect.c:6`, вне FOC не выполняется) и `AUTOTUNE_MAX_CURRENT_MA = 8000`
  мА (`src/autotune.h:7`, аборт тестов autotune);
- **все три команды** — `ch`, `iv`, `oew` — проходят `AT_SafetyCheck`
  (`src/autotune.c:336`, `775`, `1431`):
  защёлкнутый fault → `@AT:ERROR:FAULT_CLEAR_FIRST`; работающий V/f →
  `@AT:ERROR:VFC_RUNNING_STOP_FIRST`; `vbus < 12000` мВ →
  `@AT:ERROR:VBUS_LOW`; `vbus > 350000` мВ → `@AT:ERROR:VBUS_HIGH`;
  остаточный ток: `|I1|,|I2|,|Ires| ≤ 50 мА`, до 5 попыток по 100 мс
  с повторной калибровкой offset, иначе `@AT:WARN:RESIDUAL_CURRENT` и отказ
  (`src/autotune.c:640-665`);
- `ch` (`Autotune_DetectChannel`, `src/autotune.c:329`): `PWM_SetDuty1(5,0,0)` +
  `PWM_SetDuty2(100,100,100)`; ожидание нарастания тока **адаптивное** — до
  40 × 500 мкс = 20 мс, выход раньше при max|ΔI| ≥ 50 мА или мгновенном
  |I| > 2000 мА (`src/autotune.c:396-409`). Фиксированного окна 300 мкс в коде
  нет: 300 мкс упомянуты в комментарии как объяснение, почему фиксированного
  окна недостаточно (ред. 1 переносила комментарий в процедуру);
- `iv` (`Autotune_MeasureRs_IV`, `src/autotune.c:764`): ступени duty
  2/4/6/8/10/12/15 %;
- `oew` (`Autotune_MeasureLs_OEW`, `src/autotune.c:1426`): рампа d = 5…50 %
  шагом 1 %; аборт при > 8000 мА (`@AT:OEW:ERROR:OVERCURRENT`, rc −6),
  по флагу — `@AT:OEW:ABORTED` (rc −5);
- отключение ADC IRQ: `AT_TestBegin/AT_TestEnd` (`src/autotune.c:68,86`),
  в `ch` — прямой `NVIC_DisableIRQ(ADC1_2_IRQn)` (`src/autotune.c:342`) →
  PROTECT на время теста не выполняется ни в одном из трёх;
- `pi=N` (`Autotune_CalcPI`, `src/autotune.c:2188`) силовой части не касается,
  но откажет при Ls/Rs вне sane-окна `AT_SaneLs/AT_SaneRs`
  (`src/autotune_math.h:11-14`: Ls 500…500000 мкГн, Rs 10…100000 мОм) и при
  полосе вне 100…500 Гц (`AT_PI_BW_MIN_HZ=100`, `AT_PI_BW_MAX_HZ=FS/10`,
  `src/autotune.c:150-151`); ошибки `@AT:PI:ERROR:INVALID_RS_LS` /
  `:BW_RANGE` / `:ZERO_GAIN`; поэтому порядок ch → iv → oew → pi обязателен;
  `piapply` → `@PI:APPLIED:…:AP=1` / `@PI:ERROR:<rc>` /
  `@PI:ERROR:NOT_CALCULATED` (`src/cli.c:422-425`);
- результаты живут только в RAM (`g_motor_params`); `Autotune_Init()` делает
  `memset` структуры (`src/autotune.c:2319-2321`) — после перезагрузки
  параметры и pole pairs обнуляются.

### Offset'ы во время маршрута B

`AT_SafetyCheck` может **перекалибровать offset посреди маршрута B**:
после спада тока ниже 50 мА по `|I1|,|I2|,|Ires|` вызывается
`ADC_CalibrateOffsets()` (`src/autotune.c:647-648`, в цикле до 5 попыток,
`autotune.c:642`). Следствие для лога:
offset'ы, снятые в префлайте (`c`, `a?`), и offset'ы после маршрута могут
законно различаться — это действие прошивки, а не дрейф канала. В логе
маршрута фиксировать строкой вида
`# offset recalibrated by AT_SafetyCheck` до/после, и только при расхождении
offset'ов ВНЕ этих точек говорить о дрейфе. Для оператора это же означает:
firmware-гейт примет и ненулевой остаток (до 50 мА) после ожидания спада
(до 5 попыток × 100 мс), поэтому «остаток ≈ 0» — операционное ожидание, а не
условие входа.

## Stop-критерии (немедленно al off / 0 / снять питание)

- любой FAULT ≠ 0;
- срабатывание hardware break (SD/EM_STOP);
- рост тока к лимиту источника;
- шум, вибрация, нагрев двигателя или инверторов;
- просадка Vbus ниже 85 % начального;
- отсутствие реакции на al off.
