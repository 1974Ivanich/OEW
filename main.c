#include "stm32g474xx.h"
#include <stdio.h>

#define TIM_PSC                 15      // 16MHz / 16 = 1 MHz
#define TIM_ARR                 99      // 1MHz / 100 = 10 кГц edge-aligned (реально 5 кГц, т.к. CMS=center-aligned!)
#define TIM_DTG                 16      // ~1 us at 16 MHz
#define ADC_TIMEOUT             100000UL
#define DELAY_10MS              160000UL
#define DELAY_1S                16000000UL

/* ============================================================
 *  Константы для пересчёта сырых кодов АЦП в физические величины.
 *  ВНИМАНИЕ: значения с пометкой "ПРОВЕРИТЬ" взяты из документации
 *  UM2705 / расчёта делителя из переписки и ТРЕБУЮТ сверки с вашей
 *  реальной платой (маркировка резисторов, измерение мультиметром).
 * ============================================================ */
#define ADC_VREF_MV             3300U   // опорное напряжение ADC, мВ
#define ADC_MAX_CODE            4095U   // 12 бит

/* Внешний делитель шины DC (R_high = 2x56 кОм посл., R_low = 4.7 кОм).
 * Коэффициент обратного пересчёта Vbus_real = Vadc * VBUS_DIV_NUM/VBUS_DIV_DEN */
#define VBUS_DIV_NUM            2482U   // ПРОВЕРИТЬ: пересчитайте под фактические номиналы вашего делителя
#define VBUS_DIV_DEN            100U

/* Токовая цепь STEVAL-IPM20B: коэффициент усиления по UM2705 = 2 В/В (подтверждено датащитом). */
#define CS_GAIN                 2U
/* Сопротивление шунта — ЗАГЛУШКА! В UM2705 конкретное значение не приводится одним
 * числом (зависит от выбранной конфигурации платы) — посмотрите маркировку резистора
 * шунта на плате или BOM и подставьте реальное значение в миллиомах. */
#define RSHUNT_MOHM             30U     // ПРОВЕРИТЬ по вашей плате!

#define ADC_AVG_SAMPLES         16U     // усреднение против пульсаций тока от ШИМ

/* Скважность тестового импульса (из 100, т.к. TIM_ARR+1=100).
 * 5 = ~5% давало слишком слабый сигнал тока при R=13 Ом (единицы мВ на АЦП) —
 * подняли до 15%, ток по-прежнему далеко от номинального (безопасно для 10 мс импульса). */
#define TEST_DUTY_CCR           15U

static void delay_volatile(volatile uint32_t count) {
    while (count--) { __NOP(); }
}

/* Прототип — без него Execute_Command не скомпилируется (implicit declaration) */
void Measure_Phase(char phase);

void USART2_SendString(char *str) {
    while (*str) {
        while (!(USART2->ISR & USART_ISR_TXE_TXFNF)) {}
        USART2->TDR = (uint8_t)(*str++);
    }
}

char USART2_ReceiveChar(void) {
    while (!(USART2->ISR & USART_ISR_RXNE_RXFNE)) {}
    return (char)USART2->RDR;
}

static void Show_Menu(void) {
    USART2_SendString("--- Меню ---\r\n");
    USART2_SendString("1. Измерить фазу U\r\n");
    USART2_SendString("2. Измерить фазу V\r\n");
    USART2_SendString("3. Измерить фазу W\r\n");
    USART2_SendString("M. Показать меню\r\n");
    USART2_SendString("Q. Повторить цикл измерений\r\n");
    USART2_SendString("Введите команду: ");
}

static void USART2_Init(void) {
    RCC->APB1ENR1 |= RCC_APB1ENR1_USART2EN;
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN;
    /* PA2 TX */
    GPIOA->MODER &= ~(3U << 4); GPIOA->MODER |= (2U << 4);
    GPIOA->AFR[0] &= ~(0xF << 8); GPIOA->AFR[0] |= (7U << 8);
    /* PA3 RX */
    GPIOA->MODER &= ~(3U << 6); GPIOA->MODER |= (2U << 6);
    GPIOA->AFR[0] &= ~(0xF << 12); GPIOA->AFR[0] |= (7U << 12);
    
    USART2->BRR = 16000000UL / 115200; // 16 МГц!
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

static void GPIO_Init(void) {
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN | RCC_AHB2ENR_GPIOBEN | RCC_AHB2ENR_GPIOCEN;

    /* TIM1: PA8, PA9, PA10 (AF1) */
    GPIOA->MODER &= ~((3U << 16) | (3U << 18) | (3U << 20));
    GPIOA->MODER |= (2U << 16) | (2U << 18) | (2U << 20);
    GPIOA->AFR[1] &= ~((0xF << 0) | (0xF << 4) | (0xF << 8));
    GPIOA->AFR[1] |= (1U << 0) | (1U << 4) | (1U << 8);

    /* TIM1N: PB13, PB14, PB15 (AF1) */
    GPIOB->MODER &= ~((3U << 26) | (3U << 28) | (3U << 30));
    GPIOB->MODER |= (2U << 26) | (2U << 28) | (2U << 30);
    GPIOB->AFR[1] &= ~((0xF << 20) | (0xF << 24) | (0xF << 28));
    GPIOB->AFR[1] |= (1U << 20) | (1U << 24) | (1U << 28);

    /* TIM8: PC6, PC7, PC8 (AF4) */
    GPIOC->MODER &= ~((3U << 12) | (3U << 14) | (3U << 16));
    GPIOC->MODER |= (2U << 12) | (2U << 14) | (2U << 16);
    GPIOC->AFR[0] &= ~((0xF << 24) | (0xF << 28));
    GPIOC->AFR[0] |= (4U << 24) | (4U << 28);
    GPIOC->AFR[1] &= ~(0xF << 0); GPIOC->AFR[1] |= (4U << 0);

    /* TIM8N: PA5 (AF3), PB0 (AF3), PB1 (AF3) */
    GPIOA->MODER &= ~(3U << 10); GPIOA->MODER |= (2U << 10);
    GPIOA->AFR[0] &= ~(0xF << 20); GPIOA->AFR[0] |= (3U << 20);
    
    GPIOB->MODER &= ~((3U << 0) | (3U << 2));
    GPIOB->MODER |= (2U << 0) | (2U << 2); // ИСПРАВЛЕНО: AF (2), а не Output (1)
    GPIOB->AFR[0] &= ~((0xF << 0) | (0xF << 4));
    GPIOB->AFR[0] |= (3U << 0) | (3U << 4);

    /* EN_1 (PB4), EN_2 (PB5) */
    GPIOB->MODER &= ~((3U << 8) | (3U << 10));
    GPIOB->MODER |= (1U << 8) | (1U << 10);
    GPIOB->BSRR = (1U << 20) | (1U << 21);

    /* ADC Pins: PA0, PC0, PC1, PC2, PC3 */
    GPIOA->MODER |= (3U << 0);
    GPIOC->MODER |= (3U << 0) | (3U << 2) | (3U << 4) | (3U << 6);
}

static void ADC1_Init(void) {
    uint32_t timeout;
    RCC->AHB2ENR |= RCC_AHB2ENR_ADC12EN;
    delay_volatile(10000);

    // Жесткий сброс АЦП
    RCC->AHB2RSTR |= RCC_AHB2RSTR_ADC12RST;
    RCC->AHB2RSTR &= ~RCC_AHB2RSTR_ADC12RST;
    delay_volatile(1000);

    // ИСПРАВЛЕНИЕ: Включаем синхронный режим тактирования (от AHB)
    // CKMODE[1:0] = 01 (биты 17:16 в регистре CCR)
    ADC12_COMMON->CCR = (1U << 16); 

    // Выходим из глубокого сна и включаем регулятор
    ADC1->CR = 0;
    ADC1->CR &= ~ADC_CR_DEEPPWD;
    ADC1->CR |= ADC_CR_ADVREGEN;
    delay_volatile(100000); // Ждем стабилизации

    // Калибровка
    USART2_SendString("ADC: Запуск калибровки...\r\n");
    ADC1->CR |= ADC_CR_ADCAL;
    timeout = 1000000;
    while (ADC1->CR & ADC_CR_ADCAL) { 
        if (--timeout == 0) {
            USART2_SendString("ОШИБКА: таймаут калибровки ADC!\r\n");
            return; 
        }
    }
    USART2_SendString("ADC: калибровка ОК\r\n");

    // Настройка
    ADC1->CFGR = 0;
    ADC1->SMPR1 = 0;
    ADC1->SMPR1 |= (7U << ADC_SMPR1_SMP1_Pos) | (7U << ADC_SMPR1_SMP4_Pos) | 
                   (7U << ADC_SMPR1_SMP6_Pos) | (7U << ADC_SMPR1_SMP7_Pos) | 
                   (7U << ADC_SMPR1_SMP8_Pos);
    
    // Включение АЦП
    USART2_SendString("ADC: Включение...\r\n");
    ADC1->ISR = ADC_ISR_ADRDY;
    ADC1->CR |= ADC_CR_ADEN;
    timeout = 1000000;
    while (!(ADC1->ISR & ADC_ISR_ADRDY)) { 
        if (--timeout == 0) {
            USART2_SendString("ОШИБКА: таймаут включения ADC!\r\n");
            return; 
        }
    }
    USART2_SendString("Шаг 3: ADC готов\r\n");
}

uint16_t ADC1_Read(uint32_t channel) {
    uint32_t timeout = 1000000; // Увеличили таймаут
    
    if (!(ADC1->CR & ADC_CR_ADEN)) {
        USART2_SendString("ОШИБКА: ADC не включен!\r\n");
        return 0xFFFD;
    }
    
    // Настраиваем канал
    ADC1->SQR1 = (channel << ADC_SQR1_SQ1_Pos);
    ADC1->ISR = (ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR);
    
    // Запуск
    ADC1->CR |= ADC_CR_ADSTART;
    
    // Ожидание окончания
    while (!(ADC1->ISR & ADC_ISR_EOC)) { 
        if (--timeout == 0) {
            USART2_SendString("ОШИБКА: таймаут окончания преобразования ADC!\r\n");
            return 0xFFFF; 
        }
    }
    
    return (uint16_t)(ADC1->DR);
}

/* Усреднённое чтение канала — подавляет пульсацию тока от ШИМ (5 кГц) на одиночном отсчёте.
 * Возвращает код ошибки как есть (0xFFFF/0xFFFD), если он встретился хоть раз. */
uint16_t ADC1_ReadAvg(uint32_t channel, uint8_t samples) {
    uint32_t acc = 0;
    for (uint8_t i = 0; i < samples; i++) {
        uint16_t v = ADC1_Read(channel);
        if (v == 0xFFFF || v == 0xFFFD) {
            return v;
        }
        acc += v;
    }
    return (uint16_t)(acc / samples);
}

static uint32_t ADC_CodeToMillivolts(uint16_t code) {
    return ((uint32_t)code * ADC_VREF_MV) / ADC_MAX_CODE;
}

static uint32_t Vbus_RealMillivolts(uint16_t code) {
    uint32_t adc_mv = ADC_CodeToMillivolts(code);
    return (adc_mv * VBUS_DIV_NUM) / VBUS_DIV_DEN;
}

/* Разница кодов (текущий - offset при I=0) -> миллиамперы через шунт+усилитель платы */
static int32_t CurrentDeltaToMilliamps(int32_t code_delta) {
    int32_t adc_mv = (code_delta * (int32_t)ADC_VREF_MV) / (int32_t)ADC_MAX_CODE;
    return (adc_mv * 1000) / ((int32_t)RSHUNT_MOHM * (int32_t)CS_GAIN);
}

static void TIM1_Init(void) {
    RCC->APB2ENR |= RCC_APB2ENR_TIM1EN;
    TIM1->PSC = TIM_PSC; TIM1->ARR = TIM_ARR;
    TIM1->CR1 = TIM_CR1_CMS_1;
    TIM1->BDTR |= TIM_DTG; 
    TIM1->CCMR1 |= (6U << TIM_CCMR1_OC1M_Pos) | TIM_CCMR1_OC1PE | (6U << TIM_CCMR1_OC2M_Pos) | TIM_CCMR1_OC2PE;
    TIM1->CCMR2 |= (6U << TIM_CCMR2_OC3M_Pos) | TIM_CCMR2_OC3PE;
    TIM1->CCR1 = 0; TIM1->CCR2 = 0; TIM1->CCR3 = 0;
    TIM1->EGR |= TIM_EGR_UG;
}

static void TIM8_Init(void) {
    RCC->APB2ENR |= RCC_APB2ENR_TIM8EN;
    TIM8->PSC = TIM_PSC; TIM8->ARR = TIM_ARR;
    TIM8->CR1 = TIM_CR1_CMS_1;
    TIM8->BDTR |= TIM_DTG; 
    TIM8->CCMR1 |= (6U << TIM_CCMR1_OC1M_Pos) | TIM_CCMR1_OC1PE | (6U << TIM_CCMR1_OC2M_Pos) | TIM_CCMR1_OC2PE;
    TIM8->CCMR2 |= (6U << TIM_CCMR2_OC3M_Pos) | TIM_CCMR2_OC3PE;
    TIM8->CCR1 = 0; TIM8->CCR2 = 0; TIM8->CCR3 = 0;
    TIM8->EGR |= TIM_EGR_UG;
}

static void Execute_Command(char cmd) {
    if (cmd == '1') {
        Measure_Phase('U');
    } else if (cmd == '2') {
        Measure_Phase('V');
    } else if (cmd == '3') {
        Measure_Phase('W');
    } else if (cmd == 'M' || cmd == 'm') {
        Show_Menu();
    } else if (cmd == 'Q' || cmd == 'q') {
        USART2_SendString("Запуск автоматического цикла измерений...\r\n");
    } else {
        USART2_SendString("Неизвестная команда. Нажмите M для меню.\r\n");
    }
}

void Measure_Phase(char phase) {
    char buf[160];

    /* 0. Точка нуля тока: снимаем ДО включения силовых цепей (EN всё ещё выкл,
     *    реальный ток через шунт заведомо 0) — так калибруем offset усилителя/АЦП,
     *    не завися от точного значения смещения в схеме, которое нам не известно. */
    uint16_t i_offset = ADC1_ReadAvg(1, ADC_AVG_SAMPLES);

    GPIOB->BSRR = (1 << 4) | (1 << 5);   // EN_1, EN_2 -> ON
    TIM1->CCER = 0; TIM8->CCER = 0;
    TIM1->CNT = 0; TIM8->CNT = 0;

    if (phase == 'U') {
        TIM1->CCR1 = TEST_DUTY_CCR;
        TIM1->CCER = TIM_CCER_CC1E | TIM_CCER_CC1NE;
        TIM8->CCER = TIM_CCER_CC1NE | TIM_CCER_CC2NE | TIM_CCER_CC3NE;
    } else if (phase == 'V') {
        TIM1->CCR2 = TEST_DUTY_CCR;
        TIM1->CCER = TIM_CCER_CC2E | TIM_CCER_CC2NE;
        TIM8->CCER = TIM_CCER_CC1NE | TIM_CCER_CC2NE | TIM_CCER_CC3NE;
    } else if (phase == 'W') {
        TIM1->CCR3 = TEST_DUTY_CCR;
        TIM1->CCER = TIM_CCER_CC3E | TIM_CCER_CC3NE;
        TIM8->CCER = TIM_CCER_CC1NE | TIM_CCER_CC2NE | TIM_CCER_CC3NE;
    }

    TIM1->BDTR |= TIM_BDTR_MOE; TIM8->BDTR |= TIM_BDTR_MOE;
    TIM1->CR1 |= TIM_CR1_CEN; TIM8->CR1 |= TIM_CR1_CEN;
    delay_volatile(DELAY_10MS);

    uint16_t raw_vbus = ADC1_ReadAvg(6, ADC_AVG_SAMPLES);
    uint16_t raw_iu1  = ADC1_ReadAvg(1, ADC_AVG_SAMPLES);

    TIM1->CR1 &= ~TIM_CR1_CEN; TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;
    GPIOB->BSRR = (1 << 20) | (1 << 21);  // EN_1, EN_2 -> OFF

    if (raw_vbus == 0xFFFF || raw_vbus == 0xFFFD ||
        raw_iu1  == 0xFFFF || raw_iu1  == 0xFFFD) {
        USART2_SendString("ОШИБКА: чтение АЦП не удалось, измерение отменено\r\n");
        return;
    }

    /* ---- пересчёт в физические величины ---- */
    uint32_t vbus_mv = Vbus_RealMillivolts(raw_vbus);
    int32_t  i_delta  = (int32_t)raw_iu1 - (int32_t)i_offset;
    int32_t  i_ma      = CurrentDeltaToMilliamps(i_delta);

    /* Напряжение, приложенное к обмотке ~ Vbus * duty (без учёта падения на ключах —
     * при 13 Ом и токе в сотни мА падение на IGBT/диоде даёт заметную систематическую
     * погрешность, для точных измерений его стоит добавить отдельным слагаемым). */
    uint32_t v_applied_mv = (vbus_mv * TEST_DUTY_CCR) / (TIM_ARR + 1U);

    if (i_ma <= 0) {
        sprintf(buf, "Фаза %c: Vbus=%lu мВ, ток некорректен (raw=%d, offset=%d) — "
                      "увеличьте TEST_DUTY_CCR или проверьте токовую цепь\r\n",
                phase, (unsigned long)vbus_mv, raw_iu1, i_offset);
        USART2_SendString(buf);
        return;
    }

    uint32_t r_mohm = (v_applied_mv * 1000UL) / (uint32_t)i_ma;

    sprintf(buf, "Фаза %c: Vbus=%lu мВ, Uобм=%lu мВ, I=%ld мА, R=%lu.%03lu Ом "
                  "(raw Vbus=%d, raw I=%d, offset=%d)\r\n",
            phase, (unsigned long)vbus_mv, (unsigned long)v_applied_mv, (long)i_ma,
            (unsigned long)(r_mohm / 1000), (unsigned long)(r_mohm % 1000),
            raw_vbus, raw_iu1, i_offset);
    USART2_SendString(buf);
}

int main(void) {
    USART2_Init();
    USART2_SendString("Шаг 1: UART ОК\r\n");
    
    GPIO_Init();
    USART2_SendString("Шаг 2: GPIO ОК\r\n");
    
    ADC1_Init();
    USART2_SendString("Шаг 3: ADC ОК\r\n");
    
    TIM1_Init();
    TIM8_Init();
    USART2_SendString("Шаг 4: TIM ОК\r\n");

    Show_Menu();

    while (1) {
        char cmd = USART2_ReceiveChar();
        if (cmd == '\r' || cmd == '\n') {
            continue;
        }

        if (cmd == 'Q' || cmd == 'q') {
            USART2_SendString("Автоматический цикл измерений:\r\n");
            Measure_Phase('U');
            delay_volatile(DELAY_1S);
            Measure_Phase('V');
            delay_volatile(DELAY_1S);
            Measure_Phase('W');
            delay_volatile(DELAY_1S);
            Show_Menu();
            continue;
        }

        Execute_Command(cmd);
        Show_Menu();
    }
}