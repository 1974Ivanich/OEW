# ТЗ: first-break diagnostics для TIM1/TIM8 BKIN

**Статус:** отдельное ТЗ; реализацию не совмещать с изменением BOAR-профиля или
процедуры включения питания.

**Причина:** 04.09.2026 на ПК-3 `PROTECT_FAULT_HARDWARE_BREAK` (`FAULT_R=18`)
защёлкнулся при подаче 10 В DC-link до любой команды MapCapture и при
`CCER=0/MOE=0`. После ISR линии SD уже были high, а общий fault reason не
сохранил источник TIM1/TIM8. Burst-overcurrent для этого события исключён;
источник power-on `FAULT_N` не установлен.

## 1. Цель

Не меняя fail-closed поведения, сохранить **первый** аппаратный break event до
очистки `BIF/B2IF` и сделать его доступным после события через read-only CLI.
Диагностика должна различать TIM1 и TIM8 и показывать физическое состояние
SD1/SD2 в момент входа в ISR.

## 2. Safety invariants (P0)

Реализация обязана сохранить без изменений:

1. Аппаратный BKIN включён (`BKE=1`) в commissioning и production; запрещено
   включать `OEW_SD_MONITOR_ONLY` или менять полярность/фильтр BKIN.
2. Первый вызов существующего terminal safety path остаётся немедленным:
   `PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK)` + `PWM_Disable()` +
   `FOC_Stop()` + `MapCapturePort_OnProtectionLatched()`.
3. Никакого UART, форматирования, dynamic allocation, flash write, ожидания или
   циклов в break ISR.
4. Диагностика не может снять fault, включить PWM, изменить timer/GPIO/ADC
   registers или влиять на результат `PROTECT_RequestClear()`.
5. Запись диагностики bounded O(1). Если данные уже valid, последующие break
   events не перезаписывают первый event до explicit diagnostic reset.
6. Diagnostic reset разрешён только при PWM off и не снимает central fault.
7. Default-deny и production behavior остаются fail-closed.

## 3. Предлагаемый API

Новый независимый модуль:

```text
src/break_diagnostics.h
src/break_diagnostics.c
```

Минимальная структура (точные имена можно адаптировать при реализации):

```c
typedef enum {
    BREAK_DIAG_SOURCE_NONE = 0,
    BREAK_DIAG_SOURCE_TIM1 = 1,
    BREAK_DIAG_SOURCE_TIM8 = 2
} BreakDiagSource;

typedef struct {
    uint32_t sequence;
    uint32_t timestamp_cycles;
    uint32_t tim1_sr;
    uint32_t tim8_sr;
    uint32_t tim1_bdtr;
    uint32_t tim8_bdtr;
    uint32_t tim1_ccer;
    uint32_t tim8_ccer;
    uint16_t tim1_cnt;
    uint16_t tim8_cnt;
    uint32_t capture_id;
    uint16_t capture_frames;
    uint8_t source;
    uint8_t sd1_high;
    uint8_t sd2_high;
    uint8_t capture_state;
    uint8_t valid;
} BreakDiagnostics;

void BreakDiagnostics_RecordFromIsr(BreakDiagSource source,
                                    const BreakDiagnostics *snapshot);
bool BreakDiagnostics_Get(BreakDiagnostics *out);
bool BreakDiagnostics_Reset(void);
```

Требования:

- `RecordFromIsr()` получает/фиксирует данные **до очистки SR flags**.
- `timestamp_cycles = DWT->CYCCNT`; ноль допустим и означает disabled DWT.
- `tim1_sr/tim8_sr` сохраняются целиком либо как минимум `BIF|B2IF`; исходные
  значения должны быть записаны до `TIMx->SR &= ~flags`.
- `sd1_high/sd2_high` читаются напрямую через существующие read-only helpers
  `PWM_EmStop1IsHigh()` / `PWM_EmStop2IsHigh()`.
- `capture_state`, `capture_id`, `capture_frames` берутся через новый короткий
  ISR-safe read-only snapshot API MapCapture либо из существующих volatile
  counters; запрещено consume/reset capture data.
- publication выполняется last (`valid=1`) после memory barrier; foreground
  getter должен видеть целостный snapshot.
- если оба ISR пришли от одного физического импульса, сохраняется первый ISR;
  `sequence` увеличивается только при принятой первой записи после reset.

## 4. Изменения break ISR

Оба handler должны:

1. прочитать TIM1/TIM8 SR, GPIO SD state, timer state и capture context;
2. попытаться сохранить first-break snapshot;
3. очистить флаги;
4. выполнить существующий safety path без изменения порядка/условий.

Запрещено объединять обработчики так, чтобы повысилась latency или изменилась
семантика NVIC.

## 5. CLI

Добавить read-only команду, например:

```text
breakdiag
```

Формат одной bounded строки:

```text
@BRK:valid=1:seq=1:src=TIM1:cyc=12345:t1sr=0x80:t8sr=0x0:sd1=0:sd2=1:t1bdtr=0x...:t8bdtr=0x...:t1ccer=0x0:t8ccer=0x0:t1cnt=0:t8cnt=0:cap_state=0:cap_id=0:frames=0
```

Если события нет:

```text
@BRK:valid=0
```

Reset диагностики — отдельная явная команда (`breakdiag reset`) или API,
доступная только при PWM off. Reset не должен вызывать `PROTECT_RequestClear()`.

## 6. Тесты

Добавить hosted-тест `tests/break_diagnostics_test.c` и подключить к
`make test-hosted`.

Обязательные случаи:

1. no event → `valid=0`;
2. TIM1 BIF + SD1 low → source TIM1 и исходные flags/state сохранены;
3. TIM8 BIF + SD2 low → source TIM8;
4. SD уже high при ISR → flags всё равно сохраняют источник;
5. второй event не перезаписывает первый;
6. reset при PWM off очищает только diagnostic valid state;
7. reset при PWM enabled отклоняется;
8. central PROTECT fault остаётся latched после diagnostic reset;
9. break ISR по-прежнему приводит к `FAULT_R=18`, `MOE=0`, `CCER=0`, capture
   terminal stop;
10. capture idle snapshot даёт state/id/frames=0; active snapshot сохраняет
    актуальные значения;
11. CLI golden output для valid/invalid snapshots;
12. default-deny compile regression: diagnostic module не создаёт пути re-arm.

## 7. Разрешённый scope файлов

```text
main.c
src/break_diagnostics.c
src/break_diagnostics.h
src/map_capture.c
src/map_capture.h
src/cli.c или существующий CLI registry file
Makefile
tests/break_diagnostics_test.c
tests/cli_test.c (только snapshot команды)
tests/* mocks, необходимые для hosted build
```

`src/pwm.c`, `src/protect.c`, `.ioc`, pin mapping, BKIN configuration и BOAR
profile вне scope. Если реализация требует их менять — STOP и отдельное ревью.

## 8. Verification

Перед публикацией:

```text
make
make test-hosted
python -m pytest tests -q
python scripts/cubemx_check.py
```

На ПК-3 после принятия реализации разрешается только новый 10 В
**energize-only** diagnostic с логическим trigger по falling edge SD1/SD2. Burst
остаётся запрещённым до классификации power-on fault.

## 9. Acceptance criteria

Пакет принимается только если:

- CI green;
- safety invariants подтверждены тестами;
- break ISR не содержит UART/loops и сохраняет snapshot до очистки flags;
- reviewer подтверждает отсутствие изменений BKIN/SD/default-deny;
- operator runbook обновлён форматом `@BRK` и reset prohibition;
- никакое утверждение о причине STEVAL fault не делается без стендового trace.
