# OEW Motor FOC — Implementation Plan

> **Goal:** Программа управления асинхронным двигателем с разомкнутой обмоткой (OEW) на STM32G474RE. Два инвертора, CORDIC для тригонометрии, Back-EMF Observer + PLL для оценки скорости/положения.

**Architecture:** Sensorless FOC (Field Oriented Control) на CMSIS-only. Два двухуровневых инвертора с общим звеном постоянного тока, управление через TIM1+TIM8. CORDIC вычисляет sin/cos и atan2. Back-EMF Observer в αβ-координатах с PLL.

**Tech Stack:** STM32G474RE, CMSIS, CORDIC, USART2 115200, ADC2 (I1, I2, IN, Vbus), TIM1+TIM8 ШИМ 5 кГц center-aligned.

**Hardware распиновка:**
- TIM1: PC0(HIN_U1), PA7(LIN_U1), PC1(HIN_V1), PB0(LIN_V1), PC2(HIN_W1), PB1(LIN_W1)
- TIM8: PC6(HIN_U2), PC10(LIN_U2), PC7(HIN_V2), PC11(LIN_V2), PC8(HIN_W2), PC12(LIN_W2)
- ADC2: PA0(I1), PA1(I2), PA6(IN), PC4(Vbus)
- UART: PA2(TX), PA3(RX) — 115200
- EN: PB4(EN1), PB5(EN2)

---

## Task 1: Подготовка проекта и Makefile

**Objective:** Создать структуру проекта с модулями: `main.c`, `foc.c/h`, `cordic_math.c/h`, `observer.c/h`, `pll.c/h`, `pwm.c/h`, `adc.c/h`, `uart.c/h`.

**Files:**
- Create: `C:/ST/boyler/Motor/src/foc.h`
- Create: `C:/ST/boyler/Motor/src/foc.c`
- Create: `C:/ST/boyler/Motor/src/cordic_math.h`
- Create: `C:/ST/boyler/Motor/src/cordic_math.c`
- Create: `C:/ST/boyler/Motor/src/observer.h`
- Create: `C:/ST/boyler/Motor/src/observer.c`
- Create: `C:/ST/boyler/Motor/src/pll.h`
- Create: `C:/ST/boyler/Motor/src/pll.c`
- Create: `C:/ST/boyler/Motor/src/pwm.h`
- Create: `C:/ST/boyler/Motor/src/pwm.c`
- Create: `C:/ST/boyler/Motor/src/adc.h`
- Create: `C:/ST/boyler/Motor/src/adc.c`
- Create: `C:/ST/boyler/Motor/src/uart.h`
- Create: `C:/ST/boyler/Motor/src/uart.c`
- Modify: `C:/ST/boyler/Motor/main.c` — минимальный main, вызывает инициализацию модулей
- Modify: `C:/ST/boyler/Motor/Makefile` — пути к src/, список C_SOURCES

**Ключевое:** каждая функция — отдельный модуль с .h/.c. Все модули собираются в один Makefile.

---

## Task 2: PWM модуль — настройка TIM1 и TIM8

**Objective:** Абстрагировать управление ШИМ в модуль `pwm.c/h`. TIM1 для инвертора 1, TIM8 для инвертора 2.

**Files:**
- Create/Modify: `C:/ST/boyler/Motor/src/pwm.h`
- Create/Modify: `C:/ST/boyler/Motor/src/pwm.c`

**API:**
```c
void PWM_Init(void);
void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w);  // duty для TIM1, 0-ARR
void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w);  // duty для TIM8
void PWM_Enable(void);
void PWM_Disable(void);
```

**Детали:**
- Таймеры уже настроены из прошлой версии: center-aligned, ARR=99, PSC=15
- TIM1: PC0/PC1/PC2 + PA7/PB0/PB1
- TIM8: PC6/PC7/PC8 + PC10/PC11/PC12
- функция `PWM_SetDuty` записывает CCR1/2/3 и CCER для нужных каналов
- OEW: ток идёт от инвертора 1 к инвертору 2. Вектор напряжения = V1 - V2.
  Для нулевого вектора: все верхние ключи одного инвертора + все нижние другого.
- Добавить BDTR = DTG|AOE, CEN, MOE при Enable.

---

## Task 3: CORDIC модуль — быстрая тригонометрия

**Objective:** Использовать аппаратный CORDIC STM32G474 для sin/cos, atan2, модуля.

**Files:**
- Create: `C:/ST/boyler/Motor/src/cordic_math.h`
- Create: `C:/ST/boyler/Motor/src/cordic_math.c`

**API:**
```c
void CORDIC_Init(void);
int32_t CORDIC_Sin(int32_t angle_q31);   // angle в Q31 (2π = 0x7FFFFFFF)
int32_t CORDIC_Cos(int32_t angle_q31);
int32_t CORDIC_Atan2(int32_t y, int32_t x); // возвращает угол Q31
void CORDIC_Modulus(int32_t x, int32_t y, int32_t *mod, int32_t *angle); // модуль + угол
```

**Регистры CORDIC (RM0440 гл. 17):**
- `CORDIC->CSR` — управление (0 для sin/cos, 1 для atan2, бит 4 для модуля)
- `CORDIC->WDATA` — запись данных (x, затем y)
- `CORDIC->RDATA` — чтение результата (cos/sin или atan/mod)
- `CORDIC->CSR |= CORDIC_CSR_FUNC` — выбор функции

**Проверка:** CORDIC есть на G474 (CORDIC периферия). Доступен по адресу `CORDIC_BASE`.

---

## Task 4: ADC модуль — чтение токов и напряжения

**Objective:** Модуль `adc.c/h` для чтения ADC2 (I1, I2, IN, Vbus) с синхронизацией по ШИМ.

**Files:**
- Create: `C:/ST/boyler/Motor/src/adc.h`
- Create: `C:/ST/boyler/Motor/src/adc.c`

**API:**
```c
void ADC_Init(void);
void ADC_StartConversion(void);
uint16_t ADC_Get_I1(void);   // PA0, IN1
uint16_t ADC_Get_I2(void);   // PA1, IN2
uint16_t ADC_Get_IN(void);   // PA6, IN3
uint16_t ADC_Get_Vbus(void); // PC4, IN5
void ADC_WaitForEOC(void);
```

**Детали:**
- Использовать ADC2 (как в предыдущей версии).
- Синхронизировать с ШИМ: запуск конверсии по триггеру TIM1 (TRGO).
- Режим injected group для захвата всех каналов за один цикл ШИМ.
- Либо последовательный single-shot: ADC2_ReadAvg() как раньше.

---

## Task 5: Модуль Clarke/Park преобразований

**Objective:** Преобразование трёхфазных токов в αβ (Clarke) и dq (Park) с CORDIC sin/cos.

**Files:**
- Create: `C:/ST/boyler/Motor/src/foc.h` (частично)
- Create: `C:/ST/boyler/Motor/src/foc.c`

**Функции:**
```c
// Структуры
typedef struct {
    int32_t alpha; // Q15
    int32_t beta;
} AlphaBeta;

typedef struct {
    int32_t d; // Q15
    int32_t q;
} DQ;

// Clarke: Iu, Iv, Iw → Iα, Iβ
AlphaBeta Clarke_Transform(int32_t iu, int32_t iv, int32_t iw);

// Park: Iα, Iβ, θ → Id, Iq
DQ Park_Transform(int32_t alpha, int32_t beta, int32_t theta_q31);

// Inverse Park: Vd, Vq, θ → Vα, Vβ
AlphaBeta InvPark_Transform(int32_t vd, int32_t vq, int32_t theta_q31);

// Inverse Clarke: Vα, Vβ → Vu, Vv, Vw
void InvClarke_Transform(int32_t valpha, int32_t vbeta, int32_t *vu, int32_t *vv, int32_t *vw);
```

OEW топология: токи измеряются как I1 (общий инвертор 1) и I2 (общий инвертор 2).
Iu = I1, Iv = I2, Iw = -(I1+I2) — для каждого инвертора?
Или: фазы U,V,W — это разность токов инверторов:
- Iu = I1u - I2u (но у нас нет пофазных токов)
- Общий ток двигателя через каждую фазу = ток фазы U инвертора 1 минус ток фазы U инвертора 2.

**Уточнение:** При OEW ток измеряется как общий ток инвертора 1 (I1) и общий ток инвертора 2 (I2). Нулевой ток IN даёт информацию о синфазной составляющей. Для FOC нужны фазные токи, поэтому либо:
1. Установить шунты на фазах (требует 3 шунта на инвертор)
2. Либо использовать 2 шунта + закон Кирхгофа

Пока закладываем API для 3-фазного преобразования. Реальные каналы ADC можно переназначить позже.

---

## Task 6: Back-EMF Observer

**Objective:** Наблюдатель обратной ЭДС в αβ-координатах для оценки скорости и положения ротора.

**Files:**
- Create: `C:/ST/boyler/Motor/src/observer.h`
- Create: `C:/ST/boyler/Motor/src/observer.c`

**Модель:**
```
Eα = Vα - R*Iα - L*dIα/dt
Eβ = Vβ - R*Iβ - L*dIβ/dt
```
Где Vα,Vβ — напряжение управления (из Inverse Park), Iα,Iβ — измеренный ток, R,L — параметры двигателя.

**Структура:**
```c
typedef struct {
    int32_t R_mOhm;      // сопротивление фазы, мОм
    int32_t L_uH;        // индуктивность фазы, мкГн
    int32_t Ts_us;       // период ШИМ, мкс
    int32_t emf_alpha;   // Q15
    int32_t emf_beta;    // Q15
    int32_t prev_ia;     // Q15, предыдущий Iα
    int32_t prev_ib;     // Q15, предыдущий Iβ
} BEMFObserver;

void BEMF_Init(BEMFObserver *obs, int32_t r_mohm, int32_t l_uh, int32_t ts_us);
void BEMF_Update(BEMFObserver *obs, int32_t valpha, int32_t vbeta, int32_t ia, int32_t ib);
```

---

## Task 7: PLL для оценки скорости/положения

**Objective:** Фазовая автоподстройка для извлечения угла (θ) и скорости (ω) из Eα/Eβ.

**Files:**
- Create: `C:/ST/boyler/Motor/src/pll.h`
- Create: `C:/ST/boyler/Motor/src/pll.c`

**Структура:**
```c
typedef struct {
    int32_t kp;           // Q15 пропорциональный коэффициент
    int32_t ki;           // Q15 интегральный коэффициент
    int32_t theta_q31;    // текущий угол
    int32_t speed_q15;    // текущая скорость (об/мин в Q15?)
    int32_t integrator;   // Q31 интегратор
    int32_t ts_us;        // период
} PLL;

void PLL_Init(PLL *pll, int32_t kp, int32_t ki, int32_t ts_us);
void PLL_Update(PLL *pll, int32_t emf_alpha, int32_t emf_beta);
int32_t PLL_GetTheta(PLL *pll);  // угол в Q31
int32_t PLL_GetSpeed(PLL *pll);  // скорость
```

**Алгоритм:**
1. Нормализовать Eα,Eβ → единичный вектор
2. `err = -Eα*sin(θ) + Eβ*cos(θ)` — ошибка PLL
3. PI-регулятор: `integrator += ki*err*Ts`, `ω = kp*err + integrator`
4. `θ += ω*Ts`

---

## Task 8: FOC управление (основной цикл)

**Objective:** Основной цикл FOC: читаем токи → Clarke → Park → PI → Inverse Park → Inverse Clarke → ШИМ.

**Files:**
- Modify: `C:/ST/boyler/Motor/main.c`
- Modify: `C:/ST/boyler/Motor/src/foc.h`
- Modify: `C:/ST/boyler/Motor/src/foc.c`

**Структура цикла (5 кГц):**
```c
void FOC_ControlLoop(void) {
    // 1. Читаем токи (ADC)
    int32_t iu = ADC_Get_I1();
    int32_t iv = ADC_Get_I2();
    int32_t iw = -(iu + iv);  // закон Кирхгофа
    
    // 2. Clarke: Iu,Iv,Iw → Iα,Iβ
    AlphaBeta ab = Clarke_Transform(iu, iv, iw);
    
    // 3. Наблюдатель BEMF
    BEMF_Update(&observer, v_alpha, v_beta, ab.alpha, ab.beta);
    
    // 4. PLL: Eα,Eβ → θ, ω
    PLL_Update(&pll, observer.emf_alpha, observer.emf_beta);
    int32_t theta = PLL_GetTheta(&pll);
    
    // 5. Park: Iα,Iβ,θ → Id,Iq
    DQ dq = Park_Transform(ab.alpha, ab.beta, theta);
    
    // 6. PI регуляторы Id,Iq → Vd,Vq
    int32_t vd = PI_Control(&pi_d, 0 - dq.d);    // Id_ref = 0 для максимального момента
    int32_t vq = PI_Control(&pi_q, iq_ref - dq.q);
    
    // 7. Inverse Park: Vd,Vq,θ → Vα,Vβ
    AlphaBeta vab = InvPark_Transform(vd, vq, theta);
    
    // 8. Inverse Clarke: Vα,Vβ → Vu,Vv,Vw
    int32_t vu, vv, vw;
    InvClarke_Transform(vab.alpha, vab.beta, &vu, &vv, &vw);
    
    // 9. Распределение напряжения между инверторами
    // V1 = Vdc/2 + V/2, V2 = Vdc/2 - V/2  (для OEW)
    PWM_SetDuty1(dc_bias + vu/2, dc_bias + vv/2, dc_bias + vw/2);
    PWM_SetDuty2(dc_bias - vu/2, dc_bias - vv/2, dc_bias - vw/2);
}
```

**Параметры двигателя (заглушки, настраиваются):**
```c
#define MOTOR_R_MOHM    13000  // 13 Ом
#define MOTOR_L_UH      50000  // 50 мГн
#define MOTOR_POLES     4      // число пар полюсов
```

---

## Task 9: PI-регулятор

**Objective:** Простой PI-регулятор с anti-windup для токовых контуров и скорости.

**Files:**
- Add to: `C:/ST/boyler/Motor/src/foc.h`
- Add to: `C:/ST/boyler/Motor/src/foc.c`

```c
typedef struct {
    int32_t kp;           // Q15
    int32_t ki;           // Q15
    int32_t integral;     // Q31
    int32_t out_max;      // Q15, насыщение
    int32_t out_min;      // Q15
} PIController;

void PI_Init(PIController *pi, int32_t kp, int32_t ki, int32_t max, int32_t min);
int32_t PI_Update(PIController *pi, int32_t error);
```

---

## Task 10: UART командный интерфейс

**Objective:** Управление через UART: пуск/стоп, задание скорости/тока, мониторинг.

**Files:**
- Create: `C:/ST/boyler/Motor/src/uart.h`
- Create: `C:/ST/boyler/Motor/src/uart.c`

**Команды:**
- `1` — запуск FOC
- `0` — стоп, отключение ШИМ
- `s=500` — задать скорость 500 об/мин
- `i=1000` — задать ток Id=1000 мА
- `r` — чтение регистров (токи, скорость, угол)
- `m` — меню

**Вывод:** каждые 100 мс отправлять `@FOC:Iq=...:Id=...:Speed=...:Theta=...`

---

## Task 11: Основной main.c

**Objective:** Интеграция всех модулей. Инициализация периферии, главный цикл с FOC_ControlLoop на 5 кГц.

**Files:**
- Modify: `C:/ST/boyler/Motor/main.c`

**Псевдокод:**
```c
int main() {
    UART_Init();
    GPIO_Init();     // EN, ADC pins
    ADC_Init();      // ADC2
    PWM_Init();      // TIM1, TIM8
    CORDIC_Init();
    
    BEMFObserver obs;
    BEMF_Init(&obs, MOTOR_R_MOHM, MOTOR_L_UH, 200); // Ts=200us (5kHz)
    
    PLL pll;
    PLL_Init(&pll, kp, ki, 200);
    
    PIController pi_d, pi_q;
    PI_Init(&pi_d, ...);
    PI_Init(&pi_q, ...);
    
    while (1) {
        if (foc_running) {
            // Ждём синхронизации с ШИМ (прерывание)
            FOC_ControlLoop();
        }
        // Обработка UART команд
        UART_Process();
        // Отправка телеметрии (раз в 100 мс)
        UART_Telemetry();
    }
}
```

**Синхронизация:** FOC_ControlLoop вызывается по прерыванию TIM1 (UPDATE) каждые 200 мкс (5 кГц). Либо в главном цикле с polling флага.

---

## Task 12: Обработка OEW топологии в FOC

**Objective:** Адаптировать FOC под OEW: общее звено DC, два инвертора, распределение напряжения.

**Files:**
- Modify: `C:/ST/boyler/Motor/src/foc.c`

**Распределение напряжения OEW:**
```c
// V1 и V2 — напряжения инверторов 1 и 2
// V двигателя = V1 - V2
// V1 = Vdc/2 + V/2
// V2 = Vdc/2 - V/2
uint16_t dc_bias = TIM_ARR / 2;  // смещение = 50% duty

uint16_t duty1_u = dc_bias + (vu >> 1);
uint16_t duty1_v = dc_bias + (vv >> 1);
uint16_t duty1_w = dc_bias + (vw >> 1);
uint16_t duty2_u = dc_bias - (vu >> 1);
uint16_t duty2_v = dc_bias - (vv >> 1);
uint16_t duty2_w = dc_bias - (vw >> 1);

// Клиппинг
#define CLAMP(x, min, max) ((x) < (min) ? (min) : (x) > (max) ? (max) : (x))
duty1_u = CLAMP(duty1_u, 1, 98);
// ... и т.д.

PWM_SetDuty1(duty1_u, duty1_v, duty1_w);
PWM_SetDuty2(duty2_u, duty2_v, duty2_w);
```

---

## Task 13: Телеметрия и отладка

**Objective:** Вывод данных через UART для визуализации и отладки на PC.

**Files:**
- Modify: `C:/ST/boyler/Motor/src/uart.c`

**Формат:**
```
@FOC:I1=...:I2=...:IN=...:Vbus=...:Iq=...:Id=...:Speed=...:Theta=...:Vα=...:Vβ=...
@ERR:Eα=...:Eβ=...:PLLerr=...
```

**Период:** 10 мс (каждые 50 циклов ШИМ).

---

## Task 14: Защита и аварийные ситуации

**Objective:** Защита от превышения тока, перенапряжения, потери управления.

**Files:**
- Modify: `C:/ST/boyler/Motor/main.c`
- Create: `C:/ST/boyler/Motor/src/protect.h`
- Create: `C:/ST/boyler/Motor/src/protect.c`

**Защиты:**
- Overcurrent: I1 или I2 > порога → немедленное отключение ШИМ
- Overvoltage: Vbus > порога → торможение
- PLL unlock: ошибка PLL > порога → перезапуск наблюдателя
- Watchdog: если FOC_ControlLoop не вызван за 1 мс → авария

---

## Files that will change

| Файл | Действие |
|------|----------|
| `C:/ST/boyler/Motor/main.c` | Новый main, интеграция всех модулей |
| `C:/ST/boyler/Motor/Makefile` | Обновлённые C_SOURCES + INCLUDES |
| `C:/ST/boyler/Motor/src/foc.h` | Создать (структуры, API) |
| `C:/ST/boyler/Motor/src/foc.c` | Создать (PI, Clarke, Park) |
| `C:/ST/boyler/Motor/src/cordic_math.h` | Создать |
| `C:/ST/boyler/Motor/src/cordic_math.c` | Создать |
| `C:/ST/boyler/Motor/src/observer.h` | Создать |
| `C:/ST/boyler/Motor/src/observer.c` | Создать |
| `C:/ST/boyler/Motor/src/pll.h` | Создать |
| `C:/ST/boyler/Motor/src/pll.c` | Создать |
| `C:/ST/boyler/Motor/src/pwm.h` | Создать |
| `C:/ST/boyler/Motor/src/pwm.c` | Создать |
| `C:/ST/boyler/Motor/src/adc.h` | Создать |
| `C:/ST/boyler/Motor/src/adc.c` | Создать |
| `C:/ST/boyler/Motor/src/uart.h` | Создать |
| `C:/ST/boyler/Motor/src/uart.c` | Создать |
| `C:/ST/boyler/Motor/src/protect.h` | Создать |
| `C:/ST/boyler/Motor/src/protect.c` | Создать |

## Task 15: Flux Weakening (Ослабление поля)

**Objective:** Алгоритм ослабления поля для асинхронного двигателя в бездатчиковом FOC. Отслеживание Vout = sqrt(Vd^2+Vq^2), при превышении порога уменьшение Id_ref.

**Files:** Create: src/flux_weakening.h, Create: src/flux_weakening.c

**Алгоритм:**
1. V_out = sqrt(Vd^2 + Vq^2) через CORDIC_Modulus
2. Если V_out > V_threshold * V_max: integrator += ki * err * Ts, id_add = kp * err + integrator
3. Iq_max лимитируется: iq_max = sqrt(I_total_max^2 - (Id_ref + Id_add)^2)

**Интеграция в FOC:** Id_ref += FW_GetIdAdd(), Iq_ref = CLAMP(Iq_ref, -FW_GetIqLimit(), FW_GetIqLimit())

---

## Task 16: OEW специфика — наблюдатель и dead-time компенсация

**Objective:** Адаптация BEMF наблюдателя под OEW. Компенсация dead-time драйверов STEVAL-IPM20B через TIMx_BDTR.

**OEW наблюдение:** V_motor = V1 - V2 = V (разностное). Смещение dc_bias сокращается, наблюдатель использует V как обычно.

**Dead-time компенсация:**
- Измерить знак Iα, Iβ (ток каждой фазы)
- Для каждой фазы: duty += dt_comp_ticks при токе > 0, duty -= dt_comp_ticks при токе < 0
- dt_comp = 1-2 тика (1-2 мкс при 16 МГц)
- На малых скоростях (<100 об/мин) фильтр знака тока (4-8 периодов скользящее среднее)

**Старт:** V/f open-loop (0 -> 10 Гц за 2 с) -> transition -> closed-loop FOC

---

## Task 17: V/f старт (Open-Loop)

**Objective:** V/f разгон для начального намагничивания и разгона асинхронного двигателя до скорости, где BEMF захватывает управление.

**Files:** Create: src/vf_start.h, Create: src/vf_start.c

**Структура:** VFStart { target_speed, current_speed, ramp_time_ms, v_per_hz, theta_q31, iq_ref, id_ref }

**Алгоритм:**
1. Id = номинальный (поток), Iq = малый (момент)
2. Частота растёт линейно от 0 до target за ramp_time_ms
3. Угол интегрируется: theta += speed * Ts
4. Vout = speed * v_per_hz + boost (компенсация R)
5. При target + PLL error < порога N циклов -> переключение на FOC

---

## Open questions (добавлено)

4. Стартовый буст V/f при 0 Гц — тест с мотором
5. Dead-time 1 мкс при 5 кГц = 0.5% искажения. На 15% duty это ~3% ошибки
6. FW: Id_max (отриц) ограничено STEVAL-IPM20B (2.5A номин, 5A пик)
7. OEW: V_motor_max = 2x Vdc. При 60В = 120Вп-п, запас по скорости

---
## Tests / Verification

1. **Make build:** `make` без ошибок
2. **UART startup:** после прошивки через терминал видны Step 1-4 сообщения
3. **PWM на Saleae:** 5 кГц на всех 12 каналах (ручной тест)
4. **ADC:** ручной тест с мультиметром (как делали)
5. **CORDIC:** проверить sin(0)=0, cos(0)=32768, atan2(0,1)=0
6. **PLL:** тест с симулированным Eα,Eβ синусоидальным сигналом

## Risks

1. CORDIC может требовать специфической настройки (функция, точность)
2. Измерение фазных токов при OEW — нет пофазных шунтов, только I1,I2,IN. Нужно пересчитать Ia,Ib,Ic через матрицу токов.
3. Dead-time на 5 кГц может давать искажения — нужна компенсация.
4. Начальный запуск: без датчиков ротор неподвижен, EMF=0. Нужен open-loop старт (Iq ramp).

## Open questions

1. **Токовые шунты:** как именно测量的 фазные токи? I1(I_total1) + I2(I_total2) + IN(I_neutral) или пофазные шунты?
2. **Скорость старта:** у асинхронного двигателя нет магнитов, EMF нарастает после намагничивания. Нужен V/f старт, потом переключение на observer.
3. **Модуляция:** SVM (Space Vector Modulation) или синусоидальная + 3-я гармоника? SVM эффективнее.
