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

    /* 1. PB3 = TRACESWO (AF0). Ревью SWO-05: явная электрическая политика —
     * high speed (SWO baud) + no-pull. */
    GPIOB->MODER  = (GPIOB->MODER & ~(3U << (3*2)))  | (2U << (3*2));   /* AF */
    GPIOB->AFR[0] = (GPIOB->AFR[0] & ~(0xFU << (3*4))) | (0U << (3*4)); /* AF0 */
    GPIOB->OSPEEDR |= (3U << (3*2));   /* very high speed */
    GPIOB->PUPDR   &= ~(3U << (3*2));  /* no pull */

    /* 2-3. Разблокировка трассировки + ITM */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    ITM->LAR = 0xC5ACCE55UL;

    /* 4. TCR: ITMENA | SYNCENA | DWTENA | SWOENA.
     * Trace-enable = DEMCR.TRCENA (выше). Бита 24 в ITM_TCR НЕ существует
     * (ARMv7-M: ITMENA=0, TSENA=1, SYNCENA=2, DWTENA=3, SWOENA=4, ...,
     * BUSY=23) — прежний (1UL<<24) писал в резерв, безвредно, но вводил
     * в заблуждение (ревью swo.c). Ревью SWO-02: SWOENA (бит 4) включаем
     * явно — SWO-транспорт. TPI по-прежнему настраивает отладчик. */
    ITM->TCR = ITM_TCR_SYNCENA_Msk | ITM_TCR_DWTENA_Msk |
               ITM_TCR_ITMENA_Msk | ITM_TCR_SWOENA_Msk;
    /* 5. Порт 0 разрешён */
    ITM->TER = 1UL;
    /* TPI НЕ трогаем — его настраивает отладчик (OpenOCD tpiu config). */
}

static volatile uint32_t swo_dropped = 0;

/* Ревью SWO-01/04: неблокирующий транспорт — НИКАКОГО busy-wait.
 * Если ITM выключен/TER=0/стимул занят — дроп и счётчик. */
int SWO_TrySendChar(char c) {
    if((ITM->TCR & ITM_TCR_ITMENA_Msk) == 0U ||
       (ITM->TER & 1U) == 0U ||
       (ITM->PORT[0].u32 & 1U) == 0U) {
        swo_dropped++;
        return 0;
    }
    ITM->PORT[0].u8 = (uint8_t)c;
    return 1;
}

/* Отправка строки прекращается на первом дропе (ревью SWO-01: раньше
 * каждый символ получал до 100 000 итераций busy-wait). */
int SWO_TrySendStr(const char *str) {
    int n = 0;
    while(*str) {
        if(!SWO_TrySendChar(*str)) break;
        str++;
        n++;
    }
    return n;
}

void SWO_SendChar(char c) { (void)SWO_TrySendChar(c); }

void SWO_SendStr(const char *str) { (void)SWO_TrySendStr(str); }

/* Ревью SWO-04: явная диагностика отсутствующего probe/перегрузки. */
int SWO_IsReady(void) {
    return ((ITM->TCR & ITM_TCR_ITMENA_Msk) && (ITM->TER & 1U)) ? 1 : 0;
}
uint32_t SWO_GetDropped(void) { return swo_dropped; }

void SWO_Printf(const char *fmt, ...) {
    char buf[160];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    SWO_SendStr(buf);
}
