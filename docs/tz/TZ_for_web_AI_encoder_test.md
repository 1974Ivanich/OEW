# ТЗ для web AI: hosted-тест логики encoder.c (AS5048A, TIM2 PWM capture)

Проект: STM32G474 (CMSIS-only), OEW FOC для асинхронного двигателя.
Драйвер энкодера: src/encoder.c (258 строк) — читает угол AS5048A через TIM2
PWM Input Capture, считает скорость из delta-угла между захватами (~920 Гц),
фильтрует IIR (>>3), watchdog в ENC_Update() (TIM6 1 кГц).

## ЗАДАЧА
Написать ОДИН файл `tests/encoder_logic_test.c` — hosted-тест (gcc, Windows/MinGW
или Linux — без железа!). Тест инклюдит исходник encoder.c ВНУТРЬ себя и вызывает
TIM2_IRQHandler() с поддельными регистрами. Проверяет логику: знак скорости,
wrap через 0°, обработку ошибок, фильтр, угол.

## КЛЮЧЕВОЙ ПРИЁМ (обязательно так!)
Тест делается так:
1. До #include "../src/encoder.c" определяются:
   - мок-структура TIM2 (см. ниже),
   - заглушки NVIC/GPIO/RCC (если понадобятся — но ENC_Init() вызывать НЕ нужно!),
   - extern uint32_t SystemCoreClock = 170000000;
2. #include "../src/encoder.c"  ← весь драйвер скомпилируется в тесте,
   static-переменные (enc_speed_rpm, enc_angle14, enc_error...) доступны
   только через геттеры ENC_GetSpeed_rpm() / ENC_GetAngle14() / ENC_GetError().
3. main() вызывает TIM2_IRQHandler() с разными состояниями мок-TIM2 и проверяет.

## МОК TIM2 (определить ПЕРЕД #include encoder.c)
```c
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Мок регистров TIM2 — достаточно полей, которые encoder.c читает/пишет */
typedef struct {
    volatile uint32_t SR;
    volatile uint32_t CCR1;
    volatile uint32_t CCR2;
    volatile uint32_t CR1;
    volatile uint32_t DIER;
    volatile uint32_t CCMR1;
    volatile uint32_t CCER;
    volatile uint32_t SMCR;
    volatile uint32_t PSC;
    volatile uint32_t ARR;
} TIM2_Mock;
static TIM2_Mock tim2_mock;
#define TIM2 (&tim2_mock)

/* Константы флагов, которые encoder.c использует (значения из stm32g474xx.h) */
#ifndef TIM_SR_CC1IF
#define TIM_SR_CC1IF   (0x0002U)
#define TIM_SR_CC2IF   (0x0004U)
#define TIM_SR_CC1OF   (0x0200U)
#define TIM_SR_CC2OF   (0x0400U)
#endif
#ifndef TIM_DIER_CC1IE
#define TIM_DIER_CC1IE (0x0002U)
#endif

/* Заглушки IRQ/NVIC — ENC_Init() мы не вызываем, но линковка нужна */
void NVIC_SetPriority(int irqn, uint32_t prio) { (void)irqn; (void)prio; }
void NVIC_EnableIRQ(int irqn) { (void)irqn; }
#define TIM2_IRQn 1
uint32_t SystemCoreClock = 170000000U;
```

ВНИМАНИЕ: encoder.c обращается к TIM2->PSC, TIM2->ARR и т.д. ТОЛЬКО в ENC_Init()
(которую мы не вызываем). В TIM2_IRQHandler() — только TIM2->SR, TIM2->CCR1,
TIM2->CCR2. Так что мока из 10 полей достаточно, но если компилятор ругнётся на
недостающие поля структуры — добавьте ВСЕ поля из stm32g474xx.h (TIM2_TypeDef
полный, ~30 полей) — проще скопировать полный typedef из CMSIS-заголовка.

## ВАЖНО ПРО rc_w0 (семантика TIM2->SR)
В encoder.c: `TIM2->SR = ~(TIM_SR_CC2IF | TIM_SR_CC1IF);` — на реальном железе
TIM_SR — rc_w0: запись 0 очищает бит, запись 1 = no-op. В моке это просто
присваивание uint32. Поэтому:
- ПЕРЕД каждым вызовом TIM2_IRQHandler() тест САМ ставит TIM2->SR = нужный флаг
  (например TIM_SR_CC1IF) — имитация «пришёл capture».
- После вызова НЕ проверяем SR (он «испорчен» присваиванием ~mask) — проверяем
  ТОЛЬКО observable-результаты: ENC_GetSpeed_rpm(), ENC_GetAngle14(), ENC_GetError().

## ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ (для генерации захватов)
```c
/* Подать один захват: period_us и pulse_us → установить CCR1/CCR2 и вызвать IRQ */
static void feed_capture(uint32_t period_us, uint32_t pulse_us) {
    tim2_mock.SR = TIM_SR_CC1IF;      /* имитация CC1 capture event */
    tim2_mock.CCR1 = period_us;       /* период в мкс (PSC → 1 МГц) */
    tim2_mock.CCR2 = pulse_us;        /* длительность импульса в мкс */
    TIM2_IRQHandler();
}
```
Замечание: encoder.c при невалидном захвате делает return ДО чтения CCR1/CCR2
— неважно, мы их уже установили.

## ПРОВЕРКИ (минимум 12, все должны быть PASS)

Формула для ожиданий: rpm = delta * 60000000 / (16384 * period_us).

1. **Прямой ход**: period=1087, pulse: 1000 → 1200 → 1400 → 1600 → 1800 (5 захватов)
   → ENC_GetSpeed_rpm() > 0.
2. **Обратный ход**: pulse: 1800 → 1600 → 1400 → 1200 → 1000
   → ENC_GetSpeed_rpm() < 0.
3. **Wrap через 0° (прямой)**: pulse: 16000 → 1000 (angle: ~15900 → ~1000;
   delta = 1000-15900 = -14900 < -8192 → +16384 → +1484 counts ≈ +5000 rpm).
   Проверка: rpm > 1000 (положительный, НЕ отрицательный и НЕ ~200000!).
   ТОЧНАЯ проверка: |rpm| < 20000 (не гигантский).
4. **Wrap через 0° (обратный)**: pulse: 1000 → 16000 → rpm < -1000, |rpm| < 20000.
5. **Мусорный период**: feed_capture(2000, 1000) [period > 1500]
   → ENC_GetError() == ENC_ERR_BAD_PERIOD (0x02), ENC_GetSpeed_rpm() == 0.
6. **Восстановление после BAD_PERIOD**: затем валидный feed_capture(1087, 1200) ×2
   → ENC_GetError() == 0, скорость снова > 0.
7. **pulse > period**: feed_capture(1087, 5000) → BAD_PERIOD, rpm == 0.
8. **Таймаут**: после валидных захватов вызвать ENC_Update() 6 раз без feed
   → ENC_GetError() == ENC_ERR_TIMEOUT (0x01), ENC_GetSpeed_rpm() == 0.
9. **Восстановление после таймаута**: feed_capture(1087, 1200) — первый захват
   после таймаута НЕ даёт скорость (first_capture), второй даёт: rpm > 0.
10. **IIR-стабильность**: 10 одинаковых захватов (delta постоянный: pulse
    увеличивается на 100 каждый раз: 1000,1100,...,1900) → последний rpm
    отличается от теоретического (rpm = 100*60000000/(16384*1087) ≈ 337) менее
    чем на 15%.
11. **Угол 50%**: feed_capture(16384, 8192) → ENC_GetAngle14() ≈ 8192 ± 10.
12. **Угол 25%**: feed_capture(16384, 4096) → ENC_GetAngle14() ≈ 4096 ± 10.

## ФОРМАТ ВЫВОДА
printf-стиль (hosted):
```
=== encoder logic test ===
  [PASS] forward: rpm=NNN > 0
  [PASS] backward: rpm=NNN < 0
  ...
=== ALL PASS (12 checks, 0 failed) ===
```
При FAIL: `[FAIL] имя: got=X expected=Y` и в конце `=== N FAILED ===`, return 1.
Счётчик: int passed=0, failed=0; макрос check(name, cond, got, expected).

## СБОРКА И ПРОГОН (обязательно сделать самому!)
```
gcc -I tests/mocks tests/encoder_logic_test.c -o tests/enc_test_hosted.exe
./tests/enc_test_hosted.exe
```
Все проверки должны быть PASS. Если что-то FAIL — разберись: возможно,
ожидание неверно (пересчитай по формуле), а не код. НО: если FAIL указывает на
реальный баг encoder.c — НЕ ПРАВЬ encoder.c (это продакшн!), а отметь в ответе
«найден реальный баг: ...» — мы решим отдельно.

## ОТВЕТ ДОЛЖЕН СОДЕРЖАТЬ
1. Полный код tests/encoder_logic_test.c
2. Вывод прогона (ALL PASS или список FAIL)
3. Если нашёл баг encoder.c — его описание
