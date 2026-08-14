/* Заглушки для не тестируемых функций foc.c/vf_control.c (hosted-тесты).
 * Ревью TEST-01: ВСЕ сигнатуры — ТОЧНЫЕ копии production headers (мок-хедеры-
 * пустышки удалены), иначе линкер связывал бы по имени и скрывал API drift.
 * g_motor_params — настоящая структура из src/autotune.h (проверено!). */
#include <stdint.h>
#include "autotune.h"
#include "adc.h"
#include "encoder.h"
#include "pwm.h"
#include "observer.h"
#include "pll.h"
#include "flux_weakening.h"
#include "voltage_manager.h"
#include "vf_start.h"
#include "protect.h"
#include "uart.h"

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

/* Encoder — управляемый: тест задаёт test_enc_rpm */
int32_t test_enc_rpm = 0;
int32_t ENC_GetSpeed_rpm(void) { return test_enc_rpm; }

/* PWM */
void PWM_SetMod1(int16_t mu, int16_t mv, int16_t mw) { (void)mu; (void)mv; (void)mw; }
void PWM_SetMod2(int16_t mu, int16_t mv, int16_t mw) { (void)mu; (void)mv; (void)mw; }
void PWM_Enable(void) { }
void PWM_Disable(void) { }
int32_t PWM_GetDeadTime_ns(void) { return 0; }

/* Observer (BEMF) — точные сигнатуры observer.h */
int32_t BEMF_GetMagnitude(BEMFObserver *obs) { (void)obs; return 0; }
void BEMF_Init(BEMFObserver *obs, int32_t r, int32_t l, int32_t ts, int32_t vdc) { (void)obs; (void)r; (void)l; (void)ts; (void)vdc; }
void BEMF_Update(BEMFObserver *obs, int32_t va, int32_t vb, int32_t ia, int32_t ib) { (void)obs; (void)va; (void)vb; (void)ia; (void)ib; }
uint8_t BEMF_IsValid(const BEMFObserver *obs) { (void)obs; return 0; }

/* Flux weakening — точные сигнатуры flux_weakening.h */
int32_t FW_GetIdAdd(FluxWeakening *fw) { (void)fw; return 0; }
void FW_Init(FluxWeakening *fw, int32_t kp, int32_t ki) { (void)fw; (void)kp; (void)ki; }
void FW_SetBaseSpeedRpm(FluxWeakening *fw, int32_t rpm) { (void)fw; (void)rpm; }
void FW_SetVmaxQ15(FluxWeakening *fw, int32_t v) { (void)fw; (void)v; }
void FW_Update(FluxWeakening *fw, int32_t vd, int32_t vq, int32_t ls, int32_t idb, int32_t spd) { (void)fw; (void)vd; (void)vq; (void)ls; (void)idb; (void)spd; }

/* PLL — точные сигнатуры pll.h */
int32_t PLL_GetSpeed(PLL *pll) { (void)pll; return 0; }
void PLL_Init(PLL *pll, int32_t kp, int32_t ki) { (void)pll; (void)kp; (void)ki; }
void PLL_Update(PLL *pll, int32_t ea, int32_t eb) { (void)pll; (void)ea; (void)eb; }

/* Voltage manager — точные сигнатуры voltage_manager.h */
int32_t VM_GetLimitScale(const VoltageManager *vm) { (void)vm; return 0; }
int32_t VM_GetVmax(const VoltageManager *vm) { (void)vm; return 0; }
void VM_Init(VoltageManager *vm, int32_t vmax, VMPriority prio) { (void)vm; (void)vmax; (void)prio; }
void VM_SetVmax(VoltageManager *vm, int32_t v) { (void)vm; (void)v; }
void VM_Update(VoltageManager *vm, int32_t vd, int32_t vq) { (void)vm; (void)vd; (void)vq; }

/* V/f start (I-f) — точные сигнатуры vf_start.h */
void VF_Init(VFStart *vf, int32_t target, int32_t ramp) { (void)vf; (void)target; (void)ramp; }
int VF_IsComplete(VFStart *vf) { (void)vf; return 0; }
int32_t VF_GetSpeed(VFStart *vf) { (void)vf; return 0; }
int32_t VF_GetTheta(VFStart *vf) { (void)vf; return 0; }
void VF_SetTarget(VFStart *vf, int32_t t) { (void)vf; (void)t; }
void VF_Update(VFStart *vf) { (void)vf; }

/* Autotune / Protect / UART / misc */
void Autotune_Init(void) { }
int PROTECT_IsFault(void) { return 0; }
void UART_SendStr(const char *s) { (void)s; }
int UART_TrySendStr(const char *s) { (void)s; return 1; }
void TRIG_High(void) { }
void TRIG_Low(void) { }
