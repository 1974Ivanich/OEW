#include "hs1_diag.h"

#include <stdio.h>

#include "pwm.h"
#include "protect.h"
#include "stm32g474xx.h"

#if !defined(TIM_SR_BIF) || !defined(TIM_SR_B2IF)
#error "HS1 diagnostics requires full STM32G474 TIM break status definitions."
#endif

#define HS1_PB4_MASK  (1u << 4)
#define HS1_PB5_MASK  (1u << 5)
#define HS1_PB11_MASK (1u << 11)
#define HS1_PB12_MASK (1u << 12)
#define HS1_PB13_MASK (1u << 13)
#define HS1_PD2_MASK  (1u << 2)

typedef struct {
    volatile uint32_t sequence;
    volatile uint32_t tim1_count;
    volatile uint32_t tim8_count;
    volatile uint32_t last_source;
} Hs1DiagCounters;

static Hs1DiagCounters g_hs1_diag;

static void hs1_counter_update(uint32_t source)
{
    /* Single writer at a time in the IRQ model. The sequence is odd while
     * fields change; the foreground reader retries rather than disabling IRQ. */
    g_hs1_diag.sequence++;
    __DMB();
    if (source == 1u) {
        g_hs1_diag.tim1_count++;
    } else {
        g_hs1_diag.tim8_count++;
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
    __DMB();
}

void HS1Diag_OnTim1BreakIrq(void)
{
    hs1_counter_update(1u);
}

void HS1Diag_OnTim8BreakIrq(void)
{
    hs1_counter_update(8u);
}

bool HS1Diag_Read(Hs1DiagSnapshot *out)
{
    uint32_t before;
    uint32_t after;
    uint32_t gpio_b_idr;
    uint32_t gpio_b_odr;
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
        gpio_b_odr = GPIOB->ODR;
        gpio_d_idr = GPIOD->IDR;
        out->interlock = PWM_HardwareInterlockHealthy() ? 1u : 0u;
        out->safety_ok_pb11 = (gpio_b_idr & HS1_PB11_MASK) != 0u ? 1u : 0u;
        out->bkin_pb12_high = (gpio_b_idr & HS1_PB12_MASK) != 0u ? 1u : 0u;
        out->bkin_pd2_high = (gpio_d_idr & HS1_PD2_MASK) != 0u ? 1u : 0u;
        out->arm_req_a_pb4 = (gpio_b_odr & HS1_PB4_MASK) != 0u ? 1u : 0u;
        out->arm_req_b_pb5 = (gpio_b_odr & HS1_PB5_MASK) != 0u ? 1u : 0u;
        out->heartbeat_pb13 = (gpio_b_odr & HS1_PB13_MASK) != 0u ? 1u : 0u;
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
        "@HS1:interlock=%u:ok_pb11=%u:bk_pb12=%u:bk_pd2=%u:"
        "bif_t1=%u:b2if_t1=%u:bif_t8=%u:b2if_t8=%u:"
        "arm_a=%u:arm_b=%u:hb_pb13=%u:"
        "brk_t1=%lu:brk_t8=%lu:last_brk=%lu:fault=%ld:"
        "sr_t1=0x%08lX:bdtr_t1=0x%08lX:af1_t1=0x%08lX:"
        "sr_t8=0x%08lX:bdtr_t8=0x%08lX:af1_t8=0x%08lX:"
        "arr_t1=%lu:ccr_t1=%lu,%lu,%lu:arr_t8=%lu:ccr_t8=%lu,%lu,%lu\r\n",
        (unsigned)s->interlock,
        (unsigned)s->safety_ok_pb11,
        (unsigned)s->bkin_pb12_high,
        (unsigned)s->bkin_pd2_high,
        (unsigned)((s->tim1_sr & TIM_SR_BIF) != 0u),
        (unsigned)((s->tim1_sr & TIM_SR_B2IF) != 0u),
        (unsigned)((s->tim8_sr & TIM_SR_BIF) != 0u),
        (unsigned)((s->tim8_sr & TIM_SR_B2IF) != 0u),
        (unsigned)s->arm_req_a_pb4,
        (unsigned)s->arm_req_b_pb5,
        (unsigned)s->heartbeat_pb13,
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
