#include "stm32g474xx.h"
#include <stdio.h>

#define TIM_PSC                 15      // 16MHz / 16 = 1 MHz
#define TIM_ARR                 99      // 1MHz / 100 = 10 kHz
#define TIM_DTG                 16      // ~1 us at 16 MHz
#define ADC_TIMEOUT             100000UL
#define DELAY_10MS              160000UL
#define DELAY_1S                16000000UL

static void delay_volatile(volatile uint32_t count) {
    while (count--) { __NOP(); }
}

void USART2_SendString(char *str) {
    while (*str) {
        while (!(USART2->ISR & USART_ISR_TXE_TXFNF)) {}
        USART2->TDR = (uint8_t)(*str++);
    }
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
    USART2_SendString("ADC: Starting Cal...\r\n");
    ADC1->CR |= ADC_CR_ADCAL;
    timeout = 1000000;
    while (ADC1->CR & ADC_CR_ADCAL) { 
        if (--timeout == 0) {
            USART2_SendString("ERR: ADC CAL timeout!\r\n");
            return; 
        }
    }
    USART2_SendString("ADC: Cal OK\r\n");

    // Настройка
    ADC1->CFGR = 0;
    ADC1->SMPR1 = 0;
    ADC1->SMPR1 |= (7U << ADC_SMPR1_SMP1_Pos) | (7U << ADC_SMPR1_SMP4_Pos) | 
                   (7U << ADC_SMPR1_SMP6_Pos) | (7U << ADC_SMPR1_SMP7_Pos) | 
                   (7U << ADC_SMPR1_SMP8_Pos);
    
    // Включение АЦП
    USART2_SendString("ADC: Enabling...\r\n");
    ADC1->ISR = ADC_ISR_ADRDY;
    ADC1->CR |= ADC_CR_ADEN;
    timeout = 1000000;
    while (!(ADC1->ISR & ADC_ISR_ADRDY)) { 
        if (--timeout == 0) {
            USART2_SendString("ERR: ADC ADEN timeout!\r\n");
            return; 
        }
    }
    USART2_SendString("Step 3: ADC Ready\r\n");
}

uint16_t ADC1_Read(uint32_t channel) {
    uint32_t timeout = 1000000; // Увеличили таймаут
    
    if (!(ADC1->CR & ADC_CR_ADEN)) {
        USART2_SendString("ERR: ADC not enabled!\r\n");
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
            USART2_SendString("ERR: ADC EOC timeout!\r\n");
            return 0xFFFF; 
        }
    }
    
    return (uint16_t)(ADC1->DR);
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

void Measure_Phase(char phase) {
    char buf[100];
    GPIOB->BSRR = (1 << 4) | (1 << 5);
    TIM1->CCER = 0; TIM8->CCER = 0;
    TIM1->CNT = 0; TIM8->CNT = 0;

    if (phase == 'U') {
        TIM1->CCR1 = 5; // 5% of 99
        TIM1->CCER = TIM_CCER_CC1E | TIM_CCER_CC1NE;
        TIM8->CCER = TIM_CCER_CC1NE | TIM_CCER_CC2NE | TIM_CCER_CC3NE;
    } else if (phase == 'V') {
        TIM1->CCR2 = 5;
        TIM1->CCER = TIM_CCER_CC2E | TIM_CCER_CC2NE;
        TIM8->CCER = TIM_CCER_CC1NE | TIM_CCER_CC2NE | TIM_CCER_CC3NE;
    } else if (phase == 'W') {
        TIM1->CCR3 = 5;
        TIM1->CCER = TIM_CCER_CC3E | TIM_CCER_CC3NE;
        TIM8->CCER = TIM_CCER_CC1NE | TIM_CCER_CC2NE | TIM_CCER_CC3NE;
    }

    TIM1->BDTR |= TIM_BDTR_MOE; TIM8->BDTR |= TIM_BDTR_MOE;
    TIM1->CR1 |= TIM_CR1_CEN; TIM8->CR1 |= TIM_CR1_CEN;
    delay_volatile(DELAY_10MS);

    uint16_t raw_vbus = ADC1_Read(6);
    uint16_t raw_iu1 = ADC1_Read(1);

    TIM1->CR1 &= ~TIM_CR1_CEN; TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;
    GPIOB->BSRR = (1 << 20) | (1 << 21);

    sprintf(buf, "Phase %c: Raw Vbus: %d, Raw I: %d\r\n", phase, raw_vbus, raw_iu1);
    USART2_SendString(buf);
}

int main(void) {
    USART2_Init();
    USART2_SendString("Step 1: UART OK\r\n");
    
    GPIO_Init();
    USART2_SendString("Step 2: GPIO OK\r\n");
    
    ADC1_Init();
    USART2_SendString("Step 3: ADC OK\r\n");
    
    TIM1_Init();
    TIM8_Init();
    USART2_SendString("Step 4: TIM OK\r\n");

    while (1) {
        Measure_Phase('U');
        delay_volatile(DELAY_1S);
        Measure_Phase('V');
        delay_volatile(DELAY_1S);
        Measure_Phase('W');
        delay_volatile(DELAY_1S);
    }
}