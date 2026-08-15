#include "hs1_diag.h"

#include <stdio.h>

#include "pwm.h"
#include "protect.h"
#include "stm32g474xx.h"

#if !defined(TIM_SR_BIF) || !defined(TIM_SR_B2IF)
#error "HS1 diagnostics requires full STM32G474 TIM break status definitions."
#endif

#define HS1_PB12_MASK (1u << 12) /* SD1 / TIM1_BKIN */
#define HS1_PD2_MASK  (1u << 2)  /* SD2 / TIM8_BKIN */

typedef struct {
    volatile uint32_t sequence;
    volatile uint32_t tim1_count;
    volatile uint32_t tim8_count;
    volatile uint32_t last_source;
    volatile uint32_t last_tim1_flags;
    volatile uint32_t last_tim8_flags;
} Hs1DiagCounters;

static Hs1DiagCounters g_hs1_diag;

static void hs1_counter_update(uint32_t source, uint32_t flags)
{
    /* Single writer at a time in the IRQ model. The sequence is odd while
     * fields change; the foreground reader retries rather than disabling IRQ. */
    g_hs1_diag.sequence++;
    __DMB();
    if (source == 1u) {
        g_hs1_diag.tim1_count++;
        g_hs1_diag.last_tim1_flags = flags;
    } else {
        g_hs1_diag.tim8_count++;
        g_hs1_diag.last_tim8_flags = flags;
    }
    g_hs1_diag.last_source = source;
    __DMB();
    g_hs1_diag.sequence++;
}

void HS1Diag_Init(void)
{
    g_hs1_diag.sequence = 0u;
    g_hs1_diag.tim1_count = 0u;
    g_hs1_diag.tim8_count = 0u;
    g_hs1_diag.last_source = 0u;
    g_hs1_diag.last_tim1_flags = 0u;
    g_hs1_diag.last_tim8_flags = 0u;
    __DMB();
}

void HS1Diag_OnTim1BreakIrq(uint32_t flags)
{
    hs1_counter_update(1u, flags);
}

void HS1Diag_OnTim8BreakIrq(uint32_t flags)
{
    hs1_counter_update(8u, flags);
}

bool HS1Diag_Read(Hs1DiagSnapshot *out)
{
    uint32_t before;
    uint32_t after;
    uint32_t gpio_b_idr;
    
    uint32_t gpio_d_idr;
    uint32_t attempt;

    if (out == 0) {
        return false;
    }

    for (attempt = 0u; attempt < 4u; ++attempt) {
        before = g_hs1_diag.sequence;
        if ((before & 1u) != 0u) {
            continue;
        }
        __DMB();
        out->break_tim1_count = g_hs1_diag.tim1_count;
        out->break_tim8_count = g_hs1_diag.tim8_count;
        out->last_break_source = g_hs1_diag.last_source;
        out->last_tim1_flags = g_hs1_diag.last_tim1_flags;
        out->last_tim8_flags = g_hs1_diag.last_tim8_flags;
        out->tim1_sr = TIM1->SR;
        out->tim8_sr = TIM8->SR;
        out->tim1_bdtr = TIM1->BDTR;
        out->tim8_bdtr = TIM8->BDTR;
        out->tim1_af1 = TIM1->AF1;
        out->tim8_af1 = TIM8->AF1;
        out->tim1_arr = TIM1->ARR;
        out->tim8_arr = TIM8->ARR;
        out->tim1_ccr[0] = TIM1->CCR1;
        out->tim1_ccr[1] = TIM1->CCR2;
        out->tim1_ccr[2] = TIM1->CCR3;
        out->tim8_ccr[0] = TIM8->CCR1;
        out->tim8_ccr[1] = TIM8->CCR2;
        out->tim8_ccr[2] = TIM8->CCR3;
        gpio_b_idr = GPIOB->IDR;
        
        gpio_d_idr = GPIOD->IDR;
        out->interlock = PWM_HardwareInterlockHealthy() ? 1u : 0u;
        out->sd1_pb12_high = (gpio_b_idr & HS1_PB12_MASK) != 0u ? 1u : 0u;
        out->sd2_pd2_high = (gpio_d_idr & HS1_PD2_MASK) != 0u ? 1u : 0u;
        out->fault_reason = (int32_t)PROTECT_GetFaultReason();
        __DMB();
        after = g_hs1_diag.sequence;
        if ((before == after) && ((after & 1u) == 0u)) {
            return true;
        }
    }
    return false;
}

int HS1Diag_FormatLine(char *dst, size_t dst_size, const Hs1DiagSnapshot *s)
{
    if ((dst == 0) || (dst_size == 0u) || (s == 0)) {
        return -1;
    }

    return snprintf(
        dst, dst_size,
        "@HS1:interlock=%u:sd1_pb12=%u:sd2_pd2=%u:"
        "bif_t1=%u:b2if_t1=%u:bif_t8=%u:b2if_t8=%u:"
        "l_bif_t1=%u:l_b2if_t1=%u:l_bif_t8=%u:l_b2if_t8=%u:"
        "brk_t1=%lu:brk_t8=%lu:last_brk=%lu:fault=%ld:"
        "sr_t1=0x%08lX:bdtr_t1=0x%08lX:af1_t1=0x%08lX:"
        "sr_t8=0x%08lX:bdtr_t8=0x%08lX:af1_t8=0x%08lX:"
        "arr_t1=%lu:ccr_t1=%lu,%lu,%lu:arr_t8=%lu:ccr_t8=%lu,%lu,%lu\r\n",
        (unsigned)s->interlock,
        (unsigned)s->sd1_pb12_high,
        (unsigned)s->sd2_pd2_high,
        (unsigned)((s->tim1_sr & TIM_SR_BIF) != 0u),
        (unsigned)((s->tim1_sr & TIM_SR_B2IF) != 0u),
        (unsigned)((s->tim8_sr & TIM_SR_BIF) != 0u),
        (unsigned)((s->tim8_sr & TIM_SR_B2IF) != 0u),
        
        (unsigned)((s->last_tim1_flags & TIM_SR_BIF) != 0u),
        (unsigned)((s->last_tim1_flags & TIM_SR_B2IF) != 0u),
        (unsigned)((s->last_tim8_flags & TIM_SR_BIF) != 0u),
        (unsigned)((s->last_tim8_flags & TIM_SR_B2IF) != 0u),
        (unsigned long)s->break_tim1_count,
        (unsigned long)s->break_tim8_count,
        (unsigned long)s->last_break_source,
        (long)s->fault_reason,
        (unsigned long)s->tim1_sr,
        (unsigned long)s->tim1_bdtr,
        (unsigned long)s->tim1_af1,
        (unsigned long)s->tim8_sr,
        (unsigned long)s->tim8_bdtr,
        (unsigned long)s->tim8_af1,
        (unsigned long)s->tim1_arr,
        (unsigned long)s->tim1_ccr[0],
        (unsigned long)s->tim1_ccr[1],
        (unsigned long)s->tim1_ccr[2],
        (unsigned long)s->tim8_arr,
        (unsigned long)s->tim8_ccr[0],
        (unsigned long)s->tim8_ccr[1],
        (unsigned long)s->tim8_ccr[2]);
}
