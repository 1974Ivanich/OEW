#include "uart.h"
#include "stm32g474xx.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

/*
 * Простой line-based парсер UART. Работает в polling-режиме (вызов
 * UART_ReadLine из main loop). Размер буфера 32 символа — этого хватит
 * для команд типа "s=500\r" и диагностики.
 *
 * Символы собираются до '\n' (или '\r'). Возврат — длина строки без
 * терминатора. Таймаут 0 — non-blocking; на каждом вызове возвращает
 * текущее накопленное состояние.
 */
#define UART_RX_LINE_MAX  64

/* ── Non-blocking TX: ring buffer + TXE interrupt ────────────────────── */
#define UART_TX_BUF_SIZE  2048
static char     tx_buf[UART_TX_BUF_SIZE];
static volatile uint16_t tx_head = 0;
static volatile uint16_t tx_tail = 0;
static volatile uint32_t uart_tx_dropped_count = 0;
static volatile uint32_t uart_tx_truncated_count = 0;
/* V/f-local drop counter: deliberately independent of the cumulative UART
 * drop counter. It is emitted in the next successful @VFLOG packet. */
static volatile uint32_t vflog_drop_count = 0;

#define UART_RX_BUF_SIZE  64
static volatile char     rx_ring[UART_RX_BUF_SIZE];
static volatile uint16_t rx_head = 0;
static volatile uint16_t rx_tail = 0;
static volatile uint32_t uart_rx_error_count = 0;
static volatile uint32_t uart_rx_overflow_count = 0;

extern volatile uint8_t g_autotune_abort;

static void uart_rx_abort_feed(char c) {
    static const char tok[] = "abort";
    static uint8_t pos = 0;
    if(c == '\n' || c == '\r') { pos = 0; return; }
    if(c == tok[pos]) {
        pos++;
        if(tok[pos] == '\0') { g_autotune_abort = 1; pos = 0; }
    } else {
        pos = (c == tok[0]) ? 1 : 0;
    }
}

static void uart_rx_isr(char c) {
    uart_rx_abort_feed(c);
    uint16_t next = (uint16_t)((rx_head + 1) % UART_RX_BUF_SIZE);
    if(next != rx_tail) {
        rx_ring[rx_head] = c;
        rx_head = next;
    } else {
        uart_rx_overflow_count++;
    }
}

static inline uint32_t uart_enter_critical(void) {
    uint32_t prev = __get_PRIMASK();
    __disable_irq();
    return prev;
}

static inline void uart_exit_critical(uint32_t prev) {
    __set_PRIMASK(prev);
}

static uint32_t get_pclk1(void) {
    uint32_t ppre1 = (RCC->CFGR & RCC_CFGR_PPRE1) >> RCC_CFGR_PPRE1_Pos;
    uint32_t apb_div;
    switch (ppre1) {
        case 0U: apb_div = 1U;  break;
        case 4U: apb_div = 2U;  break;
        case 5U: apb_div = 4U;  break;
        case 6U: apb_div = 8U;  break;
        case 7U: apb_div = 16U; break;
        default: apb_div = 1U;  break;
    }
    return SystemCoreClock / apb_div;
}

void UART_Init(void) {
    RCC->APB1ENR1 |= RCC_APB1ENR1_USART2EN;
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN;
    GPIOA->MODER &= ~(3U<<4); GPIOA->MODER |= (2U<<4);
    GPIOA->OSPEEDR |= (3U<<4);
    GPIOA->AFR[0] &= ~(0xF<<8); GPIOA->AFR[0] |= (7U<<8);
    GPIOA->MODER &= ~(3U<<6); GPIOA->MODER |= (2U<<6);
    GPIOA->OSPEEDR |= (3U<<6);
    GPIOA->AFR[0] &= ~(0xF<<12); GPIOA->AFR[0] |= (7U<<12);
    USART2->BRR = get_pclk1() / 115200;
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
    USART2->CR1 |= USART_CR1_RXNEIE_RXFNEIE | USART_CR1_PEIE;
    NVIC_SetPriority(USART2_IRQn, 2);
    NVIC_EnableIRQ(USART2_IRQn);
}

static int uart_enqueue_byte_atomic(char c) {
    int ok;
    uint32_t prev_mask = uart_enter_critical();
    uint16_t next = (uint16_t)((tx_head + 1) % UART_TX_BUF_SIZE);
    if(next != tx_tail) {
        tx_buf[tx_head] = c;
        tx_head = next;
        ok = 1;
    } else {
        ok = 0;
    }
    uart_exit_critical(prev_mask);
    return ok;
}

void UART_SendStr(const char *str) {
    while(*str) {
        while(!uart_enqueue_byte_atomic(*str)) {}
        str++;
        USART2->CR1 |= USART_CR1_TXEIE;
    }
}

void USART2_IRQHandler(void) {
    uint32_t isr = USART2->ISR;
    if(isr & (USART_ISR_ORE | USART_ISR_FE | USART_ISR_NE | USART_ISR_PE)) {
        USART2->ICR = USART_ICR_ORECF | USART_ICR_FECF |
                      USART_ICR_NECF | USART_ICR_PECF;
        uart_rx_error_count++;
    }
    if(isr & USART_ISR_RXNE_RXFNE) {
        uart_rx_isr((char)(USART2->RDR & 0xFF));
    }
    if(isr & USART_ISR_TXE_TXFNF) {
        if(tx_head != tx_tail) {
            USART2->TDR = (uint8_t)tx_buf[tx_tail];
            tx_tail = (uint16_t)((tx_tail + 1) % UART_TX_BUF_SIZE);
        } else {
            USART2->CR1 &= ~USART_CR1_TXEIE;
        }
    }
}

void UART_SendChar(char c) {
    while(!uart_enqueue_byte_atomic(c)) {}
    USART2->CR1 |= USART_CR1_TXEIE;
}

int UART_GetChar(void) {
    if(rx_head == rx_tail) return -1;
    char c = rx_ring[rx_tail];
    rx_tail = (uint16_t)((rx_tail + 1) % UART_RX_BUF_SIZE);
    return (int)(c & 0xFF);
}

int UART_DataAvailable(void) {
    return (rx_head == rx_tail) ? 0 : 1;
}

int UART_ReadLine(char *buf, int maxlen) {
    if(buf == 0 || maxlen <= 0) return -2;
    static char rxbuf[UART_RX_LINE_MAX];
    static int  idx = 0;
    static int  drain = 0;
    int c = UART_GetChar();
    if(c < 0) return 0;
    char ch = (char)c;
    if(drain) {
        if(ch == '\n' || ch == '\r') drain = 0;
        return 0;
    }
    if(ch == '\n' || ch == '\r') {
        rxbuf[idx] = '\0';
        int len = idx;
        idx = 0;
        if(len == 0) return 0;
        if(len >= maxlen) {
            strncpy(buf, rxbuf, (size_t)maxlen - 1);
            buf[maxlen - 1] = '\0';
            return -1;
        }
        strncpy(buf, rxbuf, (size_t)len);
        buf[len] = '\0';
        return len;
    }
    if(ch == 8 || ch == 127) {
        if(idx > 0) idx--;
        return 0;
    }
    if(idx >= UART_RX_LINE_MAX - 1) {
        idx = 0;
        drain = 1;
        return -1;
    }
    if(ch >= 32 && ch < 127) rxbuf[idx++] = ch;
    return 0;
}

void UART_SendInt(int32_t val) {
    char buf[16];
    sprintf(buf, "%ld", (long)val);
    UART_SendStr(buf);
}

void UART_SendTelemetry(const char *fmt, ...) {
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    UART_SendStr(buf);
}

int UART_TrySendStr(const char *str) {
    size_t len = strlen(str);
    if(len >= UART_TX_BUF_SIZE) {
        uint32_t prev_mask = uart_enter_critical();
        uart_tx_dropped_count++;
        uart_exit_critical(prev_mask);
        return -1;
    }
    uint32_t prev_mask = uart_enter_critical();
    uint16_t head = tx_head;
    uint16_t free_space = (uint16_t)((tx_tail - head - 1 + UART_TX_BUF_SIZE) % UART_TX_BUF_SIZE);
    int ok = (free_space >= len);
    if(ok) {
        for(size_t i = 0; i < len; i++) {
            tx_buf[head] = str[i];
            head = (uint16_t)((head + 1) % UART_TX_BUF_SIZE);
        }
        tx_head = head;
    } else {
        uart_tx_dropped_count++;
    }
    uart_exit_critical(prev_mask);
    if(!ok) return -1;
    USART2->CR1 |= USART_CR1_TXEIE;
    return 0;
}

int UART_TrySendTelemetry(const char *fmt, ...) {
    char buf[256];
    va_list args;
    int formatted;
    const int is_vflog = (strncmp(fmt, "@VFLOG:", 7) == 0);

    va_start(args, fmt);
    formatted = vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    if (formatted < 0 || (size_t)formatted >= sizeof(buf)) {
        uint32_t prev_mask = uart_enter_critical();
        uart_tx_truncated_count++;
        uart_tx_dropped_count++;
        if (is_vflog) vflog_drop_count++;
        uart_exit_critical(prev_mask);
        return -1;
    }

    if (is_vflog) {
        char *crlf = strstr(buf, "\r\n");
        if (crlf != 0) {
            size_t used = (size_t)(crlf - buf);
            int n = snprintf(crlf, sizeof(buf) - used, ":vflog_drp=%lu\r\n",
                             (unsigned long)vflog_drop_count);
            if (n < 0 || used + (size_t)n >= sizeof(buf)) {
                uint32_t prev_mask = uart_enter_critical();
                uart_tx_truncated_count++;
                uart_tx_dropped_count++;
                vflog_drop_count++;
                uart_exit_critical(prev_mask);
                return -1;
            }
        }
    }

    int rc = UART_TrySendStr(buf);
    if (is_vflog && rc < 0) {
        uint32_t prev_mask = uart_enter_critical();
        vflog_drop_count++;
        uart_exit_critical(prev_mask);
    }
    return rc;
}

uint32_t UART_GetDroppedCount(void) { return uart_tx_dropped_count; }
uint32_t UART_GetTruncatedCount(void) { return uart_tx_truncated_count; }
uint32_t UART_GetRxErrorCount(void) { return uart_rx_error_count; }
uint32_t UART_GetRxOverflowCount(void) { return uart_rx_overflow_count; }
