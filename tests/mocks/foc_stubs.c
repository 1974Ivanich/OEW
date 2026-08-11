/* Автогенерированные заглушки для не тестируемых функций foc.c/vf_control.c.
 * Нужны только чтобы слинковать математические тесты.
 * Никогда не вызываются тестом по существу.
 *
 * ВАЖНО: g_motor_params использует НАСТОЯЩУЮ структуру из src/autotune.h —
 * мок-структура с другим layout дала бы мусор в pole_pairs (проверено!). */
#include <stdint.h>
#include "../src/autotune.h"

MotorParams g_motor_params;   /* bss-обнулённая; тест задаёт pole_pairs */

/* ADC */
int32_t ADC_CalibrateOffsets(void) { return 0; }
int32_t ADC_GetI1_mA(void) { return 0; }
int32_t ADC_GetI2_mA(void) { return 0; }
int32_t ADC_GetIres_mA(void) { return 0; }
int32_t ADC_GetVbus_mV(void) { return 0; }
void ADC_InjectedStart(void) { }
void ADC_InjectedStop(void) { }
void ADC_StartConversion(void) { }

/* Encoder — управляемый: тест задаёт test_enc_rpm (VFC_Update сам читает ENC) */
int32_t test_enc_rpm = 0;
int32_t ENC_GetSpeed_rpm(void) { return test_enc_rpm; }

/* PWM (3-аргументные — как в реальном pwm.h: u,v,w duty) */
void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w) { (void)u; (void)v; (void)w; }
void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w) { (void)u; (void)v; (void)w; }
void PWM_Enable(void) { }
void PWM_Disable(void) { }
int32_t PWM_GetDeadTime_ns(void) { return 0; }

/* Observer (BEMF) */
int32_t BEMF_GetMagnitude(void) { return 0; }
void BEMF_Init(void) { }
void BEMF_Update(void) { }

/* Flux weakening */
int32_t FW_GetIdAdd(void) { return 0; }
int32_t FW_GetIqLimit(void) { return 0; }
void FW_Init(void) { }
int FW_IsActive(void) { return 0; }
void FW_SetVmaxQ15(int32_t v) { (void)v; }
void FW_Update(void) { }

/* PLL */
int32_t PLL_GetSpeed(void) { return 0; }
void PLL_Init(void) { }
void PLL_Update(void) { }

/* Voltage manager */
int32_t VM_GetVmax(void) { return 0; }
int32_t VM_GetLimitScale(void) { return 0; }
void VM_SetVmax(int32_t v) { (void)v; }
void VM_Init(void) { }
void VM_Update(void) { }

/* V/f start */
void VF_SetTarget(int32_t t) { (void)t; }
void VF_Start(void) { }
void VF_Stop(void) { }
void VF_Init(void) { }
void VF_Update(void) { }
int32_t VF_GetTheta(void) { return 0; }
int32_t VF_GetSpeed(void) { return 0; }
int VF_IsComplete(void) { return 0; }

/* Autotune */
void Autotune_Init(void) { }

/* Protect */
int PROTECT_IsFault(void) { return 0; }

/* Misc */
void TRIG_High(void) { }
void TRIG_Low(void) { }
