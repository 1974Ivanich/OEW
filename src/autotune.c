#include "autotune.h"
#include "pwm.h"
#include "adc.h"
#include "uart.h"
#include "foc.h"
#include "protect.h"
#include "stm32g474xx.h"
#include <string.h>

MotorParams g_motor_params;

static void dwt_init(void) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL  |= DWT_CTRL_CYCCNTENA_Msk;
}

static void delay_us(uint32_t us) {
    uint32_t start = DWT->CYCCNT;
    uint32_t ticks = us * (SystemCoreClock / 1000000U);
    while ((DWT->CYCCNT - start) < ticks);
}

static void tim1_enable(void) {
    TIM1->CCER = TIM_CCER_CC1E | TIM_CCER_CC1NE
               | TIM_CCER_CC2E | TIM_CCER_CC2NE
               | TIM_CCER_CC3E | TIM_CCER_CC3NE;
    TIM1->BDTR |= TIM_BDTR_MOE;
    TIM1->CR1  |= TIM_CR1_CEN;
}

static void tim1_disable(void) {
    TIM1->CR1  &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE;
    TIM1->CCER  = 0;
}

void Autotune_Init(void) {
    memset(&g_motor_params, 0, sizeof(MotorParams));
}

/* ══════════════════════════════════════════════════════════════════════════
 *  STATIC IDLE TEST — Rs, Ls, кривая насыщения
 *
 *  Топология: DC импульс Phase A -> Phase B (через 2 обмотки /2).
 *  Измерение: I1 (фаза A) через STEVAL-IPM20B шунт.
 *  Масштабы: U=mV, I=mA, t=us → R=mOhm, L=uH
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_Idle(void) {
    UART_SendStr("@IDLE:START\r\n");

    if (FOC_IsRunning()) FOC_Stop();
    if (PROTECT_IsFault()) {
        UART_SendStr("@IDLE:ERROR:FAULT_CLEAR_FIRST\r\n");
        return -1;
    }

    ADC_StartConversion();
    int32_t vbus = ADC_GetVbus_mV();
    if (vbus < 12000) {
        UART_SendStr("@IDLE:ERROR:VBUS_LOW\r\n");
        return -1;
    }

    NVIC_DisableIRQ(ADC1_2_IRQn);
    PWM_Disable();
    ADC_CalibrateOffsets();
    dwt_init();

    uint16_t arr    = PWM_GetARR();
    uint32_t period = (uint32_t)arr + 1U;
    g_motor_params.curve_count = 0;
    int32_t max_Ls = 0;

    PWM_SetDuty1(0, 0, 0);
    tim1_enable();

    for (uint16_t duty_pct = 1; duty_pct <= 50; duty_pct++) {
        PWM_SetDuty1(duty_pct, 0, 0);
        delay_us(500);

        ADC_StartConversion();
        int32_t I_ss = ADC_GetI1_mA();

        if (I_ss >  AUTOTUNE_MAX_CURRENT_MA ||
            I_ss < -AUTOTUNE_MAX_CURRENT_MA) {
            PWM_SetDuty1(0, 0, 0);
            UART_SendTelemetry("@IDLE:ERROR:OVERCURRENT I=%ld\r\n", I_ss);
            tim1_disable();
            NVIC_EnableIRQ(ADC1_2_IRQn);
            return -2;
        }
        if (I_ss < 0) I_ss = -I_ss;

        /* di/dt для Ls — отключаем preload для быстрого CCR */
        TIM1->CCMR1 &= ~TIM_CCMR1_OC1PE;
        TIM1->CCR1 = 0;
        delay_us(10);
        ADC_StartConversion();
        int32_t I_lo = ADC_GetI1_mA();

        TIM1->CCR1 = (uint16_t)((duty_pct * period) / 100U);
        delay_us(10);
        ADC_StartConversion();
        int32_t I_hi = ADC_GetI1_mA();
        TIM1->CCMR1 |= TIM_CCMR1_OC1PE;

        if (I_lo < 0) I_lo = -I_lo;
        if (I_hi < 0) I_hi = -I_hi;

        int32_t U_applied = (int32_t)(((int64_t)vbus * duty_pct * period) / (100U * arr));
        int32_t di = I_hi - I_lo;
        if (di <= 0) di = 1;
        int32_t Ls_uH = (int32_t)(((int64_t)U_applied * 10) / di) / 2;

        if (g_motor_params.curve_count < AUTOTUNE_MAX_CURVE_POINTS && I_ss > 100) {
            g_motor_params.curve[g_motor_params.curve_count].current_ma   = I_ss;
            g_motor_params.curve[g_motor_params.curve_count].inductance_uh = Ls_uH;
            g_motor_params.curve_count++;
        }
        if (Ls_uH > max_Ls) max_Ls = Ls_uH;
    }

    PWM_SetDuty1(0, 0, 0);
    tim1_disable();
    NVIC_EnableIRQ(ADC1_2_IRQn);

    int32_t Isat_ma = 0;
    if (max_Ls > 0) {
        int32_t threshold = max_Ls * 70 / 100;
        for (uint8_t i = 0; i < g_motor_params.curve_count; i++) {
            if (g_motor_params.curve[i].inductance_uh <= threshold) {
                Isat_ma = g_motor_params.curve[i].current_ma;
                break;
            }
        }
    }

    g_motor_params.Ls_uH   = max_Ls;
    g_motor_params.Isat_ma = Isat_ma;

    if (g_motor_params.curve_count > 2) {
        int32_t I_ref = g_motor_params.curve[2].current_ma;
        uint32_t duty_ref_ticks = (3U * period) / 100U;
        int32_t U_ref = (int32_t)(((int64_t)vbus * duty_ref_ticks) / arr);
        g_motor_params.Rs_mOhm = (int32_t)(((int64_t)U_ref * 1000) / I_ref) / 2;
    }

    UART_SendStr("@IDLE:DONE\r\n");
    Autotune_PrintParams();
    return 0;
}

int8_t Autotune_Irot(void) {
    UART_SendStr("@IROT:START\r\n");
    if (FOC_IsRunning()) FOC_Stop();
    if (PROTECT_IsFault()) {
        UART_SendStr("@IROT:ERROR:FAULT\r\n");
        return -1;
    }
    UART_SendStr("@IROT:DONE (stub)\r\n");
    return 0;
}

int8_t Autotune_Inertia(void) {
    UART_SendStr("@INERTIA:START\r\n");
    if (!FOC_IsRunning()) {
        UART_SendStr("@INERTIA:ERROR:FOC_NOT_RUNNING\r\n");
        return -1;
    }
    UART_SendStr("@INERTIA:DONE (stub)\r\n");
    return 0;
}

void Autotune_PrintParams(void) {
    UART_SendTelemetry(
        "@PARAMS:Rs=%ld:Ls=%ld:Isat=%ld:Rr=%ld:Lm=%ld:Tr=%ld:Ke=%ld:p=%d:J=%ld\r\n",
        g_motor_params.Rs_mOhm, g_motor_params.Ls_uH, g_motor_params.Isat_ma,
        g_motor_params.Rr_mOhm, g_motor_params.Lm_uH, g_motor_params.Tr_us,
        g_motor_params.Ke_mV_rpm, g_motor_params.pole_pairs,
        g_motor_params.J_kg_m2_x1e6);
}

void Autotune_PrintCurve(void) {
    UART_SendStr("@IDLE:CURVE:");
    for (uint8_t i = 0; i < g_motor_params.curve_count; i++) {
        UART_SendTelemetry("I=%ld,L=%ld",
            g_motor_params.curve[i].current_ma,
            g_motor_params.curve[i].inductance_uh);
        if (i < g_motor_params.curve_count - 1)
            UART_SendStr(":");
    }
    UART_SendStr("\r\n");
}
