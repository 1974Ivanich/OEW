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
#define UART_TX_BUF_SIZE  1024
static char     tx_buf[UART_TX_BUF_SIZE];
static volatile uint16_t tx_head = 0;
static volatile uint16_t tx_tail = 0;
static volatile uint32_t uart_tx_dropped_count = 0;
/* Formatted telemetry that exceeds the local packet buffer is rejected as a
 * whole line before the MPSC ring reservation; never emit a partial record. */
static volatile uint32_t uart_tx_truncated_count = 0;

/* ── RX: ring buffer + RXNE ISR (ревью UART-02/04) ─────────────────────
 * Раньше RX был только polling main loop: во время длительного autotune
 * main не читал RDR — команда abort не обрабатывалась, а поток символов
 * давал ORE. Теперь RXNE ISR складывает байты в ring и распознаёт токен
 * "abort" НЕЗАВИСИМО от main loop; ORE/FE/NE/PE чистятся через ICR. */
#define UART_RX_BUF_SIZE  64
static volatile char     rx_ring[UART_RX_BUF_SIZE];
static volatile uint16_t rx_head = 0;
static volatile uint16_t rx_tail = 0;
static volatile uint32_t uart_rx_error_count = 0;
static volatile uint32_t uart_rx_overflow_count = 0;

extern volatile uint8_t g_autotune_abort;   /* autotune.c */

/* Распознавание "abort" в RX ISR: команда работает, даже когда main loop
 * занят autotune (UART-02). Сброс матчера по CR/LF. */
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
        uart_rx_overflow_count++;   /* дроп новых при переполнении */
    }
}

/* ── PRIMASK critical sections ────────────────────────────────────
 *
 * NVIC приоритеты проекта:
 *   ADC1_2_IRQn  = 0  (FOC 5 кГц — самый критичный)
 *   TIM6_DAC_IRQn = 1  (1 кГц, V/f + телеметрия)
 *   USART2_IRQn  = 2  (UART TX drain)
 *
 * tx_head — MPSC: main (thread) + TIM6 ISR + (по контракту uart.h) ЛЮБОЙ
 * ISR с приоритетом ≤2, включая ADC1_2_IRQn = 0.
 *
 * Раньше здесь был BASEPRI = 0x10: он маскирует приоритеты ≥1 (TIM6,
 * USART2), но ПО ОПРЕДЕЛЕНИЮ не может замаскировать приоритет 0 (ADC).
 * А контракт uart.h разрешает UART_TrySend* из ADC1_2_IRQHandler →
 * структурная гонка producer↔producer на tx_head: два писателя считают
 * free_space от одного head и пишут в одни слоты (перемешивание байт),
 * либо enqueue откатывает head после прерывания (обрезка чужого пакета).
 * Проверено 11.08.2026: реальных UART-вызовов в цепочке ADC ISR
 * (PROTECT_Check → FOC_Run) в коде НЕТ — гонка латентная, но класс
 * обязан быть закрыт, пока uart.h обещает поддержку приоритета 0.
 *
 * Решение: PRIMASK (__disable_irq) — маскирует ВСЕ maskable IRQ, включая
 * приоритет 0. Цена: ADC ISR может быть отложен на длину критической
 * секции. Длина ограничена размером пакета (≤256 байт копии ≈ 2 мкс при
 * 170 МГц). Выборка тока — аппаратная (TIM1_TRGO → ADC), ISR лишь читает
 * JDR: задержка ≤2 мкс на 200 мкс периоде не влияет на выборку; TX идёт
 * из main/TIM6 (≤1 кГц) → доля FOC-циклов с задержкой < 0.5%.
 *
 * Сохранение/восстановление PRIMASK обеспечивает корректную вложенность
 * (main уже под PRIMASK → TIM6 прерывает → TrySendStr ставит тот же
 * PRIMASK, при выходе восстанавливает → не разблокирует раньше времени). */
static inline uint32_t uart_enter_critical(void) {
    uint32_t prev = __get_PRIMASK();
    __disable_irq();
    return prev;
}

static inline void uart_exit_critical(uint32_t prev) {
    __set_PRIMASK(prev);
}

/* PCLK1 (частота USART2, тактируется от APB1) с учётом реального делителя
 * APB1. ВНИМАНИЕ: для USART (в отличие от таймеров) x2-правило CK_INT НЕ
 * применяется — USART clock = PCLK1 напрямую (RM0440 §38.4). Раньше BRR
 * молча предполагал APB1_DIV=1 (SystemCoreClock без делителя) — верно
 * только для текущей конфигурации проекта, тот же класс скрытого бага,
 * что был найден и исправлен в pwm.c/encoder.c для таймеров. */
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
    /* PA2 = TX, PA3 = RX, AF7 = USART2 */
    GPIOA->MODER &= ~(3U<<4); GPIOA->MODER |= (2U<<4);
    GPIOA->OSPEEDR |= (3U<<4);
    GPIOA->AFR[0] &= ~(0xF<<8); GPIOA->AFR[0] |= (7U<<8);
    GPIOA->MODER &= ~(3U<<6); GPIOA->MODER |= (2U<<6);
    GPIOA->OSPEEDR |= (3U<<6);
    GPIOA->AFR[0] &= ~(0xF<<12); GPIOA->AFR[0] |= (7U<<12);
    USART2->BRR = get_pclk1() / 115200;
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
    /* Ревью UART-02/04: RXNE-прерывание (ring + abort + ошибки приёма). */
    USART2->CR1 |= USART_CR1_RXNEIE_RXFNEIE | USART_CR1_PEIE;
    /* NVIC: USART2 — приоритет ниже чем TIM1 (control loop) */
    NVIC_SetPriority(USART2_IRQn, 2);
    NVIC_EnableIRQ(USART2_IRQn);
}

/* tx_head — общий producer-указатель. Изначально буфер был SPSC (main —
 * единственный producer, USART2 ISR — consumer tx_tail), это было
 * безопасно без синхронизации. После появления UART_TrySendStr(),
 * вызываемого из TIM6_DAC_IRQHandler (приоритет 1), tx_head стал MPSC:
 * TIM6 может прервать main() посреди чтения/инкремента tx_head в
 * UART_SendStr() — гонка producer↔producer.
 *
 * Проверка "буфер полон?" (next == tx_tail) и сама запись байта должны
 * быть ОДНОЙ неделимой операцией. Здесь check+write объединены под
 * PRIMASK critical section: возвращает 0 если буфер полон (байт НЕ
 * записан — без этого при полном буфере перезаписал бы непрочитанные
 * данные consumer'а), 1 — если байт успешно поставлен в очередь.
 * PRIMASK маскирует ВСЕ приоритеты (включая ADC=0) — см. блок выше. */
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

/* Non-blocking send: помещает строку в ring buffer, TXE ISR вытаскивает. */
void UART_SendStr(const char *str) {
    while(*str) {
        while(!uart_enqueue_byte_atomic(*str)) {}   /* буфер полон — ждём (main loop, не IRQ) */
        str++;
        USART2->CR1 |= USART_CR1_TXEIE;   /* включаем TXE interrupt */
    }
}

/* USART2 ISR: TX drain + RX ring/abort + ошибки приёма (ревью UART-02/04). */
void USART2_IRQHandler(void) {
    uint32_t isr = USART2->ISR;
    /* Ошибки приёма: ICR-очистка обязательна, иначе после ORE приём
     * встаёт; считаем для диагностики. */
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
            USART2->CR1 &= ~USART_CR1_TXEIE;   /* буфер пуст — выключаем IRQ */
        }
    }
}

void UART_SendChar(char c) {
    while(!uart_enqueue_byte_atomic(c)) {}   /* буфер полон — ждём (main loop, не IRQ) */
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

/*
 * Чтение строки (до '\n' или '\r'). Без блокировки: возвращает 0 если
 * строка ещё не собрана, >0 — длину готовой строки (без терминатора).
 * При переполнении буфера — отбрасывает и возвращает -1.
 */
int UART_ReadLine(char *buf, int maxlen) {
    /* Ревью UART-05: защита от NULL/неположительного размера. */
    if(buf == 0 || maxlen <= 0) return -2;
    static char rxbuf[UART_RX_LINE_MAX];
    static int  idx = 0;
    static int  drain = 0;   /* «доедание» хвоста переполненной строки */
    int c = UART_GetChar();
    if(c < 0) return 0;
    char ch = (char)c;
    if(drain) {
        /* После overflow не парсить хвост длинной команды как новую
         * строку: глотаем всё до терминатора (CR/LF) — иначе обрывок
         * команды склеился бы со следующей строкой (ревью uart.c). */
        if(ch == '\n' || ch == '\r') drain = 0;
        return 0;
    }
    if(ch == '\n' || ch == '\r') {
        rxbuf[idx] = '\0';
        int len = idx;
        idx = 0;
        if(len == 0) return 0;
        /* Ревью UART-05: при truncation возвращаем -1, а не исходную длину
         * (иначе caller считает строку полной). */
        if(len >= maxlen) {
            strncpy(buf, rxbuf, (size_t)maxlen - 1);
            buf[maxlen - 1] = '\0';
            return -1;
        }
        strncpy(buf, rxbuf, (size_t)len);
        buf[len] = '\0';
        return len;
    }
    if(ch == 8 || ch == 127) {   /* backspace */
        if(idx > 0) idx--;
        return 0;
    }
    if(idx >= UART_RX_LINE_MAX - 1) {
        idx = 0;      /* overflow — сброс */
        drain = 1;    /* остаток строки доедается до CR/LF (см. выше) */
        return -1;
    }
    if(ch >= 32 && ch < 127) {   /* printable */
        rxbuf[idx++] = ch;
    }
    return 0;
}

void UART_SendInt(int32_t val) {
    char buf[16];
    sprintf(buf, "%ld", (long)val);
    UART_SendStr(buf);
}

void UART_SendTelemetry(const char *fmt, ...) {
    /* 256 байт — было 128, что приводило к молчаливой обрезке длинных
     * диагностических строк (напр. @DBG:CH:EN с 7 полями 0x%08lX ~157
     * символов после подстановки — превышало старый лимит на треть). */
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    UART_SendStr(buf);
}

/* ── Неблокирующие варианты для вызова из ISR с приоритетом ≤ USART2_IRQn (2) ──
 *
 * ВАЖНО: обычные UART_SendStr/UART_SendTelemetry делают busy-wait
 * (while(next==tx_tail){}) при заполнении кольцевого буфера, предполагая,
 * что USART2_IRQHandler параллельно его drain'ит. На Cortex-M это верно
 * ТОЛЬКО если вызывающий код имеет БОЛЕЕ НИЗКИЙ приоритет NVIC (большее
 * число), чем USART2_IRQn. Прерывание с более высоким приоритетом
 * (ADC1_2_IRQn=0, TIM6_DAC_IRQn=1) не может быть вытеснено USART2 (=2) —
 * если такое ISR вызовет блокирующий UART_SendStr при полном буфере,
 * получится настоящий deadlock (priority inversion), а не задержка.
 *
 * Эти функции никогда не блокируются: если места не хватает — весь пакет
 * отбрасывается целиком (не частично, чтобы не отправить битую строку),
 * инкрементируется uart_tx_dropped_count. Использовать из любого ISR с
 * приоритетом 0 или 1 (ADC1_2_IRQHandler, TIM6_DAC_IRQHandler, TIM2_IRQHandler).
 *
 * Критическая секция на PRIMASK (__disable_irq): маскирует ВСЕ приоритеты,
 * включая ADC (0). Длина пакета ограничена 256 байтами (буфер
 * UART_TrySendTelemetry), поэтому критическая секция короткая и
 * предсказуемая (≤2 мкс при 170 МГц). Отложенный из-за этого ADC ISR
 * лишь позже читает уже готовые JDR — на выборку не влияет. */

int UART_TrySendStr(const char *str) {
    size_t len = strlen(str);
    /* Ранний reject: пакет длиннее буфера никогда не поместится —
     * нет смысла входить в критическую секцию. */
    if(len >= UART_TX_BUF_SIZE) {
        /* Ревью UART-06: счётчик под тем же PRIMASK (иначе потеря
         * инкрементов при нескольких producers). */
        uint32_t prev_mask = uart_enter_critical();
        uart_tx_dropped_count++;
        uart_exit_critical(prev_mask);
        return -1;
    }
    /* Весь reserve+copy+advance — под PRIMASK (маскирует ВСЕ приоритеты,
     * включая ADC=0). Длина пакета ограничена 256 байтами, поэтому
     * критическая секция фиксирована по длительности. */
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

    va_start(args, fmt);
    formatted = vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    /* vsnprintf returns the required byte count excluding NUL. Reject an
     * encoding error or truncated record before UART_TrySendStr() can enqueue
     * a line whose final CRLF was cut off. Count it under the same PRIMASK
     * contract as ordinary whole-packet TX drops. */
    if (formatted < 0 || (size_t)formatted >= sizeof(buf)) {
        uint32_t prev_mask = uart_enter_critical();
        uart_tx_truncated_count++;
        uart_tx_dropped_count++;
        uart_exit_critical(prev_mask);
        return -1;
    }
    return UART_TrySendStr(buf);
}

uint32_t UART_GetDroppedCount(void) {
    return uart_tx_dropped_count;
}

uint32_t UART_GetTruncatedCount(void) {
    return uart_tx_truncated_count;
}

uint32_t UART_GetRxErrorCount(void)     { return uart_rx_error_count; }
uint32_t UART_GetRxOverflowCount(void)  { return uart_rx_overflow_count; }
