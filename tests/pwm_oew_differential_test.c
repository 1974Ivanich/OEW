/* VF-2 (hosted, unit): механизм дифференциального OEW-напряжения.
 *
 * Проверяется НЕ формула сама по себе, а тот механизм, которым прошивка её
 * реализует (src/pwm.c, PWM_Init):
 *     TIM1 — PWM mode 1 (OCxM = 6): полюс платы 1 = d*Vbus
 *     TIM8 — PWM mode 2 (OCxM = 7): полюс платы 2 = (1-d)*Vbus  (ТЕ ЖЕ CCR)
 *     TIM8 тактируется от TIM1_TRGO (SMCR: SMS=4, TS=ITR0)
 * => напряжение обмотки: Vw/Vbus = 2d - 1,  d = CCR/(ARR+1).
 *
 * Таблица приёмки (ARR=999, mid=500):
 *     CCR=100 -> d=0.100 -> -80%   CCR=250 -> d=0.250 -> -50%
 *     CCR=500 -> d=0.500 ->   0%   CCR=750 -> d=0.750 -> +50%
 *     CCR=900 -> d=0.900 -> +80%
 *
 * Если режим TIM8 «исправят» на 6 (или добавят комплемент в софт), дифференциал
 * исчезнет — тест обязан это поймать. Это свойство силовой части OEW до сих
 * пор не было покрыто ни одним тестом.
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "pwm.h"
#include "stm32g474xx.h"

/* ── Host-регистры (как в tests/pwm_hs1_test.c) ───────────────────────────── */
TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;
GPIO_TypeDef host_gpioa;
GPIO_TypeDef host_gpiob;
GPIO_TypeDef host_gpioc;
GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc;
uint32_t SystemCoreClock = 170000000u;
volatile uint8_t g_clock_fail;

static bool host_adc_armed;
static bool host_protect_fault;
static bool host_control_admission;

bool ADC_InjectedIsArmed(void) { return host_adc_armed; }
void ADC_InjectedStop(void) { host_adc_armed = false; }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{
    (void)sector; (void)window; (void)valid;
}
void ADC_SetControlAdmission(bool enabled) { host_control_admission = enabled; }
int PROTECT_IsFault(void) { return host_protect_fault ? 1 : 0; }

static void host_reset(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa));
    memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc));
    memset(&host_gpiod, 0, sizeof(host_gpiod));
    memset(&host_rcc, 0, sizeof(host_rcc));
    host_adc_armed = false;
    host_protect_fault = false;
    host_control_admission = false;
    g_clock_fail = 0u;
}

/* ── Шкала: CCR -> дифференциал ───────────────────────────────────────────── */
#define PWM_MID_CCR 500   /* (ARR+1)/2 при ARR=999 */

/* Обратная к mod_to_ccr: даёт mod, при котором CCR1 равен целевому. */
static int32_t mod_for_ccr(int32_t target_ccr)
{
    const int64_t num = (int64_t)(target_ccr - PWM_MID_CCR) * 32768LL;
    return (int32_t)((num >= 0) ? (num + PWM_MID_CCR / 2) / PWM_MID_CCR
                                : (num - PWM_MID_CCR / 2) / PWM_MID_CCR);
}

/* 100 * (2d - 1) в процентах: d = CCR/(ARR+1), (ARR+1) = 1000. */
static int32_t vvb_percent(int32_t ccr)
{
    return (2 * ccr - 1000) * 100 / 1000;
}

int main(void)
{
    const int32_t table[5][2] = {
        { 100, -80 }, { 250, -50 }, { 500, 0 }, { 750, 50 }, { 900, 80 }
    };
    PwmSampleContext valid = { 3u, 1u, true };
    uint32_t oc1m_t1, oc1m_t8;
    unsigned i;

    host_reset();
    PWM_Init();

    /* 1. Механизм: ARR, режимы каналов, подчинение TIM8. */
    assert(PWM_GetARR() == 999u);
    oc1m_t1 = (host_tim1.CCMR1 >> TIM_CCMR1_OC1M_Pos) & 0x7u;
    oc1m_t8 = (host_tim8.CCMR1 >> TIM_CCMR1_OC1M_Pos) & 0x7u;
    assert(oc1m_t1 == 6u);                      /* PWM mode 1 */
    assert(oc1m_t8 == 7u);                      /* PWM mode 2 = инверсия */
    assert(oc1m_t1 != oc1m_t8);
    assert(((host_tim1.CCMR1 >> TIM_CCMR1_OC2M_Pos) & 0x7u) == 6u);
    assert(((host_tim1.CCMR2 >> TIM_CCMR2_OC3M_Pos) & 0x7u) == 6u);
    assert(((host_tim8.CCMR1 >> TIM_CCMR1_OC2M_Pos) & 0x7u) == 7u);
    assert(((host_tim8.CCMR2 >> TIM_CCMR2_OC3M_Pos) & 0x7u) == 7u);
    assert(host_tim8.SMCR == 4u);               /* SMS=reset mode, TS=ITR0 = TIM1_TRGO */
    puts("  режимы: TIM1 OCxM=6, TIM8 OCxM=7, TIM8 slave=TIM1_TRGO  [OK]");

    /* 2. Таблица дифференциала: Vw/Vbus = 2d-1 при одинаковых CCR обоих таймеров. */
    for (i = 0u; i < 5u; i++) {
        const int32_t ccr = table[i][0];
        const int32_t mod = mod_for_ccr(ccr);
        assert(PWM_SetControlVector((int16_t)mod, 0, 0, &valid));
        assert(host_tim1.CCR1 == (uint32_t)ccr);   /* обратная шкала точна */
        assert(host_tim8.CCR1 == (uint32_t)ccr);   /* второй инвертор — тот же CCR */
        assert(vvb_percent(ccr) == table[i][1]);   /* знак и величина дифференциала */
        printf("  CCR=%4d  d=%.3f  Vw/Vbus=%+d%%\n",
               (int)ccr, (double)ccr / 1000.0, (int)table[i][1]);
    }

    /* 3. Разные фазы — но CCR обоих таймеров обязаны совпадать всегда. */
    assert(PWM_SetControlVector(26214, -16384, 0, &valid));
    assert(host_tim1.CCR1 == host_tim8.CCR1);
    assert(host_tim1.CCR2 == host_tim8.CCR2);
    assert(host_tim1.CCR3 == host_tim8.CCR3);
    assert(host_tim1.CCR1 != host_tim1.CCR2 && host_tim1.CCR2 != host_tim1.CCR3);
    assert(vvb_percent((int32_t)host_tim1.CCR1) > 0);    /* d>0.5 -> положительный */
    assert(vvb_percent((int32_t)host_tim1.CCR2) < 0);    /* d<0.5 -> отрицательный */
    assert(vvb_percent((int32_t)host_tim1.CCR3) == 0);   /* d=0.5 -> ноль */
    puts("  равенство CCR обоих таймеров и знак дифференциала  [OK]");

    puts("pwm_oew_differential_test: PASS");
    return 0;
}
