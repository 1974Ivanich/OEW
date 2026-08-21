#include "cli.h"
#include "uart.h"
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
    char legacy[256];
    char output[64];
    char oversized[300];
    int length;
    uint32_t wire_bps;
    uint32_t headroom_bps;

    UART_Init();

    /* Bounded physical domains: target/VFC <= 5000 rpm; encoder bound from
     * period/delta contract; raw ADC fields are uint16; fault is enum <= 18. */
    length = snprintf(
        legacy, sizeof(legacy),
        "@VFLOG:t=%lu:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld:theta=%lu:"
        "du=%ld:dv=%ld:dw=%ld:i1=%u:i2=%u:ires=%u:vbus=%u:"
        "eangle=%u:espeed=%ld:eerr=%u:fault=%d:drp=%lu\r\n",
        (unsigned long)UINT32_MAX,
        -5000L, -42857L, -200L, -5L, 95L, (unsigned long)UINT32_MAX,
        98L, 98L, 98L,
        UINT16_MAX, UINT16_MAX, UINT16_MAX, UINT16_MAX,
        UINT16_MAX, -42857L, 255U, 18, (unsigned long)UINT32_MAX);

    check("legacy vflog fits UART formatter", length > 0 && length < (int)sizeof(legacy));
    check("legacy vflog has CRLF", length >= 2 &&
          legacy[length - 2] == '\r' && legacy[length - 1] == '\n');
    check("legacy vflog snapshot", strcmp(
        legacy,
        "@VFLOG:t=4294967295:target=-5000:meas=-42857:fe=-200:fslip=-5:vmag=95:theta=4294967295:"
        "du=98:dv=98:dw=98:i1=65535:i2=65535:ires=65535:vbus=65535:"
        "eangle=65535:espeed=-42857:eerr=255:fault=18:drp=4294967295\r\n") == 0);

    wire_bps = (uint32_t)length * (1000u / CLI_VFLOG_DEFAULT_PERIOD_MS);
    headroom_bps = UART_PAYLOAD_BPS_115200_8N1 - wire_bps;
    check("compact vflog default is 40 ms", CLI_VFLOG_DEFAULT_PERIOD_MS == 40u);
    check("compact vflog budget <= 5150 B/s", wire_bps <= 5150u);
    check("compact vflog headroom >= 6370 B/s", headroom_bps >= 6370u);

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

    printf("telemetry_budget_test: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
