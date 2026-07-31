# ТЗ — Передача параметров автотюнинга в FOC-управление

## Цель

Передать измеренные Auto-Tune параметры (Rs, Ls, Rr, Lm, Tr, Ke, pole_pairs, J, Kp/Ki) из `nucleo_debug_tool.py` (Auto-Tune вкладка) в `foc_control_gui.py` и применить их в алгоритме FOC на STM32.

**Текущее состояние:**
- `autotune.c` хранит результаты в `g_motor_params` (структура `MotorParams`)
- `foc.c` использует **жёсткие константы**: `FOC_DEFAULT_R_MOHM 50`, `FOC_DEFAULT_L_UH 100`, `FOC_DEFAULT_PI_KP 2000`, `FOC_DEFAULT_PI_KI 100` и т.д.
- `BEMF_Init(&observer, R, L, Ts, Vdc)` инициализируется дефолтами в `FOC_Init()` и `FOC_Start()`
- GUI: `foc_control_gui.py` — управление FOC (старт/стоп, скорость, pole pairs через `pp=`)

---

## 1. Прошивка (STM32) — команды применения параметров

### 1.1. Новая функция в `foc.c` / `foc.h`

```c
/* Применить параметры мотора из автотюнинга.
 * Вызывать при остановленном FOC.
 * Возвращает 0 = OK, -1 = FOC запущен, -2 = невалидные параметры. */
int FOC_SetMotorParams(int32_t r_mohm, int32_t l_uh, int32_t vdc_mv);

/* Применить коэффициенты PI-регуляторов тока. */
int FOC_SetPIGains(int32_t kp, int32_t ki);
```

Реализация:
- `FOC_SetMotorParams()`:
  - если `foc_running` → return -1
  - если `r_mohm < 1 || l_uh < 1` → return -2
  - сохранить в статические переменные `motor_R_mOhm`, `motor_L_uH`
  - вызвать `BEMF_Init(&observer, motor_R_mOhm, motor_L_uH, FOC_DEFAULT_TS_US, vdc_mv)`
  - return 0
- `FOC_SetPIGains(kp, ki)`:
  - если `foc_running` → return -1
  - `PI_Init(&pi_d, kp, ki, 32767, -32768)`
  - `PI_Init(&pi_q, kp, ki, 32767, -32768)`
  - return 0

В `FOC_Start()` заменить `BEMF_Init(... FOC_DEFAULT_R_MOHM, FOC_DEFAULT_L_UH ...)` на использование сохранённых `motor_R_mOhm`/`motor_L_uH` (если не заданы — дефолты).

### 1.2. Команда `mp` в `main.c` (Motor Params)

```
mp=185,2500,120,2400,5000,50,4,100
     │    │    │    │     │  │ │  │
     Rs   Ls   Rr   Lm    Tr Ke p  J
```

Парсинг (8 значений, все опциональны кроме Rs/Ls):
```c
else if(sscanf(linebuf, "mp=%d,%d,%d,%d,%d,%d,%d,%d",
        &a1,&a2,&a3,&a4,&a5,&a6,&a7,&a8) >= 2) {
    int rc = FOC_SetMotorParams(a1, a2, (int32_t)ADC_GetVbus_mV());
    if(rc == 0) {
        FOC_SetPolePairs(a7);          /* пары полюсов */
        UART_SendTelemetry("@MP:OK:Rs=%d:Ls=%d:Rr=%d:Lm=%d:Tr=%d:Ke=%d:p=%d:J=%d\r\n> ", a1,a2,a3,a4,a5,a6,a7,a8);
    } else UART_SendTelemetry("@MP:ERROR:%d\r\n> ", rc);
}
```

### 1.3. Команда `pi` — расширить

Уже есть `pi=N` (расчёт Kp/Ki из измеренных Ls/Rs). Добавить:
```
pi apply     — применить последние расчётные Kp/Ki в PI-регуляторы (FOC_SetPIGains)
```
Или `pi=N` сразу применяет: после `Autotune_CalcPI(N)` вызвать `FOC_SetPIGains(kp, ki)`.

### 1.4. Команда `params` — выводить флаг применения

После `@PARAMS:...` добавить поле `AP=1` (applied) / `AP=0` — применены ли параметры в FOC.

---

## 2. GUI `nucleo_debug_tool.py` — AutoTuneTab

### 2.1. Кнопка "➡ Apply to FOC"

В секции Results добавить:
```python
self.btn_apply = ttk.Button(res_f, text="➡ Apply to FOC", command=self._apply_to_foc)
```

Метод:
```python
def _apply_to_foc(self):
    """Отправить измеренные параметры в FOC (команда mp=)."""
    p = self._params
    if not p or 'Rs' not in p or 'Ls' not in p:
        self._log_local("[AT] No params — run idle first", "error")
        return
    rs = p.get('Rs', 0)
    ls = p.get('Ls', 0)
    rr = p.get('Rr', 0)
    lm = p.get('Lm', 0)
    tr = p.get('Tr', 0)
    ke = p.get('Ke', 0)
    pp = p.get('p', 4)
    j  = p.get('J', 0)
    self.send(f"mp={rs},{ls},{rr},{lm},{tr},{ke},{pp},{j}")
    self._log_local(f"[AT] Applied to FOC: Rs={rs}mΩ Ls={ls}µH p={pp}", "sent")
```

### 2.2. Автоприменение после idle

В `on_line()` после `@IDLE:DONE` + валидации добавить:
```python
if self._params.get('Rs', 0) > 0 and self._params.get('Ls', 0) > 0:
    self._apply_to_foc()
```

Или чекбокс "Auto-apply after test" (default OFF — безопасность).

### 2.3. Обработка `@MP:OK` / `@MP:ERROR`

```python
if line.startswith("@MP:OK"):
    self._log_local("[AT] Motor params applied to FOC ✓", "tlm")
    return True
if line.startswith("@MP:ERROR"):
    self._log_local(f"[AT] Apply failed: {line}", "error")
    return True
```

---

## 3. GUI `foc_control_gui.py` — применение параметров

### 3.1. Кнопка "Apply measured params"

В FOC GUI добавить кнопку, которая:
1. Спрашивает путь к CSV (или читает последний `autotune_*.csv`)
2. Парсит Rs/Ls/Rr/Lm/Tr/Ke/p/J
3. Отправляет `mp=...` через `_send()`

Или проще: общий `shared_params.json` — файл, куда `nucleo_debug_tool.py` пишет результаты после `@IDLE:DONE`, а `foc_control_gui.py` читает при старте:

```json
{
  "Rs_mOhm": 850,
  "Ls_uH": 620,
  "Rr_mOhm": 120,
  "Lm_uH": 2400,
  "Tr_us": 5000,
  "Ke_mV_rpm": 50,
  "pole_pairs": 4,
  "J_kg_m2_x1e6": 100,
  "Kp": 125,
  "Ki": 5
}
```

- `nucleo_debug_tool.py` пишет `autotune_params.json` после `@IDLE:DONE` (и после `@AT:PI:`)
- `foc_control_gui.py` при старте читает его, показывает в отдельной секции "Motor params (autotuned)", кнопка "Apply" отправляет `mp=...` + `pi apply`

---

## 4. Использование параметров в алгоритме FOC

### 4.1. Observer (BEMF)

`BEMF_Init(&observer, R, L, Ts, Vdc)` — главное применение Rs и Ls. Правильные R/L дают точную оценку EMF → точный угол → стабильная работа.

### 4.2. PI-регуляторы тока

`Autotune_CalcPI(bw)` считает:
```
Kp = 2π·bw·Ls / √3
Ki = 2π·bw·Rs / √3
```
Применять через `FOC_SetPIGains(kp, ki)`. Рекомендуемый bw = 800 Гц.

### 4.3. Контур скорости

`FOC_SPD_KP/KI` — оставить дефолтными или задать через команду `spdpi=kp,ki` (опционально).

### 4.4. Flux Weakening

`FW_Init(&fw, VDC, kp, ki)` — уже использует `FOC_DEFAULT_VDC_MV`. Обновлять Vdc из `ADC_GetVbus_mV()` на каждом цикле (уже есть: `fw.vdc_mv = ADC_GetVbus_mV()`).

---

## 5. Формат телеметрии (итог)

```
> idle
@IDLE:START
...
@AT:STAT:Rs=850:820:890:8%:Ls=620:610:635:4%:Isat=2600:2500:2700:7%
@PARAMS:Rs=850:Ls=620:Isat=2600:Rr=120:Lm=2400:Tr=5000:Ke=50:p=4:J=100:CH=3:AP=0
@IDLE:DONE
@IDLE:OK

> pi=800
@AT:PI:BW=800:Kp=125:Ki=5:Ls=620:Rs=850
pi apply        ← или pi=800 сразу применяет

> mp=850,620,120,2400,5000,50,4,100
@MP:OK:Rs=850:Ls=620:Rr=120:Lm=2400:Tr=5000:Ke=50:p=4:J=100
```

---

## 6. Порядок работы пользователя

```
1. run_debug_tool.bat → Auto-Tune → ▶ Rs/Ls/Isat (5x) → ждёт @IDLE:DONE
2. ▶ Calc Kp/Ki (bw=800) → получает Kp/Ki
3. ➡ Apply to FOC → отправляет mp=... + pi → @MP:OK
4. (опционально) pi=800 применяет Kp/Ki
5. Открыть foc_control_gui.py → (читает autotune_params.json) → Apply → FOC стартует с правильными параметрами
```

---

## 7. Файлы для модификации

| Файл | Изменения |
|------|-----------|
| `src/foc.c` / `foc.h` | `FOC_SetMotorParams()`, `FOC_SetPIGains()`, сохранение R/L, применение в FOC_Start |
| `main.c` | Команды `mp=...`, `pi apply` (или автоприменение), поле `AP=` в params |
| `nucleo_debug_tool.py` | Кнопка "➡ Apply to FOC", парсинг `@MP:OK/ERROR`, запись `autotune_params.json` |
| `foc_control_gui.py` | Чтение `autotune_params.json`, кнопка Apply, отображение параметров |

---

## 8. Критерии приёмки

1. `mp=850,620,...` → `@MP:OK` при остановленном FOC
2. `mp=...` при запущенном FOC → `@MP:ERROR:-1`
3. После `mp=` команда `dump` показывает обновлённые R/L в observer (добавить R/L в dump)
4. `pi=800` → `@AT:PI:` с Kp/Ki, и PI-регуляторы используют новые коэффициенты
5. GUI: кнопка Apply отправляет корректную команду, `@MP:OK` логируется
6. `foc_control_gui.py` показывает параметры из JSON и применяет их
