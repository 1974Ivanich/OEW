# ТЗ: ADC2 читает IN2/IN5 как 3072 при 1.65В (ожидается ~2048)

Проект: FOC-двигатель на STM32G474RE, CMSIS-only (без HAL), ARM GCC, make.

## Симптом (подтверждено мультиметром)
На ВСЕХ 4 пинах ADC2 подано ровно 1.65В (внешний БП, проверено мультиметром).
Команда `a` (software чтение) возвращает:
- I1 (PA0 = ADC2_IN1): 2047 ✅ (1.65В = ~2048 кодов, верно)
- I2 (PA1 = ADC2_IN2): **3072** ❌ (2.475В — НЕВЕРНО, должно быть ~2048)
- Ires (PA6 = ADC2_IN3): 2047 ✅
- VBUS (PC4 = ADC2_IN5): **3072** ❌ (должно быть ~2048)

3072 = 0xC00 — стабильно, сотни отсчётов, без шума. PWM выключен (p=...,0),
не наводка. Паттерн: НЕВЕРНЫЕ каналы (IN2, IN5) идут ВТОРЫМ и ЧЕТВЁРТЫМ
в последовательности чтения adc2_read(1), adc2_read(2), adc2_read(3), adc2_read(5).

## Код чтения (src/adc.c)

```c
static uint16_t adc2_read(uint32_t ch) {
    uint32_t t = 1000000;
    if(!(ADC2->CR & ADC_CR_ADEN)) return 0xFFFD;
    if(ADC2->CR & ADC_CR_ADSTART) {
        ADC2->CR |= ADC_CR_ADSTP; t = 100000;
        while(ADC2->CR & ADC_CR_ADSTP) { if(--t == 0) break; }
    }
    ADC2->SQR1 = (ch << ADC_SQR1_SQ1_Pos);
    ADC2->ISR = (ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR);
    ADC2->CR |= ADC_CR_ADSTART;
    while(!(ADC2->ISR & ADC_ISR_EOC)) { if(--t == 0) return 0xFFFF; }
    uint16_t r = (uint16_t)(ADC2->DR);
    adc2_stop();
    return r;
}

void ADC_StartConversion(void) {
    adc_data.raw_i1   = adc2_read(1);
    adc_data.raw_i2   = adc2_read(2);
    adc_data.raw_ires = adc2_read(3);   /* PA6 = ADC2_IN3 */
    adc_data.raw_vbus = adc2_read(5);   /* PC4 = ADC2_IN5 */
}
```

Инициализация (src/adc.c):
```c
ADC2->SMPR1 |= (7U<<ADC_SMPR1_SMP1_Pos)|(7U<<ADC_SMPR1_SMP2_Pos)
             | (7U<<ADC_SMPR1_SMP3_Pos)|(7U<<ADC_SMPR1_SMP5_Pos);
```

ВАЖНО: одновременно настроена INJECTED-группа (JEXTEN=01, триггер TIM1_TRGO,
JSQR: JSQ1=IN1, JSQ2=IN2, JSQ3=IN3, JSQ4=IN5; JADSTART активен при FOC).
ADC_CalibrateOffsets читает JDR1..JDR3 (injected) — калибровка offset_i1/i2/ires
работает и даёт ~2047 при 1.65В (значит injected чтение КОРРЕКТНО!).

## Вопросы для диагностики
1. Почему software-чтение (regular, SQR1=ch) даёт 3072 для IN2/IN5, при том что
   injected-чтение (JDR) тех же каналов даёт правильные 2047?
2. Связано ли с конфликтом regular ADSTART vs активной injected (JADSTART=1,
   JEXTEN=01, ожидание триггера TIM1_TRGO)? Может ли pending injected
   блокировать/портить regular конверсию?
3. Может ли быть проблема в SQR1: запись `ch << ADC_SQR1_SQ1_Pos` — корректна ли
   для G4 (SQ1_Pos=6)? Не затирает ли L[3:0] (длина=0 → 1 конверсия)?
4. 3072 = 0xC00 — что это может быть: остаточный заряд ёмкости выборки от
   предыдущего канала? Неверная конфигурация SMPR для IN2/IN5?
5. Почему IN2 (2-й вызов) и IN5 (4-й вызов) — оба неверны, а IN1/IN3 верны?
   Гипотеза: 3072 = 2048 + 1024 = 1.5 × 2048 — похоже на «сумму» или
   неполное переключение канала.

## Ограничения
- Не менять маппинг пинов (IN1=PA0, IN2=PA1, IN3=PA6, IN5=PC4 — проверено).
- Не менять injected-конфигурацию (FOC зависит от неё).
- Дать анализ ПРИЧИНЫ + минимальный фикс (только чтение regular/инициализация).

## Ответ
Дай: вероятную причину (по RM0440 §22.4), точное место бага, изменённый код
(минимальный), способ проверки на железе.
