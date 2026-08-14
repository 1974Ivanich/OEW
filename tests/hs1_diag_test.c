#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "hs1_diag.h"
#include "stm32g474xx.h"

TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;
GPIO_TypeDef host_gpioa;
GPIO_TypeDef host_gpiob;
GPIO_TypeDef host_gpioc;
GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc;
uint32_t SystemCoreClock = 170000000u;

static int host_fault_reason;
static int host_interlock;

int PROTECT_GetFaultReason(void) { return host_fault_reason; }
bool PWM_HardwareInterlockHealthy(void) { return host_interlock != 0; }

int main(void)
{
    Hs1DiagSnapshot s;
    char line[512];

    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpiod, 0, sizeof(host_gpiod));
    HS1Diag_Init();

    host_interlock = 0;
    host_fault_reason = 23;
    host_gpiob.IDR = (1u << 11) | (1u << 12);
    host_gpiod.IDR = (1u << 2);
    host_gpiob.ODR = (1u << 4) | (1u << 13);
    host_tim1.SR = TIM_SR_BIF;
    host_tim8.SR = TIM_SR_B2IF;
    host_tim1.BDTR = 0x00001C55u;
    host_tim8.BDTR = 0x00001C55u;
    host_tim1.AF1 = 0x00000001u;
    host_tim8.AF1 = 0x00000001u;
    host_tim1.ARR = 999u;
    host_tim8.ARR = 999u;
    host_tim1.CCR1 = 100u; host_tim1.CCR2 = 200u; host_tim1.CCR3 = 300u;
    host_tim8.CCR1 = 300u; host_tim8.CCR2 = 200u; host_tim8.CCR3 = 100u;

    HS1Diag_OnTim1BreakIrq(TIM_SR_BIF);
    HS1Diag_OnTim8BreakIrq(TIM_SR_B2IF);
    HS1Diag_OnTim8BreakIrq(TIM_SR_BIF | TIM_SR_B2IF);
    assert(HS1Diag_Read(&s));
    assert(s.break_tim1_count == 1u);
    assert(s.break_tim8_count == 2u);
    assert(s.last_break_source == 8u);
    assert(s.last_tim1_flags == TIM_SR_BIF);
    assert(s.last_tim8_flags == (TIM_SR_BIF | TIM_SR_B2IF));
    assert(s.interlock == 0u);
    assert(s.safety_ok_pb11 == 1u);
    assert(s.bkin_pb12_high == 1u && s.bkin_pd2_high == 1u);
    assert(s.arm_req_a_pb4 == 1u && s.arm_req_b_pb5 == 0u);
    assert(s.heartbeat_pb13 == 1u);
    assert(s.tim1_sr == TIM_SR_BIF && s.tim8_sr == TIM_SR_B2IF);
    assert(s.tim1_af1 == 1u && s.tim8_af1 == 1u);
    assert(s.fault_reason == 23);
    assert(HS1Diag_FormatLine(line, sizeof(line), &s) > 0);
    assert(strstr(line, "@HS1:interlock=0") != 0);
    assert(strstr(line, ":bif_t1=1:") != 0);
    assert(strstr(line, ":b2if_t8=1:") != 0);
    assert(strstr(line, ":l_bif_t1=1:l_b2if_t1=0:l_bif_t8=1:l_b2if_t8=1:") != 0);
    assert(strstr(line, ":brk_t1=1:brk_t8=2:last_brk=8:fault=23:") != 0);
    assert(strstr(line, ":af1_t1=0x00000001:") != 0);

    /* Read-only proof: formatter and snapshot do not clear flags, counters,
     * arm request pins or safety status. */
    assert(host_tim1.SR == TIM_SR_BIF);
    assert(host_tim8.SR == TIM_SR_B2IF);
    assert((host_gpiob.ODR & ((1u << 4) | (1u << 13))) == ((1u << 4) | (1u << 13)));

    puts("hs1_diag_test: PASS");
    return 0;
}
