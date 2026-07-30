#ifndef PWM_H
#define PWM_H

#include <stdint.h>

void PWM_Init(void);
void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w);
void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w);
void PWM_Enable(void);
void PWM_Disable(void);
void PWM_SetDeadTimeComp(int32_t dt_ticks);

/* Debug tool: прямое управление и чтение регистров */
void PWM_DebugConfig(uint16_t arr, uint16_t duty, uint32_t dt_ns, uint8_t mask);
uint16_t PWM_GetARR(void);
void PWM_SetDeadTime_ns(uint32_t dt_ns);
uint32_t PWM_GetDeadTime_ns(void);
void PWM_GetStatus(uint32_t *cr1, uint32_t *ccer, uint32_t *bdtr, uint32_t *cnt);
void PWM_GetSysInfo(uint32_t *psc, uint32_t *tclk);
void PWM_DumpRegs8(uint32_t *psc, uint32_t *arr, uint32_t *bdtr, uint32_t *cr1, uint32_t *cr2, uint32_t *ccer);
void PWM_DumpRegs(uint32_t *psc, uint32_t *arr, uint32_t *bdtr, uint32_t *cr1, uint32_t *cr2, uint32_t *ccer);

#endif
