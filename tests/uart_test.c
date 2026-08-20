#include "uart.h"
#include "stm32g474xx.h"
#include <stdio.h>
#include <string.h>

void USART2_IRQHandler(void);

static int failures;
static int checks;

static void check(const char *name, int condition)
{
    ++checks;
    if (!condition) {
        ++failures;
        printf("FAIL: %s\n", name);
    } else {
        printf("ok:   %s\n", name);
    }
}

static void inject_rx(const char *text)
{
    for (const char *p = text; *p != '\0'; ++p) {
        USART2->RDR = (uint32_t)(unsigned char)*p;
        USART2->ISR = USART_ISR_RXNE_RXFNE;
        USART2_IRQHandler();
    }
}

static int drain_tx(char *out, int capacity)
{
    int count = 0;
    while ((USART2->CR1 & USART_CR1_TXEIE) != 0 && count + 1 < capacity) {
        USART2->ISR = USART_ISR_TXE_TXFNF;
        USART2_IRQHandler();
        if ((USART2->CR1 & USART_CR1_TXEIE) != 0) out[count++] = (char)USART2->TDR;
    }
    out[count] = '\0';
    return count;
}

int main(void)
{
    char line[80];
    char output[128];
    UART_Init();

    inject_rx("abc");
    check("RX data available", UART_DataAvailable() != 0);
    check("RX first char", UART_GetChar() == 'a');
    check("RX second char", UART_GetChar() == 'b');
    check("RX third char", UART_GetChar() == 'c');
    check("RX empty", UART_GetChar() == -1 && UART_DataAvailable() == 0);

    inject_rx("hello\r\n");
    int line_result = 0;
    while (UART_DataAvailable()) {
        int result = UART_ReadLine(line, (int)sizeof(line));
        if (result != 0) line_result = result;
    }
    check("line completes", line_result == 5);
    check("line content", strcmp(line, "hello") == 0);

    inject_rx("this-line-is-too-long\r\n");
    line_result = 0;
    while (UART_DataAvailable()) {
        int result = UART_ReadLine(line, 5);
        if (result != 0) line_result = result;
    }
    check("line overflow", line_result == -1);
    inject_rx("\r\n");
    while (UART_DataAvailable()) (void)UART_ReadLine(line, (int)sizeof(line));

    UART_SendStr("OK:");
    UART_SendTelemetry("%d:%s", 42, "yes");
    check("TX interrupt enabled", (USART2->CR1 & USART_CR1_TXEIE) != 0);
    drain_tx(output, (int)sizeof(output));
    check("TX content", strcmp(output, "OK:42:yes") == 0);

    printf("UART: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
