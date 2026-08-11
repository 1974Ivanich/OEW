#include "swo.h"
#include "stm32g474xx.h"
#include <stdarg.h>
#include <stdio.h>

/* ── SWO/ITM вывод (TRACESWO = PB3, AF0) ──────────────────────────────────
 * Прошивка включает ТОЛЬКО ITM (DEMCR + LAR + TCR + TER). TPI (скорость,
 * формат) настраивает ОТЛАДЧИК при подключении (OpenOCD: `tpiu config ...`).
 * Это важно: если TPI не готов, ITM-стимул не принимается, и busy-wait без
 * таймаута навсегда повесит main(). Поэтому SWO_SendChar имеет таймаут.
 *
 * Чтение на ПК (OpenOCD, ST-Link V2):
 *   openocd -f interface/stlink.cfg -f target/stm32g4x.cfg \
 *           -c "tpiu config internal swo.log uart off 0" \
 *           -c "itm port 0 on" -c "reset halt"
 */

void SWO_Init(void) {
    /* 0. Самодостаточность: тактирование GPIOB (не зависеть от порядка
     * вызовов в main — UART_Init делает то же для GPIOA). */
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOBEN;

    /* 1. PB3 = TRACESWO (AF0) */
    GPIOB->MODER  = (GPIOB->MODER & ~(3U << (3*2)))  | (2U << (3*2));   /* AF */
    GPIOB->AFR[0] = (GPIOB->AFR[0] & ~(0xFU << (3*4))) | (0U << (3*4)); /* AF0 */

    /* 2-3. Разблокировка трассировки + ITM */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    ITM->LAR = 0xC5ACCE55UL;

    /* 4. TCR: ITMENA | SYNCENA | DWTENA.
     * Trace-enable = DEMCR.TRCENA (выше). Бита 24 в ITM_TCR НЕ существует
     * (ARMv7-M: ITMENA=0, TSENA=1, SYNCENA=2, DWTENA=3, SWOENA=4, ...,
     * BUSY=23) — прежний (1UL<<24) писал в резерв, безвредно, но вводил
     * в заблуждение (ревью swo.c). */
    ITM->TCR = ITM_TCR_SYNCENA_Msk | ITM_TCR_DWTENA_Msk | ITM_TCR_ITMENA_Msk;
    /* 5. Порт 0 разрешён */
    ITM->TER = 1UL;
    /* TPI НЕ трогаем — его настраивает отладчик (OpenOCD tpiu config). */
}

void SWO_SendChar(char c) {
    /* Не блокировать main навсегда, если TPI ещё не настроен отладчиком */
    uint32_t timeout = 100000UL;
    while(!(ITM->PORT[0].u32 & 1UL)) {
        if(--timeout == 0UL) return;   /* TPI не готов — пропускаем символ */
    }
    ITM->PORT[0].u8 = (uint8_t)c;
}

void SWO_SendStr(const char *str) {
    while(*str) {
        SWO_SendChar(*str++);
    }
}

void SWO_Printf(const char *fmt, ...) {
    char buf[160];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    SWO_SendStr(buf);
}
