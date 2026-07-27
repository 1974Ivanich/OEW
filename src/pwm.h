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
void PWM_DebugConfig(uint16_t arr, uint16_t duty, uint8_t dt, uint8_t mask);
void PWM_GetStatus(uint32_t *cr1, uint32_t *ccer, uint32_t *bdtr, uint32_t *cnt);

#endif
