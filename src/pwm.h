#ifndef PWM_H
#define PWM_H

#include <stdint.h>

void PWM_Init(void);
/* Q15 signed modulation (управляющий контур: FOC, V/f).
 * mod ∈ [−32768, +32767] → V_phase ≈ mod·Vbus (OEW, CCR обоих инверторов равны).
 * CCR = ARR/2 + mod·ARR/2, mod=0 → 50% → 0 В по фазе. Полное разрешение ARR. */
void PWM_SetMod1(int16_t mu, int16_t mv, int16_t mw);   /* TIM1 (Inv1) */
void PWM_SetMod2(int16_t mu, int16_t mv, int16_t mw);   /* TIM8 (Inv2) */
/* Абсолютный duty % 0..100 (СЕРВИС: autotune, дифференциальная схема OEW).
 * НЕ использовать в FOC/Vf — только PWM_SetMod*. */
void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w);
void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w);
void PWM_Enable(void);
void PWM_Disable(void);
uint32_t PWM_IsEnabled(void);
void PWM_SetDeadTimeComp(int32_t dt_ticks);

/* Debug tool: прямое управление и чтение регистров.
 * Имя подчёркивает ШКАЛУ: mod_pct — OEW modulation index 0..100%,
 * в отличие от PWM_SetMod* (Q15) и PWM_SetDuty* (абсолютный %). */
void PWM_DebugSetModulation(uint16_t arr, uint16_t mod_pct, uint32_t dt_ns, uint8_t mask);
uint16_t PWM_GetARR(void);
/* SERVICE-ONLY: возвращает 0 при успехе, −1 если PWM работает (вызов запрещён). */
int PWM_SetDeadTime_ns(uint32_t dt_ns);
uint32_t PWM_GetDeadTime_ns(void);
void PWM_GetStatus(uint32_t *cr1, uint32_t *ccer, uint32_t *bdtr, uint32_t *cnt);
void PWM_GetSysInfo(uint32_t *psc, uint32_t *tclk);
void PWM_DumpRegs8(uint32_t *psc, uint32_t *arr, uint32_t *bdtr, uint32_t *cr1, uint32_t *cr2, uint32_t *ccer);
void PWM_DumpRegs(uint32_t *psc, uint32_t *arr, uint32_t *bdtr, uint32_t *cr1, uint32_t *cr2, uint32_t *ccer);

#endif
