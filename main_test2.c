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
    USART2->BRR = 16000000UL / 115200; // Частота 16 МГц!
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;

    /* 5. Главный цикл */
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
