# ТЗ: channel probe (`chu`/`chv`/`chw`) через сервисный силовой вход

Проект: STM32G474RE, CMSIS-only, OEW (2×STEVAL-IPM20B). Ветка-исполнитель: `ai2/tz2-ls-probe-service-path`
от `origin/main`. Один пакет = одно ТЗ: изменяется ровно одна функция + тест парсинга CLI.

## 1. Проблема (с цитатами кода)

`Autotune_ProbePhase()` (`src/autotune.c:490`) включает мосты через `both_enable()`, а тот —
**намеренная fail-closed заглушка** (`src/autotune.c:1405`):

```c
static void both_enable(void) {
    /* Интеграция sample-context: прямого включения мостов больше НЕТ.
     * Energised autotune-тесты заблокированы, пока измеренная карта
     * OEW sector/window не существует (CurrentMap_Select* = fail-closed). */
    PWM_InvalidateSampleContext();
    g_autotune_abort = 1;
    UART_SendStr("@AT:ERROR:POWER_BLOCKED:no measured OEW sector/window map\r\n");
}
```

Следствия (проверено чтением кода, не предположения):

1. `chu`/`chv`/`chw` (`main.c:508` → `Autotune_ProbePhase(0/1/2)`) **физически не включают мост**:
   в логе будет `@DBG:CHU:START` → `@AT:ERROR:POWER_BLOCKED:…` → `ZERO/TEST/DELTA` с дельтами ≈ 0.
   Формально команда «работает», фактически даёт вводящий в заблуждение результат.
2. Никакое состояние времени выполнения блокировку не снимает — ветки «карта измерена»
   в `both_enable()` нет; `mapload` на это не влияет.
3. Тот же путь используют `Autotune_DetectChannel()` (`ch`), `Autotune_MeasureLs_OEW()` (`oew`),
   `Autotune_MeasureLs_Position()` (`lspos`), `Autotune_Irot/Inertia/Rr/NoLoad/Scope` —
   все они в текущем `main` неработоспособны по той же причине (вне объёма этого ТЗ).

При этом в проекте есть **разрешённый диагностический силовой вход**
`PWM_ServiceCaptureStart(const PwmServiceCapturePattern *)` (`src/pwm.c`), которым пользуется
map capture и уже принятый тест `ls`. Он принимает произвольный 3-фазный CCR-паттерн для обоих
инверторов, сам проверяет предусловия (`pwm_common_arm_preconditions(false)`: fault/interlock/clock/ADC armed),
публикует диагностический контекст (`valid=false`) и запускает таймеры.

## 2. Цель

Сделать `chu`/`chv`/`chw` работоспособными для их прямой задачи — определить, **какой
измерительный канал отвечает на возбуждение конкретной фазы и с каким знаком**
(I1 — DC-шунт Inv1, I2 — DC-шунт Inv2, Ires — фазовый CT, `AT_CH_IN`) — **не снимая**
fail-closed блокировку `both_enable()` и не трогая остальные энергии.тесты.

Результат — таблица «фаза → (d_i1, d_i2, d_ires)», которая является входом для правки `ls`
(отдельный пакет, конвенция привода/чтения).

## 3. Контракт (совместимый вывод + две новые строки)

Формат `@DBG:*` сохраняется полностью (совместимость с историей и раннерами):

```
@DBG:CHU:START
@DBG:CHU:ZERO:i1=<мА>:i2=<мА>:ires=<мА>:vbus=<мВ>
@DBG:CHU:TEST:i1=<мА>:i2=<мА>:ires=<мА>:vbus=<мВ>
@DBG:CHU:DELTA:d_i1=<мА>:d_i2=<мА>:d_ires=<мА>
@DBG:CHU:OK
```

Добавляется (чтобы по логу было видно, что именно подано — по образцу `ls`):

```
@DBG:CH<U|V|W>:ARM:arr=<u16>:ccr_hi=<u16>:ccr_lo=<u16>:d_pct=5:vbus_mv=<мВ>
```

Аварийные строки (все — завершение команды, `retcode != 0`):

```
@DBG:CHx:ERROR:SAFETY              (AT_SafetyCheck отказал: fault/VFC/VBUS вне окна)
@DBG:CHx:ERROR:ADC_ARM_FAIL        (ADC_InjectedStart() != 0)
@DBG:CHx:ERROR:ARM_FAIL:<знаковый код>  (PWM_ServiceCaptureStart != PWM_ENABLE_OK; печатать %d, не %u)
@DBG:CHx:ABORT                     (g_autotune_abort во время цикла)
```

## 4. Что именно делать (минимальная правка)

Правка только в `Autotune_ProbePhase()`; структура функции, тексты и порядок строк сохраняются.

1. `AT_SafetyCheck()` — как сейчас, ДО любого возбуждения; при отказе `@DBG:CHx:ERROR:SAFETY`.
2. `FOC_Stop()` при работающем FOC — как сейчас. `NVIC_DisableIRQ(ADC1_2_IRQn)`, `PWM_Disable()`,
   `ADC_InjectedStop()`, `ADC_CalibrateOffsets()` — как сейчас.
3. Вместо `PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_enable();` — сервисный вход:

```c
uint16_t arr   = PWM_GetARR();
uint32_t period= (uint32_t)arr + 1U;
uint16_t half  = (uint16_t)(period / 2U);
uint16_t ccr   = (uint16_t)(((uint32_t)5U * (period - 1U)) / 100U);  /* 5 % */
uint16_t hi    = (uint16_t)(half + ccr);

PWM_SetDuty1(0, 0, 0);
PWM_SetDuty2(100, 100, 100);
PWM_TriggerHigh();

PwmServiceCapturePattern pattern;
memset(&pattern, 0, sizeof(pattern));
for (uint8_t i = 0; i < 3U; i++) {
    pattern.tim1_ccr[i] = half;      /* нейтраль */
    pattern.tim8_ccr[i] = half;
}
pattern.tim1_ccr[phase] = hi;        /* возбуждается ОДНА фаза */
pattern.tim8_ccr[phase] = hi;        /* оба инвертора ОДИНАКОВО (общая мода, как в OEW-тесте) */
pattern.sector_candidate = phase;    /* диагностические метки, как в ls */
pattern.window_candidate = 0U;
pattern.trigger_revision = PWM_OEW_ADC_TRIGGER_REVISION;

/* Порядок арма — как в map capture: сначала ADC, затем сервисный PWM. */
if (ADC_InjectedStart() != 0) { /* @DBG:CHx:ERROR:ADC_ARM_FAIL; TriggerLow; break; */ }
int rc = PWM_ServiceCaptureStart(&pattern);
if (rc != PWM_ENABLE_OK) { /* @DBG:CHx:ERROR:ARM_FAIL:%d; TriggerLow; break; */ }
```

4. Порог VBUS: тот же конверт, что у `ls` — **24 000 мВ** (не полагаться на 12 000 мВ из
   `AT_SafetyCheck`): если `ADC_ReadVbusRegularMv() < 24000`, выходить с
   `@DBG:CHx:ERROR:VBUS_LOW:<мВ>` **до** арма. (Инжектированный VBUS до старта ШИМ может
   читаться 0 — читать регулярной конверсией.)
5. Измерения — как сейчас: `ZERO` = медиана из 5 регулярных конверсий по каждому из трёх
   каналов; `TEST` = адаптивный цикл до 20 мс (шаг 500 мкс), выход при `|Δ| ≥ 50 мА` по любому
   каналу или `|I| > 2000 мА`. **Добавить** проверку `g_autotune_abort` в каждой итерации
   цикла (сейчас её нет) → `@DBG:CHx:ABORT`.
6. Завершение на ЛЮБОМ выходе: `PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); PWM_Disable();
   PWM_TriggerLow(); ADC_InjectedStop();` и восстановление `NVIC_EnableIRQ(ADC1_2_IRQn)`
   по прежнему состоянию (как в `ls`), затем `@DBG:CHx:OK` при успехе.
7. `main.c`, `src/cli.c`, `src/cli.h`, `src/autotune.h` **не менять**: `chu/chv/chw` уже
   диспетчеризуются (`main.c:508`, `cli.c:252-257`), тексты `@AT:CHx:OK`/`@AT:CHP:FAIL`
   остаются на месте.

## 5. Объём изменений

```
src/autotune.c        ТОЛЬКО Autotune_ProbePhase()
tests/cli_test.c      одна проверка парсинга chu/chv/chw -> CLI_AT_CHU/CHV/CHW (без железа)
```

Запрещено трогать: `both_enable()` и `both_disable()` (должны остаться как есть),
`Autotune_DetectChannel()`, `Autotune_MeasureLs_OEW()`, `Autotune_MeasureLs_Position()`,
`Autotune_LsStep()`, прочие энергии.тесты, `src/foc.c`, `src/pwm.c`, `src/pwm.h`, `src/adc.c`,
`src/protect.c`, `src/adc_dispatch.c`, `src/map_capture*.c`, `main.c`, `Makefile`, `*.ioc`.

## 6. Конверт и безопасность

- VBUS 24…36 В, подача звена — по обычной процедуре стенда (DC link OFF → LOTO → ≥ 60 с → DMM < 1 В).
- Возбуждение: одна фаза, 5 % (при `arr=999` это ±49 отсчётов от нейтрали), ≤ 20 мс на команду.
- Ожидаемые токи: десятки–сотни мА (ограничены сопротивлением обмотки); аппаратный предел
  2 А внутри цикла, плюс BKIN/EM_STOP и `AT_SafetyCheck` — без изменений.
- Никаких изменений в gate-логике, порядке включения, dead-time, защитах и `.ioc`.

## 7. Проверки (обязательно, до публикации)

1. `make` — чисто; `make test` — PASS (QEMU/hosted/pytest);
2. `python scripts/cubemx_check.py` — PASS (периферия не менялась);
3. дифф: изменены только `src/autotune.c` и `tests/cli_test.c`; `both_enable()` побайтово прежняя;
4. CI build-test — success;
5. на стенде: три команды (`chu`, `chv`, `chw`) подряд, полный лог, после каждой —
   контроль `CCER=0`, `MOE=0`, `@BRK:valid=0`, `@MC:STATUS state=0`.

## 8. Что докажет и что не докажет

Докажет: какой канал отвечает на возбуждение каждой фазы и с каким знаком; какой канал
пригоден для измерения тока обмотки; воспроизводимость (повтор трижды).
**Не докажет**: Ls, пригодность карты OEW, корректность момента выборки ADC (окно/сектор —
отдельный вопрос), номинал Rs.

## 9. Ответ/отчёт исполнителя

1. Дифф (только разрешённые файлы), `make`, `make test`, `cubemx_check`, номер CI-прогона.
2. Подтверждение, что `both_enable()` не изменена (хеш функции/строки).
3. Явное указание, что на железе НЕ запускалось (запуск — только на стороне стенда).
