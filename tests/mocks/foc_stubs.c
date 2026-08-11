/* Автогенерированные заглушки для не тестируемых функций foc.c.
 * Нужны только чтобы слинковать foc.c с математическим тестом.
 * Никогда не вызываются тестом (gc-sections не работает на mingw-ld). */
#include <stdint.h>

struct MotorParams { int32_t Lm_uH, Rr_mOhm, Tr_rotor_us; };
struct MotorParams g_motor_params = {0, 0, 0};

/* ADC */
int32_t ADC_CalibrateOffsets(void) { return 0; }
int32_t ADC_GetI1_mA(void) { return 0; }
int32_t ADC_GetI2_mA(void) { return 0; }
int32_t ADC_GetIres_mA(void) { return 0; }
int32_t ADC_GetVbus_mV(void) { return 0; }
void ADC_InjectedStart(void) { }
void ADC_InjectedStop(void) { }
void ADC_StartConversion(void) { }

/* Observer (BEMF) */
int32_t BEMF_GetMagnitude(void) { return 0; }
void BEMF_Init(void) { }
void BEMF_Update(void) { }

/* Encoder */
int32_t ENC_GetSpeed_rpm(void) { return 0; }

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

/* PWM */
void PWM_Disable(void) { }
void PWM_Enable(void) { }
int32_t PWM_GetDeadTime_ns(void) { return 0; }
void PWM_SetDuty1(int32_t d) { (void)d; }
void PWM_SetDuty2(int32_t d) { (void)d; }
void PWM_SetDuty3(int32_t d) { (void)d; }
void PWM_SetDuty4(int32_t d) { (void)d; }
void PWM_SetDuty5(int32_t d) { (void)d; }
void PWM_SetDuty6(int32_t d) { (void)d; }

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

/* V/f control */
int VFC_IsRunning(void) { return 0; }
void VFC_Stop(void) { }

/* Protect */
int PROTECT_IsFault(void) { return 0; }

/* Misc */
void TRIG_High(void) { }
void TRIG_Low(void) { }
