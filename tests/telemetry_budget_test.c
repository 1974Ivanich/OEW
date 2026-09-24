#include "cli.h"
#include "uart.h"
#include "telemetry_format.h"   /* FOC_TELEMETRY_FMT — тот же формат, что в main.c */
#include "stm32g474xx.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

void USART2_IRQHandler(void);

#define UART_PAYLOAD_BPS_115200_8N1 11520u

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

static int drain_tx(char *out, int capacity)
{
    int count = 0;
    while ((USART2->CR1 & USART_CR1_TXEIE) != 0 && count + 1 < capacity) {
        USART2->ISR = USART_ISR_TXE_TXFNF;
        USART2_IRQHandler();
        if ((USART2->CR1 & USART_CR1_TXEIE) != 0) {
            out[count++] = (char)USART2->TDR;
        }
    }
    out[count] = '\0';
    return count;
}

int main(void)
{
    char vflog[256];
    char output[64];
    char oversized[300];
    int length;
    uint32_t wire_bps;
    uint32_t headroom_bps;

    UART_Init();

    /* Bounded physical domains: target/VFC <= 5000 rpm; encoder bound from
     * period/delta contract; raw ADC fields are uint16; fault is enum <= 18. */
    /* Формат берётся из src/telemetry_format.h — это ТА ЖЕ строка, что уходит
     * в эфир из main.c (копия в тесте уже один раз отстала). */
    length = snprintf(
        vflog, sizeof(vflog),
        VFLOG_TELEMETRY_FMT,
        (unsigned long)UINT32_MAX,
        -5000L, -42857L, -200L, -5L, 95L, (unsigned long)UINT32_MAX,
        98L, 98L, 98L,
        UINT16_MAX, UINT16_MAX, UINT16_MAX, UINT16_MAX,
        UINT16_MAX, -42857L, 255U, 18, (unsigned long)UINT32_MAX,
        1, 1, -30L, 0);

    check("vflog line fits UART formatter", length > 0 && length < (int)sizeof(vflog));
    check("vflog line has CRLF", length >= 2 &&
          vflog[length - 2] == '\r' && vflog[length - 1] == '\n');
    check("vflog worst-case snapshot (pinned)", strcmp(
        vflog,
        "@VFLOG:t=4294967295:target=-5000:meas=-42857:fe=-200:fslip=-5:vmag=95:theta=4294967295:"
        "du=98:dv=98:dw=98:i1=65535:i2=65535:ires=65535:vbus=65535:"
        "eangle=65535:espeed=-42857:eerr=255:fault=18:drp=4294967295:sd1=1:sd2=1:swing=-30:commit=0\r\n") == 0);

    /* Бюджет @VFLOG пиннингован ПО ФАКТИЧЕСКОЙ строке из telemetry_format.h.
     * Прежний порог 5150 B/s снимался с устаревшей копии (206 байт) и не
     * соответствовал даже строке БЕЗ новых полей: 218 байт -> 5450 B/s.
     * Точный пин длины (как у @FOC: 244/314) заставляет при каждом новом поле
     * осознанно пересчитывать бюджет, а не молча терять строки в эфире. */
    wire_bps = (uint32_t)length * (1000u / CLI_VFLOG_DEFAULT_PERIOD_MS);
    headroom_bps = UART_PAYLOAD_BPS_115200_8N1 - wire_bps;
    check("vflog worst case is 237 bytes (pinned)", length == 237);
    check("vflog default period is 40 ms", CLI_VFLOG_DEFAULT_PERIOD_MS == 40u);
    check("vflog wire load is 5925 B/s (pinned, 25 Hz)", wire_bps == 5925u);
    check("vflog wire load <= 55 % of the 115200 8N1 link",
          wire_bps * 100u <= UART_PAYLOAD_BPS_115200_8N1 * 55u);
    check("vflog headroom >= 5595 B/s", headroom_bps >= 5595u);

    memset(oversized, 'X', sizeof(oversized) - 1u);
    oversized[sizeof(oversized) - 1u] = '\0';
    check("oversized telemetry rejected", UART_TrySendTelemetry("@X:%s\r\n", oversized) < 0);
    check("truncation counted", UART_GetTruncatedCount() == 1u);
    check("truncation included in drops", UART_GetDroppedCount() == 1u);
    check("truncated packet not enqueued", (USART2->CR1 & USART_CR1_TXEIE) == 0u);
    drain_tx(output, (int)sizeof(output));
    check("truncated packet leaves TX empty", output[0] == '\0');
    check("next telemetry accepted", UART_TrySendTelemetry("@OK\r\n") == 0);
    drain_tx(output, (int)sizeof(output));
    check("next telemetry keeps CRLF", strcmp(output, "@OK\r\n") == 0);

    /* ── @FOC baseline: контракт длины пакета ──────────────────────────────
     * Формат берётся из src/telemetry_format.h — это ТА ЖЕ строка, что уходит
     * в эфир из main.c. Копия формата в тесте ничего не доказывала бы: поле,
     * добавленное в прошивку, не ломало бы тест, а пакет на стенде молча
     * переставал бы помещаться в буфер. */
    {
        char foc_idle[UART_TELEMETRY_BUF_SIZE];
        char foc_worst[UART_TELEMETRY_BUF_SIZE];
        char wire[600];
        int idle_len;
        int worst_len;
        uint32_t trunc_before;

        idle_len = snprintf(foc_idle, sizeof(foc_idle), FOC_TELEMETRY_FMT,
                            123456789UL, "M0-R1", 0x1A2B3C4DUL,
                            0L, 0L, 0L, 0L, 0L, 0L, 0L, 201L, 0u, 0L, 0L,
                            0u, 0u, 500u, 500u, 500u, 7u, 0, 0, 0, 0, 1u, 1u);
        check("foc idle line fits checked buffer",
              idle_len > 0 && idle_len < UART_TELEMETRY_BUF_SIZE);
        check("foc idle line ends with CRLF",
              idle_len >= 2 && foc_idle[idle_len - 2] == '\r' &&
              foc_idle[idle_len - 1] == '\n');
        check("foc idle line is 244 bytes (pinned)", idle_len == 244);
        check("foc idle margin over legacy 256-byte buffer is 12 bytes",
              256 - idle_len == 12);

        worst_len = snprintf(foc_worst, sizeof(foc_worst), FOC_TELEMETRY_FMT,
                             4294967295UL, "M0-R1234567890123456789", 0xFFFFFFFFUL,
                             -32768L, -32768L, -32768L, -32768L, -32768L,
                             -32768L, -32768L, 400000L, 9u, -32000L, 6283185L,
                             5u, 1u, 999u, 999u, 999u, 7u, 18, 18, 9, 1, 1u, 1u);
        check("foc worst-case fits checked buffer",
              worst_len > 0 && worst_len < UART_TELEMETRY_BUF_SIZE);
        check("foc worst-case does not fit legacy 256-byte buffer", worst_len > 256);
        check("foc worst-case ends with CRLF",
              worst_len >= 2 && foc_worst[worst_len - 2] == '\r' &&
              foc_worst[worst_len - 1] == '\n');
        check("foc worst-case is 314 bytes (pinned)", worst_len == 314);

        /* Checked sender: строка уходит целиком вместе с CRLF либо не уходит. */
        trunc_before = UART_GetTruncatedCount();
        check("checked sender accepts worst-case line",
              UART_SendTelemetryChecked("%s", foc_worst) == 0);
        drain_tx(wire, (int)sizeof(wire));
        check("checked sender emitted the whole line",
              (int)strlen(wire) == worst_len);
        check("checked sender kept CRLF",
              worst_len >= 2 && wire[worst_len - 2] == '\r' &&
              wire[worst_len - 1] == '\n');
        check("checked sender did not count truncation",
              UART_GetTruncatedCount() == trunc_before);

        {
            char huge[UART_TELEMETRY_BUF_SIZE + 100u];
            memset(huge, 'X', sizeof(huge) - 1u);
            huge[sizeof(huge) - 1u] = '\0';
            check("checked sender rejects oversize",
                  UART_SendTelemetryChecked("@X:%s", huge) < 0);
            check("checked sender counts truncation",
                  UART_GetTruncatedCount() == trunc_before + 1u);
            check("checked sender dropped the packet as a whole",
                  UART_GetDroppedCount() == UART_GetTruncatedCount());
            drain_tx(wire, (int)sizeof(wire));
            check("checked sender emitted nothing on oversize", wire[0] == '\0');
        }
    }

    printf("telemetry_budget_test: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
