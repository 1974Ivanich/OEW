# ТЗ — Auto-Tune v2: канал тока, Multi-point Rs, безопасность

## Файлы для модификации

- `src/autotune.c` / `src/autotune.h`
- `main.c` — CLI: `ch`, `iv`, `abort`
- `nucleo_debug_tool.py` — GUI: новые кнопки + парсинг

---

## 1. Автоопределение канала тока — команда `ch`

Подать короткий импульс 5% duty на фазу A→B, измерить отклик по всем трём каналам (I1, I2, IN). Выбрать канал с максимальным `|I_test - I_zero|`.

```c
typedef enum { AT_CH_UNKNOWN=0, AT_CH_I1, AT_CH_I2, AT_CH_IN } AtCurrentChannel;
typedef struct { AtCurrentChannel channel; int32_t sign; int32_t test_current_ma; } AtChannelDetect;
extern AtChannelDetect g_at_ch;
```

Функция `Autotune_DetectCurrentChannel()`:
- Отключает FOC/PWM, калибрует offset
- Читает I1_zero, I2_zero, IN_zero
- Подаёт импульс 5% duty на 300 мкс
- Читает I1_test, I2_test, IN_test
- Выбирает канал с max `|diff|`
- Определяет sign (+1 или -1)
- Сохраняет в `g_at_ch`
- Выводит `@AT:CH_DETECT:OK:CH=3:I=412:SIGN=1`

Обёртка для чтения:
```c
static int32_t AT_ReadCurrent_mA(void) {
    int32_t i=0;
    switch(g_at_ch.channel){
        case AT_CH_I1: i=ADC_GetI1_mA(); break;
        case AT_CH_I2: i=ADC_GetI2_mA(); break;
        case AT_CH_IN: i=ADC_GetIN_mA(); break;
        default: i=ADC_GetIN_mA(); break;
    }
    return i * g_at_ch.sign;
}
```

**CLI:** `ch` → вызывает `Autotune_DetectCurrentChannel()`
**GUI:** кнопка "🔍 Detect Channel"

---

## 2. Multi-point Rs (I-V характеристика) — команда `iv`

Заменить одноточечный Rs на линейную регрессию по 7 точкам duty: 2, 4, 6, 8, 10, 12, 15%.

Для каждого duty:
- Установить duty, ждать 1000 мкс
- Прочитать I_ss = `AT_ReadCurrent_mA()`
- Прочитать Vbus
- U = Vbus * duty / 100

Линейная регрессия: U = R_pp * I + U_offset
```c
// sum_i, sum_u, mean_i, mean_u
// R_pp = sum(du*di) / sum(di*di)
// Rs_phase = R_pp / 2
```

Функция `Autotune_MeasureRs_IV()`:
- Возвращает Rs в `g_motor_params.Rs_mOhm`
- Выводит `@AT:RS_IV:POINT:D=2:U=480:I=85`
- Выводит `@AT:RS_IV:OK:Rs=852`

**CLI:** `iv` → вызывает `Autotune_MeasureRs_IV()`
**GUI:** кнопка "📈 Rs I-V"

---

## 3. Безопасность — добавить в `Autotune_Idle()`

### 3.1 Проверка нулевого тока перед стартом
```c
if(i1>100||i2>100||in>100){ UART_SendStr("@IDLE:ERROR:NONZERO_CURRENT\r\n"); return -1; }
```

### 3.2 Детект обрыва фазы
```c
if(duty_pct>=20 && I_ss<30){ UART_SendStr("@IDLE:ERROR:OPEN_PHASE\r\n"); return -1; }
```

### 3.3 Детект КЗ / очень низкого Rs
```c
if(duty_pct<=2 && I_ss>3000){ UART_SendStr("@IDLE:ERROR:SHORT_CIRCUIT\r\n"); return -1; }
```

### 3.4 Контроль Vbus на каждом шаге
```c
int32_t vbus_now = ADC_GetVbus_mV();
if(vbus_now < vbus_start * 85 / 100){ UART_SendStr("@AT:WARN:VBUS_SAG\r\n"); }
// Использовать vbus_now в U_applied
```

### 3.5 Abort — глобальный флаг
```c
volatile uint8_t g_at_abort = 0;
```
В цикле idle проверять `if(g_at_abort)` — выход с `@IDLE:ABORTED`.
Команда `abort` → `g_at_abort = 1`.

**GUI:** кнопка "⏹ Abort" (активна во время теста)

---

## 4. Формат телеметрии (новый)

```c
@AT:CH_DETECT:OK:CH=3:I=412:SIGN=1
@AT:RS_IV:POINT:D=2:U=480:I=85
@AT:RS_IV:POINT:D=4:U=960:I=162
@AT:RS_IV:OK:Rs=852
@IDLE:PROG=5/50:D=5:I=244:L=629:VBUS=24001
@IDLE:ERROR:OPEN_PHASE
@IDLE:ABORTED
```

**GUI — новые элементы AutoTuneTab:**
- Кнопка "🔍 Detect Channel" → `ch`
- Кнопка "📈 Rs I-V" → `iv`
- Кнопка "⏹ Abort" → `abort` (state=disabled в покое, normal во время теста)
- После `@IDLE:DONE` — отобразить `Rs I-V` результат
- Progress бар (ttk.Progressbar) + парсинг `@IDLE:PROG=`

---

## 5. Порядок idle-теста (изменённый)

```
1. Проверка нулевого тока (3.1)
2. Автоопределение канала (если не определён ранее)
3. Цикл 1..50% duty с AT_ReadCurrent_mA()
   - Проверка обрыва/КЗ/abort на каждом шаге
   - Vbus_now на каждом шаге
   - @IDLE:PROG= на каждом шаге
4. Multi-point Rs (Autotune_MeasureRs_IV) — использует те же данные
5. Вывод @PARAMS: + @IDLE:CURVE:
6. @IDLE:DONE
```
