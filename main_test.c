#include "stm32g474xx.h"

void delay(volatile uint32_t count) {
    while(count--) { __NOP(); }
}

void USART2_Send(char *str) {
    while(*str) {
        while(!(USART2->ISR & USART_ISR_TXE_TXFNF));
        USART2->TDR = *str++;
    }
}

static void SystemClock_Config(void) {
    uint32_t timeout;
    RCC->CR |= RCC_CR_HSION;
    timeout = 1000000;
    while (!(RCC->CR & RCC_CR_HSIRDY)) { if (--timeout == 0) break; }

    RCC->PLLCFGR = RCC_PLLCFGR_PLLSRC_HSI |
                   (4U << 4) |            /* PLLM = 4 */
                   (80U << 8) |           /* PLLN = 80 */
                   RCC_PLLCFGR_PLLREN;    /* PLLREN = 1, PLLR = 0 (/2) */

    RCC->CR |= RCC_CR_PLLON;
    timeout = 1000000;
    while (!(RCC->CR & RCC_CR_PLLRDY)) { if (--timeout == 0) break; }

    FLASH->ACR = FLASH_ACR_LATENCY_4WS | FLASH_ACR_PRFTEN;
    RCC->CFGR = RCC_CFGR_HPRE_DIV1 | RCC_CFGR_PPRE1_DIV2 | RCC_CFGR_PPRE2_DIV1;
    RCC->CFGR |= RCC_CFGR_SW_PLL;
    timeout = 1000000;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) { if (--timeout == 0) break; }
}

int main(void) {
    /* 1. Включаем тактирование GPIOA, GPIOC и USART2 */
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN | RCC_AHB2ENR_GPIOCEN;
    RCC->APB1ENR1 |= RCC_APB1ENR1_USART2EN;

    /* 2. Настраиваем PA5 (Синий светодиод LD2 на Nucleo-G474RE) */
    GPIOA->MODER &= ~(3U << 10);
    GPIOA->MODER |= (1U << 10); // Output

    /* 3. Настраиваем PA2 (TX) и PA3 (RX) на AF7 (USART2) */
    GPIOA->MODER &= ~((3U << 4) | (3U << 6));
    GPIOA->MODER |= (2U << 4) | (2U << 6);
    GPIOA->AFR[0] &= ~((0xF << 8) | (0xF << 12));
    GPIOA->AFR[0] |= (7U << 8) | (7U << 12);

    /* 4. Настраиваем USART2 на 16 МГц (HSI, без PLL) */
    USART2->BRR = 16000000UL / 115200;
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;

    USART2_Send("BEFORE PLL\r\n");

    /* 5. Пробуем PLL */
    SystemClock_Config();

    /* 6. Перенастраиваем UART на 80 МГц */
    USART2->BRR = 80000000UL / 115200;

    /* 7. Главный цикл */
    while(1) {
        // Мигаем светодиодом
        GPIOA->BSRR = (1U << 5); // Вкл
        delay(500000);
        GPIOA->BSRR = (1U << 21); // Выкл
        delay(500000);

        // Отправляем текст
        USART2_Send("ALIVE\r\n");
    }
}
